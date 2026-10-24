"""Test helpers: camera settings, waiting, LSL inlets, and reading back recorded files."""

from __future__ import annotations

import dataclasses
import itertools
import time
from collections.abc import Callable
from pathlib import Path

import av
import numpy as np
import pylsl

from timeshot.cameras.base import RAW, CameraInfo, CameraMode
from timeshot.cameras.synthetic import read_sequence_number
from timeshot.setup import CameraSetup

# The modes of the cameras camera_setups() makes: one JPEG, one raw.
MODES = (CameraMode(1920, 1080, 30.0), CameraMode(640, 480, 30.0, RAW, "yuyv422"))


def camera_setups(count: int = 2) -> list[CameraSetup]:
    """Settings of distinct cameras ("Camera model 0" and so on), numbered from 0, with the default templates.

    Args:
        count: How many cameras.

    Returns:
        Their settings.
    """
    cameras = [
        CameraInfo(str(i), f"Camera model {i}", MODES, "synthetic", f"Camera model {i}") for i in range(count)
    ]
    return [CameraSetup(camera, number) for number, camera in enumerate(cameras)]


def set_file(setup: CameraSetup, file: str) -> None:
    """Gives a camera another file name template, as typing in its card does.

    Args:
        setup: The camera's settings.
        file: The new template.
    """
    setup.templates = dataclasses.replace(setup.templates, file=file)


def wait_until(condition: Callable[[], bool], timeout: float = 20.0, step: float = 0.05) -> bool:
    """Waits until a condition holds.

    Args:
        condition: Checked every ``step`` seconds.
        timeout: How many seconds to wait at most.
        step: How many seconds to sleep between checks.

    Returns:
        Whether the condition held in time.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()


def connect_inlet(name: str, timeout: float = 10.0) -> pylsl.StreamInlet:
    """An inlet receiving an LSL stream, as LabRecorder would; connected before it returns.

    Args:
        name: The stream's name.
        timeout: How many seconds to wait for the stream to appear, and to connect.

    Returns:
        The connected inlet.
    """
    found = pylsl.resolve_byprop("name", name, timeout=timeout)
    assert found, f"LSL stream {name} not found"
    inlet = pylsl.StreamInlet(found[0])
    inlet.open_stream(timeout=timeout)
    return inlet


def pull_markers(inlet: pylsl.StreamInlet, timeout: float = 1.0) -> list[tuple[int, float]]:
    """Every marker an inlet has received.

    Args:
        inlet: The inlet.
        timeout: How many seconds without a new marker mean that all have arrived.

    Returns:
        (frame index, LSL timestamp) of each marker, in the order received.
    """
    markers: list[tuple[int, float]] = []
    while True:
        sample, timestamp = inlet.pull_sample(timeout=timeout)
        if sample is None:
            return markers
        markers.append((int(sample[0]), timestamp))


def file_sequence_numbers(path: Path) -> list[int]:
    """The synthetic camera's sequence number of every frame in a video file.

    Args:
        path: The video file.

    Returns:
        The numbers, in file order.
    """
    with av.open(str(path)) as container:
        return [
            read_sequence_number(np.asarray(frame.to_ndarray(format="rgb24")))
            for frame in container.decode(video=0)
        ]


def assert_frames_match_markers(path: Path, markers: list[int]) -> None:
    """Asserts what TimeShot guarantees about a recording of a synthetic camera.

    One marker per frame, numbered from 0 without gaps, and the frames in the file in the order
    the camera delivered them.

    Args:
        path: The video file.
        markers: The frame index of each marker received, in the order received.
    """
    sequence = file_sequence_numbers(path)
    assert markers == list(range(len(markers)))
    assert len(sequence) == len(markers)
    assert all(b > a for a, b in itertools.pairwise(sequence)), "frames out of order"
