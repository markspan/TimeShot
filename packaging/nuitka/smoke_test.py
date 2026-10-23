#!/usr/bin/env python3
"""Check a compiled TimeShot end to end, without cameras.

Usage (in the build environment, which has pylsl, PyAV and TimeShot itself)::

    python packaging/nuitka/smoke_test.py build/TimeShot/TimeShot.exe

With two synthetic cameras it checks that the executable

1. starts and reports its version;
2. lists the cameras (the camera code and PyAV's FFmpeg load);
3. records: it waits for LabRecorder, played here by LSL inlets, so the recording starts
   only if liblsl works inside the build; then every marker received must have its frame
   in the video file, in order (recorder processes, writer and FFmpeg work);
4. starts the desktop app and keeps it running (Qt and its plugins load).
"""

from __future__ import annotations

import itertools
import os
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

    with tempfile.TemporaryDirectory(prefix="timeshot-smoke-") as scratch:
        check_recording(executable, Path(scratch))
    check_gui_starts(executable)
    print("Smoke test passed.")
    return 0


def check_recording(executable: Path, folder: Path) -> None:
    prefix = f"smoke-{uuid.uuid4().hex[:8]}"
    arguments = ["--synthetic", "2", "record", "-t", str(RECORD_SECONDS), "-o", str(folder),
                 "--file", "cam{i}.avi", "--stream", f"{prefix}-{{i}}"]  # fmt: skip
    process = subprocess.Popen(
        [str(executable), *arguments], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        inlets = [connect(f"{prefix}-{i}") for i in range(2)]  # "LabRecorder" starts recording
        output, _ = process.communicate(timeout=STARTUP_TIMEOUT + RECORD_SECONDS)
    finally:
        if process.poll() is None:
            process.kill()
    print(output)
    expect(process.returncode == 0, f"record exited with {process.returncode}")

    for i, inlet in enumerate(inlets):
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
    environment = {**os.environ, "TIMESHOT_SYNTHETIC_CAMERAS": "2"}
    process = subprocess.Popen([str(executable)], env=environment)
    try:
        time.sleep(GUI_ALIVE_SECONDS)
        expect(process.poll() is None, f"the desktop app is still running after {GUI_ALIVE_SECONDS} s")
    finally:
        process.kill()  # its recorder processes end when their connection to it breaks
        process.wait()


def connect(name: str) -> pylsl.StreamInlet:
    found = pylsl.resolve_byprop("name", name, timeout=STARTUP_TIMEOUT)
    expect(bool(found), f"LSL stream {name} appears")
    inlet = pylsl.StreamInlet(found[0])
    inlet.open_stream(timeout=10)
    return inlet


def pull_markers(inlet: pylsl.StreamInlet) -> list[int]:
    markers = []
    while (sample := inlet.pull_sample(timeout=2.0)[0]) is not None:
        markers.append(int(sample[0]))
    return markers


def sequence_numbers(path: Path) -> list[int]:
    """The synthetic camera's number of every frame in the file, in file order."""
    with av.open(str(path)) as container:
        return [
            read_sequence_number(np.asarray(f.to_ndarray(format="rgb24"))) for f in container.decode(video=0)
        ]


def run(executable: Path, *arguments: str) -> str:
    command = [str(executable), *arguments]
    result = subprocess.run(command, capture_output=True, text=True, timeout=STARTUP_TIMEOUT, check=False)
    if result.returncode:
        what = " ".join([executable.name, *arguments])
        raise SystemExit(f"{what} failed ({result.returncode}):\n{result.stdout}{result.stderr}")
    return result.stdout


def expect(condition: bool, what: str) -> None:
    print(f"{'ok' if condition else 'FAILED'}: {what}", flush=True)
    if not condition:
        raise SystemExit(f"Smoke test failed: {what}")


if __name__ == "__main__":
    sys.exit(main())
