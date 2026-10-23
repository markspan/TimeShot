"""TimeShot's command line.

    timeshot                      start the desktop app
    timeshot devices              list the cameras and the modes they offer
    timeshot record ...           record without the desktop app (see timeshot record --help)

``--synthetic N`` (before the command) replaces the cameras by N synthetic test cameras.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

from timeshot import __version__
from timeshot.cameras import SYNTHETIC_ENV, CameraError, CameraMode, default_backend_name, list_cameras
from timeshot.session import DEFAULT_FILE, DEFAULT_NAME, DEFAULT_STREAM, CameraSetup, Phase, Session, check

# How often the record command checks on the recorder processes.
POLL_INTERVAL = 0.1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.synthetic:
        os.environ[SYNTHETIC_ENV] = str(args.synthetic)  # inherited by the recorder processes
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    if args.command is None:
        from timeshot.gui.app import main as gui_main  # noqa: PLC0415  (Qt loads only for the GUI)

        return gui_main()
    try:
        return int(args.run(args))
    except CameraError as error:
        print(f"timeshot: {error}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
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
        metavar="MODE",
        help="e.g. 1920x1080@30 or 640x480@30:rawvideo/yuyv422 (default: the camera's best)",
    )
    record.add_argument("-s", "--session", default="1", help="session name, for {s} in names (default: 1)")
    record.add_argument("-o", "--folder", type=Path, default=Path(), help="folder for the video files")
    record.add_argument("--file", default=DEFAULT_FILE, help=f"file name template (default: {DEFAULT_FILE})")
    record.add_argument(
        "--stream", default=DEFAULT_STREAM, help=f"LSL stream name template (default: {DEFAULT_STREAM})"
    )
    record.add_argument(
        "--name", default=DEFAULT_NAME, help=f"camera name template (default: {DEFAULT_NAME})"
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


def _devices(_args: argparse.Namespace) -> int:
    cameras = list_cameras()
    print(f"Camera interface: {default_backend_name()}")
    if not cameras:
        print("No cameras found.")
        return 1
    for number, camera in enumerate(cameras):
        print(f"\n[{number}] {camera.key}\n    id: {camera.id}")
        for mode in sorted(camera.modes, key=lambda m: (not m.is_jpeg, -m.width * m.height, -m.fps)):
            print(f"    {mode.key():32} {mode}")
    return 0


def _record(args: argparse.Namespace) -> int:
    setups = _setups(args)
    problems = [p for p in check(setups, args.session, args.folder) if not (p.confirm and args.overwrite)]
    for problem in problems:
        where = f"{problem.camera}: " if problem.camera else ""
        print(f"timeshot: {where}{problem.message}", file=sys.stderr)
    if problems:
        return 2

    session = Session(default_backend_name())
    stop_requested = False

    def request_stop(_signal: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    try:
        session.preview(setups)
        _wait(session, lambda: all(s.ready or s.ended for s in session.cameras.values()))
        failed = [f"{s.setup.camera.key}: {s.error}" for s in session.cameras.values() if s.ended]
        if failed:
            print("timeshot: could not open " + "; ".join(failed), file=sys.stderr)
            return 1
        try:
            session.start(
                args.session,
                args.folder,
                wait_for_consumers=not args.no_wait,
                frame_numbers=args.frame_numbers,
            )
        except (RuntimeError, OSError) as error:
            print(f"timeshot: cannot record: {error}", file=sys.stderr)
            return 1
        if session.phase is Phase.WAITING:
            names = ", ".join(s.setup.names(args.session)[1] for s in session.cameras.values())
            print(f"Waiting for LabRecorder to record: {names}  (Ctrl+C cancels)")
        while not stop_requested and session.phase is not Phase.RECORDING:
            _wait(session, lambda: True)
        if session.phase is Phase.RECORDING:
            print("Recording" + (f" for {args.seconds:g} s" if args.seconds else "; Ctrl+C stops") + ".")
            while not stop_requested and (args.seconds is None or session.elapsed < args.seconds):
                _wait(session, lambda: True)
        session.stop()
        _wait(session, lambda: session.phase is Phase.PREVIEW)
    finally:
        session.shutdown()
    return _report(session)


def _setups(args: argparse.Namespace) -> list[CameraSetup]:
    """The cameras to record, as chosen on the command line (all, without --camera)."""
    setups = [
        CameraSetup(c, i, False, None, args.name, args.stream, args.file)
        for i, c in enumerate(list_cameras())
    ]
    mode = CameraMode.parse(args.mode) if args.mode else None
    for setup in _select(setups, args.camera):
        setup.enabled = True
        if mode is not None:
            if mode not in setup.camera.modes:
                raise CameraError(f"{setup.camera.key} does not offer {mode}")
            setup.mode = mode
    return setups


def _report(session: Session) -> int:
    """Prints how each camera's recording went; 0 if all went well."""
    ok = bool(session.results)
    for key, result in session.results.items():
        status = (
            "ok" if result.ok else f"PROBLEM: {result.file.error or 'frame count differs from marker count'}"
        )
        counts = f"{result.file.frames_written} frames, {result.markers_sent} markers"
        print(f"{key}: {counts} -> {result.file.path}  {status}")
        ok &= result.ok
    return 0 if ok else 1


def _select(setups: list[CameraSetup], wanted: list[str] | None) -> list[CameraSetup]:
    if not wanted:
        return setups
    chosen = []
    for item in wanted:
        match = [s for s in setups if item in (str(s.index), s.camera.key, s.camera.name)]
        if not match:
            raise CameraError(f"no camera {item!r} (see 'timeshot devices')")
        chosen.extend(match[:1])
    return chosen


def _wait(session: Session, done: Callable[[], bool]) -> None:
    """Polls the session until ``done()`` holds (at least once)."""
    while True:
        session.poll()
        if done():
            return
        time.sleep(POLL_INTERVAL)
