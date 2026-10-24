"""A camera's LSL marker stream: one sample per recorded frame, holding the frame's index.

The stream is laid out exactly as TimeShot 1.x's, so LabRecorder recordings stay readable by
MovieMerger and by existing analysis scripts: a single string channel "FrameNumber" at an
irregular rate, with ``video_file``, ``camera_name`` and ``measured_fps`` in its description.
"""

from __future__ import annotations

import socket

import pylsl

from timeshot import __version__


class MarkerOutlet:
    """One camera's marker stream, visible to LabRecorder from creation until :meth:`close`."""

    def __init__(self, stream_name: str, *, camera_name: str, video_file: str, measured_fps: float) -> None:
        """Creates the stream and announces it on the network.

        Args:
            stream_name: The stream's name, as LabRecorder lists it.
            camera_name: The camera's name, for the stream description.
            video_file: The name of the video file the markers belong to, for the description.
            measured_fps: The frame rate the camera delivers at, for the description.
        """
        # Stable across restarts of this stream on this computer, so that LabRecorder can find
        # the stream again after an interruption (see liblsl's documentation of source_id).
        source_id = f"{socket.gethostname()}-timeshot-{stream_name}"
        info = pylsl.StreamInfo(stream_name, "Markers", 1, pylsl.IRREGULAR_RATE, "string", source_id)
        description = info.desc()
        channel = description.append_child("channels").append_child("channel")
        channel.append_child_value("label", "FrameNumber")
        channel.append_child_value("type", "Marker")
        description.append_child_value("video_file", video_file)
        description.append_child_value("camera_name", camera_name)
        description.append_child_value("measured_fps", f"{measured_fps:.2f}")
        description.append_child_value("recorder", f"TimeShot {__version__}")
        self.stream_name = stream_name
        self._outlet: pylsl.StreamOutlet | None = pylsl.StreamOutlet(info)

    def push(self, frame_index: int) -> None:
        """Sends a frame's index; call it the moment the camera delivers the frame.

        No timestamp is passed: LSL stamps the sample with its own clock as it is pushed,
        which is why the moment of the call matters.

        Args:
            frame_index: The frame's index in the video file.
        """
        if self._outlet is not None:
            self._outlet.push_sample([str(frame_index)])

    def have_consumers(self) -> bool:
        """Whether a recorder (LabRecorder) is connected to the stream."""
        return self._outlet is not None and bool(self._outlet.have_consumers())

    def close(self) -> None:
        """Withdraws the stream from the network."""
        self._outlet = None  # pylsl destroys the outlet when its last reference goes
