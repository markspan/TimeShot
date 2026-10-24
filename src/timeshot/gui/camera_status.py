"""What a camera's card says about it: its status line, and the message over its preview.

Kept apart from the widgets, as plain functions of the camera's state, so that tests can
check the wording without a window.
"""

from __future__ import annotations

import html
from typing import NamedTuple

from timeshot.cameras.base import CameraMode
from timeshot.gui.theme import OK, RECORD, WARNING, colored
from timeshot.recording.recorder import RecorderStats
from timeshot.rig import CameraState, Phase

# A camera that delivered nothing for this long is reported as stalled.
STALL_SECONDS = 1.5

STARTING = "Starting camera…"
UNAVAILABLE = "We are so sorry: this camera is unavailable"
STALLED = "We regret that the camera has stopped delivering frames"
NO_LABRECORDER = (
    "we are terribly sorry, but no LabRecorder is connected, so the markers are not being recorded"
)


class CameraStatus(NamedTuple):
    """What a card shows about its camera's state."""

    line: str  # the status line, as rich text, e.g. "30.00 fps · LabRecorder connected"
    overlay: str | None  # the message over the preview; None to show the picture only


def camera_status(state: CameraState | None, phase: Phase, requested: CameraMode | None) -> CameraStatus:
    """Describes an enabled camera's state.

    Args:
        state: The state of its recorder process; None if it has none yet.
        phase: What the rig is doing.
        requested: The mode chosen for the camera, to point out a camera that delivers another.

    Returns:
        The status line and the preview's message.
    """
    if state is None:
        return CameraStatus("", STARTING)
    if state.ended or (state.error and state.stats is None):
        return CameraStatus(
            colored(state.error or "We are sorry, but the camera has stopped.", RECORD), UNAVAILABLE
        )
    if state.mode is None or state.stats is None:
        return CameraStatus("Opening camera…", STARTING)

    stats = state.stats
    parts = [f"{stats.fps:.2f} fps"]
    if requested is not None and state.mode != requested:
        parts.append(html.escape(f"the camera kindly offered {state.mode} instead"))
    parts += _link_status(stats)
    parts += _recording_status(state, phase)
    if stats.error:
        parts.append(colored(stats.error, RECORD))
    overlay = None
    if stats.seconds_since_frame > STALL_SECONDS:
        parts.append(
            colored(f"we regret that no frames have arrived for {stats.seconds_since_frame:.0f} s", RECORD)
        )
        overlay = STALLED
    return CameraStatus(" · ".join(parts), overlay)


def _link_status(stats: RecorderStats) -> list[str]:
    """Whether LabRecorder receives the camera's markers.

    Args:
        stats: The recorder's status.

    Returns:
        A rich-text part for the status line, or none while there is no LSL stream.
    """
    if stats.streaming and stats.consumers:
        return [colored("LabRecorder connected", OK)]
    if stats.recording:
        return [colored(NO_LABRECORDER, RECORD)]
    if stats.streaming:
        return [colored("waiting patiently for LabRecorder", WARNING)]
    return []


def _recording_status(state: CameraState, phase: Phase) -> list[str]:
    """How the running recording goes, or how the last one ended.

    Args:
        state: The camera's state.
        phase: What the rig is doing.

    Returns:
        Rich-text parts for the status line: while recording, the frames so far, how they
        are written, and a disk falling behind; after it, the frames recorded or what went
        wrong; none otherwise.
    """
    stats, result = state.stats, state.result
    if stats is not None and stats.recording:
        parts = [colored(f"● {stats.markers_sent:,} frames", RECORD), html.escape(stats.writing)]
        if stats.backlog > stats.fps / 2:
            parts.append(colored(f"{stats.backlog} frames are politely waiting for the disk", WARNING))
        return parts
    if phase is Phase.PREVIEW and result is not None:
        if result.ok:
            return [colored(f"recorded {result.file.frames_written:,} frames", OK)]
        return [
            colored(result.file.error or "we are terribly sorry, but the frames and markers differ", RECORD)
        ]
    return []
