"""Messages between the GUI (or command line) and a camera's recorder process.

Plain frozen dataclasses: they cross the process boundary pickled through a pipe.
"""

from __future__ import annotations

from dataclasses import dataclass

from timeshot.cameras.base import CameraMode
from timeshot.recording.recorder import RecorderStats, RecordingResult


@dataclass(frozen=True, slots=True)
class RecorderConfig:
    """What a recorder process needs to open its camera."""

    backend: str
    camera_id: str
    camera_name: str
    mode: CameraMode


# ── commands, to the recorder process ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OpenStream:
    """Create the camera's LSL marker stream."""

    stream_name: str
    video_file: str  # file name advertised in the stream description


@dataclass(frozen=True, slots=True)
class StartRecording:
    path: str
    frame_numbers: bool = False


@dataclass(frozen=True, slots=True)
class StopRecording:
    pass


@dataclass(frozen=True, slots=True)
class CloseStream:
    pass


@dataclass(frozen=True, slots=True)
class Shutdown:
    pass


Command = OpenStream | StartRecording | StopRecording | CloseStream | Shutdown


# ── reports, from the recorder process ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Opened:
    """The camera is open, in this mode (possibly the nearest to the one asked for)."""

    mode: CameraMode


@dataclass(frozen=True, slots=True)
class Status:
    stats: RecorderStats


@dataclass(frozen=True, slots=True)
class Recorded:
    """A recording has finished; its file is complete."""

    result: RecordingResult


@dataclass(frozen=True, slots=True)
class Failed:
    """A command, or the camera itself, failed."""

    message: str
    fatal: bool  # the recorder process has ended


@dataclass(frozen=True, slots=True)
class Ended:
    """The recorder process has shut down."""


Report = Opened | Status | Recorded | Failed | Ended
