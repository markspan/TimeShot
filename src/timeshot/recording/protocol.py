"""The messages between the GUI (or the command line) and a camera's recorder process.

Commands go to the recorder process; reports come back. All are plain frozen dataclasses,
which cross the process boundary pickled through a pipe. A recording runs through:

1. :class:`OpenStream`: the camera's LSL stream appears, and LabRecorder can connect.
2. :class:`StartRecording`: from the next frame on, each frame gets a marker and goes into the file.
3. :class:`CloseStream`: the recording stops (reported by :class:`Recorded`), the stream goes.

:class:`Shutdown` ends the process, completing a recording that still runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from timeshot.cameras.base import CameraInfo, CameraMode
from timeshot.recording.recorder import RecorderStats, RecordingResult


@dataclass(frozen=True, slots=True)
class RecorderConfig:
    """What a recorder process needs to open its camera.

    Attributes:
        camera: The camera, as its backend found it.
        mode: The mode to open it in.
    """

    camera: CameraInfo
    mode: CameraMode


# ── commands, to the recorder process ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OpenStream:
    """Create the camera's LSL marker stream.

    Attributes:
        stream_name: The stream's name.
        video_file: The name of the video file, for the stream's description.
    """

    stream_name: str
    video_file: str


@dataclass(frozen=True, slots=True)
class StartRecording:
    """Record from the next frame on: write a video file and send a marker per frame.

    Attributes:
        path: The video file to write.
        frame_numbers: Whether to burn each frame's number into its picture.
    """

    path: str
    frame_numbers: bool = False


@dataclass(frozen=True, slots=True)
class CloseStream:
    """Stop recording, if a recording runs, and withdraw the LSL stream."""


@dataclass(frozen=True, slots=True)
class Shutdown:
    """Stop recording, if a recording runs, release the camera and end the process."""


Command = OpenStream | StartRecording | CloseStream | Shutdown


# ── reports, from the recorder process ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Opened:
    """The camera is open.

    Attributes:
        mode: The mode it delivers in: the one asked for, or the nearest it supports.
    """

    mode: CameraMode


@dataclass(frozen=True, slots=True)
class Status:
    """The recorder's state, sent a few times per second.

    Attributes:
        stats: The state.
    """

    stats: RecorderStats


@dataclass(frozen=True, slots=True)
class Recorded:
    """A recording has stopped; its file is complete.

    Attributes:
        result: How it went.
    """

    result: RecordingResult


@dataclass(frozen=True, slots=True)
class Failed:
    """A command, or the camera itself, failed.

    Attributes:
        message: What went wrong.
        fatal: Whether the recorder process has ended because of it.
    """

    message: str
    fatal: bool


@dataclass(frozen=True, slots=True)
class Ended:
    """The recorder process has shut down: its last report."""


Report = Opened | Status | Recorded | Failed | Ended
