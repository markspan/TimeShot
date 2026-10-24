#!/usr/bin/env python3
"""Check a compiled TimeShot end to end, without cameras.

Usage (in the build environment, which has pylsl, PyAV and TimeShot itself)::

    python packaging/nuitka/smoke_test.py build/TimeShot/TimeShot.exe

With two synthetic cameras it checks that the executable

1. starts and reports its version;
2. lists the cameras (the camera code and PyAV's FFmpeg load);
3. records without LabRecorder, as camera JPEG and as H.264: the recorder processes, the
   writer and FFmpeg's encoders work;
4. records waiting for LabRecorder, played here by LSL inlets, so the recording starts only
   if liblsl works inside the build (and LSL can reach it through the firewall); then every
   marker received must have its frame in the video file, in order;
5. starts the desktop app and keeps it running (Qt and its plugins load).
"""

from __future__ import annotations

import itertools
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import av
import numpy as np
import pylsl

from timeshot import __version__
from timeshot.cameras.synthetic import read_sequence_number

RECORD_SECONDS = 3
STARTUP_TIMEOUT = 60  # the first start of a compiled app can be slow (antivirus scans)
GUI_ALIVE_SECONDS = 15


def main() -> int:
    """Runs every check on the executable named on the command line.

    Returns:
        0 if every check passed; a failing check ends the script with a message instead.
    """
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"No executable at {executable}")

    version = run(executable, "--version").strip()
    expect(version == f"TimeShot {__version__}", f"version: {version}")

    listing = run(executable, "--synthetic", "2", "devices")
    expect(
        "Synthetic camera 1" in listing and "Synthetic camera 2" in listing, "lists both synthetic cameras"
    )
    check_real_camera_listing(executable)

    with tempfile.TemporaryDirectory(prefix="timeshot-smoke-") as scratch:
        check_recording_alone(executable, Path(scratch))
        check_recording_with_labrecorder(executable, Path(scratch))
    check_gui_starts(executable)
    print("Smoke test passed.")
    return 0


def check_real_camera_listing(executable: Path) -> None:
    """Lists the computer's own cameras through the real camera interface (DirectShow on Windows).

    A build machine usually has no camera and no microphone: that must come out as "No
    cameras found", not as an error.

    Args:
        executable: The compiled TimeShot.exe.
    """
    command = [str(executable), "devices", "--details"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=STARTUP_TIMEOUT, check=False)
    output = result.stdout + result.stderr
    print(output, flush=True)  # what the camera interface reports
    listed = result.returncode in (0, 1) and "Camera interface:" in output and "Traceback" not in output
    expect(listed, "lists this computer's cameras (if any) without an error")


def check_recording_alone(executable: Path, folder: Path) -> None:
    """Records at once, without LabRecorder: camera JPEG into AVI, and H.264 into MP4.

    No LSL connection is needed for these, so they test the recorder processes, the writer
    and FFmpeg's encoders on their own.

    Args:
        executable: The compiled TimeShot.exe.
        folder: A folder for the video files.
    """
    for extension in (".avi", ".mp4"):
        streams = f"smoke-{uuid.uuid4().hex[:8]}-{{i}}"
        arguments = ["--synthetic", "2", "record", "--no-wait", "-t", str(RECORD_SECONDS), "-o", str(folder),
                     "--file", f"alone{{i}}{extension}", "--stream", streams]  # fmt: skip
        print(run(executable, *arguments))
        for i in range(2):
            sequence = sequence_numbers(folder / f"alone{i}{extension}")
            expect(
                len(sequence) > 10, f"camera {i}, {extension}, without LabRecorder: {len(sequence)} frames"
            )
            expect(all(b > a for a, b in itertools.pairwise(sequence)), f"camera {i}: frames in camera order")


