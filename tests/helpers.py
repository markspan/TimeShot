"""Test helpers: waiting, LSL inlets, and reading back recorded files."""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from pathlib import Path

import av
import numpy as np
import pylsl

from timeshot.cameras.synthetic import read_sequence_number


def wait_until(condition: Callable[[], bool], timeout: float = 20.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()


def connect_inlet(name: str, timeout: float = 10.0) -> pylsl.StreamInlet:
    """An inlet receiving the stream called ``name``, connected before it returns."""
    found = pylsl.resolve_byprop("name", name, timeout=timeout)
    assert found, f"LSL stream {name} not found"
    inlet = pylsl.StreamInlet(found[0])
    inlet.open_stream(timeout=timeout)
    return inlet


def pull_markers(inlet: pylsl.StreamInlet, timeout: float = 1.0) -> list[tuple[int, float]]:
    """Every marker the inlet has received, as (frame index, LSL timestamp)."""
    markers: list[tuple[int, float]] = []
    while True:
        sample, timestamp = inlet.pull_sample(timeout=timeout)
        if sample is None:
            return markers
        markers.append((int(sample[0]), timestamp))


def file_sequence_numbers(path: Path) -> list[int]:
    """The synthetic camera's sequence number of every frame in a video file, in file order."""
    with av.open(str(path)) as container:
        return [
            read_sequence_number(np.asarray(f.to_ndarray(format="rgb24"))) for f in container.decode(video=0)
        ]


def assert_frames_match_markers(path: Path, markers: list[int]) -> None:
    """What TimeShot guarantees: one marker per frame, numbered from 0 without gaps, and the
    frames in the file in the order the camera delivered them."""
    sequence = file_sequence_numbers(path)
    assert markers == list(range(len(markers)))
    assert len(sequence) == len(markers)
    assert all(b > a for a, b in itertools.pairwise(sequence)), "frames out of order"
