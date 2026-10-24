"""A camera's LSL marker stream: one sample per recorded frame, holding the frame's index.

The stream is laid out exactly as TimeShot 1.x's, so LabRecorder recordings stay readable by
MovieMerger and by existing analysis scripts: a single string channel "FrameNumber" at an
irregular rate, with ``video_file``, ``camera_name`` and ``measured_fps`` in its description.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

from timeshot import __version__

if TYPE_CHECKING:
    import pylsl


class MarkerOutlet:
    """One camera's marker stream. Visible to LabRecorder from creation until :meth:`close`."""

    def __init__(self, stream_name: str, *, camera_name: str, video_file: str, measured_fps: float) -> None:
        # Imported here, in the recorder process, so that the app's own process never loads
        # liblsl. On Windows, liblsl loads the C++ runtime (msvcp140.dll) from next to
        # python.exe when there is a copy (Anaconda has one); loaded first, that copy is the one
        # Qt gets as well, and a Qt built for a newer runtime then fails to load.
        import pylsl  # noqa: PLC0415

        # Stable across restarts of this stream on this computer, so that LabRecorder can
        # re-find the stream after an interruption (see liblsl's documentation of source_id).
        source_id = f"{socket.gethostname()}-timeshot-{stream_name}"
        info = pylsl.StreamInfo(stream_name, "Markers", 1, pylsl.IRREGULAR_RATE, "string", source_id)
        desc = info.desc()
        channel = desc.append_child("channels").append_child("channel")
        channel.append_child_value("label", "FrameNumber")
        channel.append_child_value("type", "Marker")
        desc.append_child_value("video_file", video_file)
        desc.append_child_value("camera_name", camera_name)
        desc.append_child_value("measured_fps", f"{measured_fps:.2f}")
        desc.append_child_value("recorder", f"TimeShot {__version__}")
        self.stream_name = stream_name
        self._outlet: pylsl.StreamOutlet | None = pylsl.StreamOutlet(info)

    def push(self, frame_index: int) -> None:
        """Sends a frame's index. No timestamp is passed: LSL stamps the sample with its own
        clock as it is pushed, which is why it must be called the moment the frame arrives."""
        if self._outlet is not None:
            self._outlet.push_sample([str(frame_index)])

    def have_consumers(self) -> bool:
        """Whether a recorder (LabRecorder) is connected to the stream."""
        return self._outlet is not None and bool(self._outlet.have_consumers())

    def close(self) -> None:
        """Withdraws the stream from the network."""
        self._outlet = None  # pylsl destroys the outlet when its last reference goes