def check_recording_with_labrecorder(executable: Path, folder: Path) -> None:
    """Records once LabRecorder (LSL inlets here) records; every marker must have its frame.

    Args:
        executable: The compiled TimeShot.exe.
        folder: A folder for the video files.
    """
    prefix = f"smoke-{uuid.uuid4().hex[:8]}"
    arguments = ["--synthetic", "2", "record", "-t", str(RECORD_SECONDS), "-o", str(folder),
                 "--file", "cam{i}.avi", "--stream", f"{prefix}-{{i}}"]  # fmt: skip
    process = subprocess.Popen(
        [str(executable), *arguments], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        inlets = [connect(f"{prefix}-{i}") for i in range(2)]  # "LabRecorder" starts recording
        if not all(inlets):
            process.kill()
        output, _ = process.communicate(timeout=STARTUP_TIMEOUT + RECORD_SECONDS)
    finally:
        if process.poll() is None:
            process.kill()
    print(output)
    expect(all(inlets), "LSL found both streams (see the output above if not)")
    expect(process.returncode == 0, f"record exited with {process.returncode}")

    for i, inlet in enumerate(inlets):
        assert inlet is not None
        markers = pull_markers(inlet)
        sequence = sequence_numbers(folder / f"cam{i}.avi")
        expect(
            markers == list(range(len(markers))) and len(markers) > 10,
            f"camera {i}: {len(markers)} markers, numbered 0..",
        )
        expect(
            len(sequence) == len(markers), f"camera {i}: {len(sequence)} frames for {len(markers)} markers"
        )
        expect(all(b > a for a, b in itertools.pairwise(sequence)), f"camera {i}: frames in camera order")


def check_gui_starts(executable: Path) -> None:
    """Starts the desktop app with synthetic cameras, and checks that it keeps running.

    Args:
        executable: The compiled TimeShot.exe.
    """
    environment = {**os.environ, "TIMESHOT_SYNTHETIC_CAMERAS": "2"}
    # In a process group of its own (outside Windows), so that end_process_tree() finds them all.
    process = subprocess.Popen([str(executable)], env=environment, start_new_session=sys.platform != "win32")
    try:
        time.sleep(GUI_ALIVE_SECONDS)
        expect(process.poll() is None, f"the desktop app is still running after {GUI_ALIVE_SECONDS} s")
    finally:
        end_process_tree(process)


def end_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Ends the app and its recorder processes together, and waits for it.

    Ended alone, it leaves them running until they notice, still attached to the console of
    the CI step; the step's shell then once failed as it ended ("No process is on the other
    end of the pipe").

    Args:
        process: The desktop app's process.
    """
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def connect(name: str) -> pylsl.StreamInlet | None:
    """An inlet connected to an LSL stream, as LabRecorder would connect.

    Args:
        name: The stream's name.

    Returns:
        The connected inlet, or None if the stream does not appear in time.
    """
    found = pylsl.resolve_byprop("name", name, timeout=STARTUP_TIMEOUT)
    print(f"{'ok' if found else 'FAILED'}: LSL stream {name} appears", flush=True)
    if not found:
        return None
    inlet = pylsl.StreamInlet(found[0])
    inlet.open_stream(timeout=10)
    return inlet


def pull_markers(inlet: pylsl.StreamInlet) -> list[int]:
    """The frame index of every marker an inlet has received, in the order received."""
    markers = []
    while (sample := inlet.pull_sample(timeout=2.0)[0]) is not None:
        markers.append(int(sample[0]))
    return markers


def sequence_numbers(path: Path) -> list[int]:
    """The synthetic camera's sequence number of every frame in a video file, in file order."""
    with av.open(str(path)) as container:
        return [
            read_sequence_number(np.asarray(f.to_ndarray(format="rgb24"))) for f in container.decode(video=0)
        ]


def run(executable: Path, *arguments: str) -> str:
    """Runs the executable, and fails the smoke test if it fails.

    Args:
        executable: The compiled TimeShot.exe.
        *arguments: Its arguments.

    Returns:
        What it printed.
    """
    command = [str(executable), *arguments]
    result = subprocess.run(command, capture_output=True, text=True, timeout=STARTUP_TIMEOUT, check=False)
    if result.returncode:
        what = " ".join([executable.name, *arguments])
        raise SystemExit(f"{what} failed ({result.returncode}):\n{result.stdout}{result.stderr}")
    return result.stdout


def expect(condition: bool, what: str) -> None:
    """Reports a check, and ends the smoke test if it failed.

    Args:
        condition: Whether the check passed.
        what: What was checked.
    """
    print(f"{'ok' if condition else 'FAILED'}: {what}", flush=True)
    if not condition:
        raise SystemExit(f"Smoke test failed: {what}")


if __name__ == "__main__":
    sys.exit(main())
