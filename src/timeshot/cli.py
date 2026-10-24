"""TimeShot's command line.

    timeshot                      start the desktop app
    timeshot devices              list the cameras and the modes they offer
    timeshot devices --details    ... and what the camera interface reports about them
    timeshot record ...           record without the desktop app (see timeshot record --help)

``--synthetic N`` (before the command) replaces the cameras by N synthetic test cameras.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import platform
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import av

from timeshot import __version__, h264
from timeshot.cameras import SYNTHETIC_ENV, CameraError, CameraMode, default_backend_name, list_cameras
from timeshot.rig import CameraRig, Phase
from timeshot.setup import DEFAULT_TEMPLATES, CameraSetup, NameTemplates, Problem, check

# How often the record command takes in the recorder processes' reports.
POLL_INTERVAL = 0.1

# Reported for a file whose frames and markers differ in number without an error saying why.
_COUNTS_DIFFER = "We are terribly sorry, but the frame count differs from the marker count."

# Exit codes.
OK, FAILED, REFUSED = 0, 1, 2  # REFUSED: the settings have problems, nothing was recorded


def main(argv: list[str] | None = None) -> int:
    """Runs the command line, or the desktop app when there is no command.

    Args:
        argv: The arguments; None for the program's own (``sys.argv[1:]``).

    Returns:
        The exit code: 0 if all went well.
    """
    args = _parser().parse_args(argv)
    if args.synthetic:
        os.environ[SYNTHETIC_ENV] = str(args.synthetic)  # inherited by the recorder processes
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    if args.command is None:
        from timeshot.gui.app import main as run_app  # noqa: PLC0415  (Qt loads for the app only)

        return run_app()
    try:
        return int(args.run(args))
    except CameraError as error:
        print(f"timeshot: {error}", file=sys.stderr)
        return FAILED


def _parser() -> argparse.ArgumentParser:
    """The command line's arguments: the commands, and the options of each."""
    parser = argparse.ArgumentParser(
        prog="timeshot", description="Record several cameras with an LSL marker per frame."
    )
    parser.add_argument("--version", action="version", version=f"TimeShot {__version__}")
    parser.add_argument(
        "--synthetic", type=int, metavar="N", help="use N synthetic test cameras instead of real ones"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log what happens")
    commands = parser.add_subparsers(dest="command", metavar="command")

    devices = commands.add_parser("devices", help="list the cameras and their modes")
    devices.set_defaults(run=_devices)
    devices.add_argument(
        "--details",
        action="store_true",
        help="also show what the camera interface reports, to find out why a camera is missing",
    )

    record = commands.add_parser("record", help="record without the desktop app")
    record.set_defaults(run=_record)
    record.add_argument(
        "-c",
        "--camera",
        action="append",
        metavar="CAMERA",
        help="a camera's number or name from 'timeshot devices' (repeatable; default: all)",
    )
    record.add_argument(
        "-m",
        "--mode",
        type=_mode,
        metavar="MODE",
        help="e.g. 1920x1080@30 or 640x480@30:rawvideo/yuyv422 (default: the camera's best)",
    )
    record.add_argument("-s", "--session", default="1", help="session name, for {s} in names (default: 1)")
    record.add_argument("-o", "--folder", type=Path, default=Path(), help="folder for the video files")
    defaults = DEFAULT_TEMPLATES
    record.add_argument(
        "--file",
        default=defaults.file,
        help=f"file name template: .avi or .mkv for camera JPEG, .mp4 for H.264 (default: {defaults.file})",
    )
    record.add_argument(
        "--stream", default=defaults.stream, help=f"LSL stream name template (default: {defaults.stream})"
    )
    record.add_argument(
        "--name", default=defaults.name, help=f"camera name template (default: {defaults.name})"
    )
    record.add_argument("-t", "--seconds", type=float, help="stop after this long (default: on Ctrl+C)")
    record.add_argument(
        "--no-wait", action="store_true", help="start at once instead of waiting for LabRecorder"
    )
    record.add_argument(
        "--frame-numbers", action="store_true", help="burn each frame's number into the video"
    )
    record.add_argument("-y", "--overwrite", action="store_true", help="overwrite existing video files")
    return parser


def _mode(text: str) -> CameraMode:
    """Reads the ``--mode`` argument (see :meth:`CameraMode.parse`).

    Raises:
        argparse.ArgumentTypeError: It is not a mode; argparse reports why.
    """
    try:
        return CameraMode.parse(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


# ── timeshot devices ─────────────────────────────────────────────────────────


def _devices(args: argparse.Namespace) -> int:
    """Lists the cameras with their modes, and the H.264 encoder ``.mp4`` files would get.

    Args:
        args: The parsed arguments; ``--details`` adds the versions of everything involved
            and what the camera interface reports about each device.

    Returns:
        0 if there are cameras, 1 if there are none.
    """
    if args.details:
        print(
            f"TimeShot {__version__}, PyAV {av.__version__}, FFmpeg {av.ffmpeg_version_info},"
            f" Python {platform.python_version()}, {platform.platform()}"
        )
        logging.getLogger("timeshot.cameras").setLevel(logging.DEBUG)  # what the backends find
    cameras = list_cameras()
    print(f"Camera interface: {default_backend_name()}")
    if not cameras:
        hint = "" if args.details else " Might we suggest --details, to see why?"
        print(f"We are terribly sorry, but we found no cameras.{hint}")
        return FAILED
    for number, camera in enumerate(cameras):
        print(f"\n[{number}] {camera.key}\n    id: {camera.id}")
        for mode in sorted(camera.modes, key=lambda m: (not m.is_jpeg, -m.width * m.height, -m.fps)):
            print(f"    {mode.key():32} {mode}")
    encoder = h264.available_encoder()
    print(f"\nH.264 encoder for .mp4 files: {encoder.label} ({encoder.name})")
    return OK


# ── timeshot record ──────────────────────────────────────────────────────────


def _record(args: argparse.Namespace) -> int:
    """Records the chosen cameras, then reports how each recording went.

    Args:
        args: The parsed arguments (see ``timeshot record --help``).

    Returns:
        0 if every camera recorded every frame with its marker; 1 if something failed or the
        recording never started; 2 if the settings have problems (nothing was recorded).
    """
    setups = _chosen_setups(args)
    if _report_problems(check(setups, args.session, args.folder), overwrite=args.overwrite):
        return REFUSED
    rig = CameraRig()
    stop = threading.Event()
    with _ctrl_c_sets(stop):
        try:
            if not (_open_cameras(rig, setups, stop) and _start(rig, args)):
                return FAILED
            _record_until_stopped(rig, args.seconds, stop)
        finally:
            rig.shutdown()  # also completes the files if something went wrong
    return _report_results(rig)


def _chosen_setups(args: argparse.Namespace) -> list[CameraSetup]:
    """The cameras' settings, as the arguments choose them.

    Args:
        args: The parsed arguments: the cameras (all, without ``--camera``), their mode and
            the name templates.

    Returns:
        Every camera's settings, numbered as ``timeshot devices`` lists them, the chosen
        ones enabled.

    Raises:
        CameraError: A chosen camera does not exist or does not offer the chosen mode.
    """
    templates = NameTemplates(args.name, args.stream, args.file)
    setups = [
        CameraSetup(camera, number, enabled=False, templates=templates)
        for number, camera in enumerate(list_cameras())
    ]
    for setup in _select(setups, args.camera):
        setup.enabled = True
        if args.mode is not None:
            if args.mode not in setup.camera.modes:
                raise CameraError(
                    f"We are terribly sorry, but {setup.camera.key} does not offer {args.mode}."
                )
            setup.mode = args.mode
    return setups


def _select(setups: list[CameraSetup], wanted: list[str] | None) -> list[CameraSetup]:
    """The cameras ``--camera`` asks for.

    Args:
        setups: Every camera's settings.
        wanted: Each camera's number, key or name; None for all cameras.

    Returns:
        The chosen cameras' settings.

    Raises:
        CameraError: No camera has one of the numbers or names.
    """
    if not wanted:
        return setups
    chosen = []
    for item in wanted:
        match = next((s for s in setups if item in (str(s.number), s.camera.key, s.camera.name)), None)
        if match is None:
            raise CameraError(
                f"We are so sorry, but we know of no camera {item!r}."
                " Might we suggest 'timeshot devices', to see them all?"
            )
        chosen.append(match)
    return chosen


def _report_problems(problems: list[Problem], *, overwrite: bool) -> bool:
    """Prints what stops the recording.

    Args:
        problems: What :func:`~timeshot.setup.check` found.
        overwrite: Whether ``--overwrite`` confirms overwriting existing files.

    Returns:
        Whether there are problems that stop the recording.
    """
    blocking = [problem for problem in problems if not (problem.confirm and overwrite)]
    for problem in blocking:
        where = f"{problem.camera}: " if problem.camera else ""
        print(f"timeshot: {where}{problem.message}", file=sys.stderr)
    if any(problem.confirm for problem in blocking):  # the permission asked for is --overwrite
        print("timeshot: Should you wish to grant that permission, kindly add --overwrite.", file=sys.stderr)
    return bool(blocking)


@contextlib.contextmanager
def _ctrl_c_sets(event: threading.Event) -> Iterator[None]:
    """Makes Ctrl+C set ``event``, instead of interrupting the program, within the block.

    The recording then stops in an orderly way, completing its files.

    Args:
        event: The event Ctrl+C sets.
    """
    previous = signal.signal(signal.SIGINT, lambda _signal, _frame: event.set())
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _open_cameras(rig: CameraRig, setups: list[CameraSetup], stop: threading.Event) -> bool:
    """Opens the enabled cameras and waits until each is open or has failed.

    Args:
        rig: The rig.
        setups: Every camera's settings.
        stop: Set by Ctrl+C, which stops the waiting.

    Returns:
        Whether every camera opened (the failures are printed).
    """
    rig.preview(setups)
    _poll_until(rig, lambda: stop.is_set() or all(s.ready or s.ended for s in rig.cameras.values()))
    failed = [f"{s.setup.camera.key}: {s.error}" for s in rig.cameras.values() if s.ended]
    if failed:
        print(
            "timeshot: We are terribly sorry, but not every camera would open. " + "; ".join(failed),
            file=sys.stderr,
        )
    return not failed and not stop.is_set()


def _start(rig: CameraRig, args: argparse.Namespace) -> bool:
    """Opens the cameras' LSL streams, so that LabRecorder can connect to them.

    Args:
        rig: The rig, with every camera open.
        args: The parsed arguments: session, folder and recording options.

    Returns:
        Whether that worked (the failure is printed).
    """
    try:
        rig.start(
            args.session, args.folder, wait_for_consumers=not args.no_wait, frame_numbers=args.frame_numbers
        )
    except (RuntimeError, OSError) as error:
        print(f"timeshot: We are terribly sorry, but the recording could not start. {error}", file=sys.stderr)
        return False
    if rig.phase is Phase.WAITING:
        streams = ", ".join(state.setup.names(args.session).stream for state in rig.cameras.values())
        print(f"Waiting patiently for LabRecorder. Would you kindly record: {streams}  (Ctrl+C cancels)")
    return True


def _record_until_stopped(rig: CameraRig, seconds: float | None, stop: threading.Event) -> None:
    """Waits for the recording to start, records, and stops it, waiting until the files are complete.

    Args:
        rig: The rig, waiting for LabRecorder or recording already.
        seconds: How long to record; None until Ctrl+C.
        stop: Set by Ctrl+C, which cancels the waiting or stops the recording.
    """
    _poll_until(rig, lambda: stop.is_set() or rig.phase is Phase.RECORDING)
    if rig.phase is Phase.RECORDING:
        print("Recording" + (f" for {seconds:g} s" if seconds else "; Ctrl+C stops") + ".")
        _poll_until(rig, lambda: stop.is_set() or (seconds is not None and rig.elapsed >= seconds))
    rig.stop()
    _poll_until(rig, lambda: rig.phase is Phase.PREVIEW)


def _poll_until(rig: CameraRig, condition: Callable[[], bool]) -> None:
    """Takes in the recorder processes' reports, every :data:`POLL_INTERVAL`, until ``condition()`` holds.

    Args:
        rig: The rig to poll.
        condition: Checked after each poll.
    """
    rig.poll()
    while not condition():
        time.sleep(POLL_INTERVAL)
        rig.poll()


def _report_results(rig: CameraRig) -> int:
    """Prints how each camera's recording went, and what went wrong.

    Args:
        rig: The rig, shut down after the recording.

    Returns:
        0 if every camera recorded every frame with its marker, else 1.
    """
    for key, error in rig.errors.items():
        print(f"{key}: {error}", file=sys.stderr)
    for key, result in rig.results.items():
        status = "ok" if result.ok else result.file.error or _COUNTS_DIFFER
        counts = f"{result.file.frames_written} frames, {result.markers_sent} markers"
        print(f"{key}: {counts} -> {result.file.path}  {status}")
    all_ok = bool(rig.results) and not rig.errors and all(result.ok for result in rig.results.values())
    return OK if all_ok else FAILED
