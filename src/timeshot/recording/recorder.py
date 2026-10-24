"""Runs one camera: capture, LSL markers, recording and previews.

The timing rules, carried over from TimeShot 1.1:

* Each frame's marker is pushed the moment the camera delivers the frame, and LSL stamps it
  then. TimeShot never passes a timestamp of its own.
* The capture thread does nothing slow: writing happens on the writer's thread and preview
  images are made on a preview thread, so neither can delay the next marker.
* While recording, every marker gets its frame and frames are written in marker order, so
  frame N of the file is always marker N.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
import numpy.typing as npt

from timeshot.cameras.base import CameraError, CameraMode, CameraStream, Frame
from timeshot.frames import decode, decoder_for, to_rgb
from timeshot.recording.markers import MarkerOutlet
from timeshot.recording.preview import preview_size
from timeshot.recording.writer import VideoWriter, WriterResult

log = logging.getLogger(__name__)

# How often previews are made: plenty to aim a camera, and cheap.
PREVIEW_INTERVAL = 0.05
# How long close() waits for the capture thread to see that it should stop.
STOP_TIMEOUT = 3.0
# How much the newest frame counts in the measured frame rate and frame size.
SMOOTHING = 0.05


@dataclass(frozen=True, slots=True)
class RecorderStats:
    """A snapshot of a recorder's state, for status displays.

    Attributes:
        fps: The rate the camera delivers frames at, as measured.
        frame_bytes: The average size of a delivered frame, for estimating disk use.
        seconds_since_frame: How long ago the camera delivered its last frame.
        streaming: Whether the LSL marker stream exists.
        consumers: Whether a recorder (LabRecorder) is connected to the stream.
        recording: Whether frames are being recorded.
        markers_sent: How many markers the current (or last) recording has sent.
        backlog: How many frames wait to be written.
        error: Why the camera stopped delivering, if it did.
        writing: How frames are written while recording, e.g. "H.264, NVIDIA NVENC".
    """

    fps: float
    frame_bytes: float
    seconds_since_frame: float
    streaming: bool
    consumers: bool
    recording: bool
    markers_sent: int
    backlog: int
    error: str | None
    writing: str = ""


@dataclass(frozen=True, slots=True)
class RecordingResult:
    """How one camera's recording went.

    Attributes:
        markers_sent: How many markers were sent.
        file: How the video file ended up; it holds as many frames as markers were sent,
            unless writing failed.
    """

    markers_sent: int
    file: WriterResult

    @property
    def ok(self) -> bool:
        """Whether the file is complete: written without error, a frame for every marker."""
        return self.file.error is None and self.file.frames_written == self.markers_sent


class MovingAverage:
    """An exponential moving average: the newest values count most, older ones fade out."""

    def __init__(self, weight: float) -> None:
        """Starts without a value.

        Args:
            weight: How much each new value counts, between 0 and 1.
        """
        self._weight = weight
        self.value: float | None = None  # None until the first value

    def add(self, value: float) -> None:
        """Takes in a new value; the first one becomes the average as it is."""
        self.value = value if self.value is None else self.value + self._weight * (value - self.value)


class CameraRecorder:
    """Owns an open camera, and runs its capture thread from :meth:`start` to :meth:`close`.

    The LSL stream and the recording are switched on and off from another thread (the
    recorder process's command loop) while the capture thread runs.
    """

    def __init__(self, stream: CameraStream, *, camera_name: str) -> None:
        """Takes over an open camera; capture starts with :meth:`start`.

        Args:
            stream: The open camera. From now on only the capture thread touches it.
            camera_name: The camera's name, for the LSL stream and the log.
        """
        self._stream = stream
        self.camera_name = camera_name
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._capture_loop, name=f"capture {camera_name}", daemon=True)

        # Recording state, changed only under _record_lock: a marker and its frame are handed on
        # together, and the writer is never closed between the two.
        self._record_lock = threading.Lock()
        self._outlet: MarkerOutlet | None = None
        self._writer: VideoWriter | None = None
        self._markers_sent = 0

        # Written by the capture thread only; read elsewhere as snapshots.
        self._latest: tuple[int, Frame] | None = None
        self._frames_seen = 0
        self._interval = MovingAverage(SMOOTHING)
        self._frame_bytes = MovingAverage(SMOOTHING)
        self._last_frame_time = time.perf_counter()
        self._error: str | None = None

    @property
    def mode(self) -> CameraMode:
        """The mode the camera delivers in."""
        return self._stream.mode

    @property
    def fps(self) -> float:
        """The rate the camera delivers frames at: as measured, or its mode's until measured."""
        interval = self._interval.value
        return 1.0 / interval if interval else self._stream.mode.fps

    @property
    def recording(self) -> bool:
        """Whether frames are being recorded."""
        return self._writer is not None

    def start(self) -> None:
        """Starts capturing."""
        self._thread.start()

    # ── LSL stream and recording ─────────────────────────────────────────────

    def open_stream(self, stream_name: str, video_file: str) -> None:
        """Creates the camera's LSL marker stream, so that LabRecorder can find it.

        Args:
            stream_name: The stream's name.
            video_file: The name of the video file the markers will belong to.

        Raises:
            RuntimeError: The stream is open already.
        """
        with self._record_lock:
            if self._outlet is not None:
                raise RuntimeError("We are sorry, but the LSL stream is already open.")
            self._outlet = MarkerOutlet(
                stream_name, camera_name=self.camera_name, video_file=video_file, measured_fps=self.fps
            )

    def close_stream(self) -> None:
        """Withdraws the LSL stream, stopping a recording first."""
        if self.recording:
            self.stop_recording()
        with self._record_lock:
            if self._outlet is not None:
                self._outlet.close()
                self._outlet = None

    def start_recording(self, path: Path, *, frame_numbers: bool = False) -> None:
        """Records from the next frame on: creates the file and starts sending markers.

        Call it from one thread at a time (the recorder process's command loop).

        Args:
            path: The video file to write.
            frame_numbers: Whether to burn each frame's number into its picture.

        Raises:
            RuntimeError: The LSL stream is not open, or a recording runs already.
            ValueError: The file name has no supported extension.
        """
        with self._record_lock:
            if self._outlet is None:
                raise RuntimeError(
                    "We would be most grateful if you would kindly open the LSL stream before recording."
                )
            if self._writer is not None:
                raise RuntimeError("We are sorry, but a recording is already running.")
        # Created outside the lock: opening a file and an encoder takes a while, and the
        # capture thread must not wait for it.
        writer = VideoWriter(path, self._stream.mode, frame_numbers=frame_numbers)
        with self._record_lock:
            self._markers_sent = 0
            self._writer = writer

    def stop_recording(self) -> RecordingResult:
        """Stops sending markers, then completes the file.

        Returns:
            How the recording went.

        Raises:
            RuntimeError: No recording runs.
        """
        with self._record_lock:
            writer, self._writer = self._writer, None
        if writer is None:
            raise RuntimeError("We are sorry, but no recording is running.")
        return RecordingResult(self._markers_sent, writer.close())

    # ── state for displays ───────────────────────────────────────────────────

    def stats(self) -> RecorderStats:
        """A snapshot of the recorder's state."""
        outlet, writer = self._outlet, self._writer
        return RecorderStats(
            fps=self.fps,
            frame_bytes=self._frame_bytes.value or 0.0,
            seconds_since_frame=time.perf_counter() - self._last_frame_time,
            streaming=outlet is not None,
            consumers=outlet is not None and outlet.have_consumers(),
            recording=writer is not None,
            markers_sent=self._markers_sent,
            backlog=writer.backlog if writer is not None else 0,
            error=self._error,
            writing=writer.description if writer is not None else "",
        )

    def latest_frame(self) -> tuple[int, Frame] | None:
        """The newest frame, numbered from 0 since the camera was opened; None before the first."""
        return self._latest

    # ── shutdown ─────────────────────────────────────────────────────────────

    def close(self) -> RecordingResult | None:
        """Stops a recording (completing its file), withdraws the stream and stops capturing.

        If the camera has stopped delivering, its read cannot be interrupted: the capture
        thread is then left behind, and the camera is released when the process ends.

        Returns:
            How the recording that was stopped went; None if none was running.
        """
        result = self.stop_recording() if self.recording else None
        self.close_stream()
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join(STOP_TIMEOUT)
            if self._thread.is_alive():
                log.warning("%s: we are terribly sorry, but the camera would not stop delivering, so we"
                            " leave it, most respectfully, to the end of the process.",
                            self.camera_name)  # fmt: skip
        return result

    # ── capture thread ───────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        """The capture thread: reads frames until :meth:`close`, then releases the camera."""
        raise_thread_priority()
        try:
            while not self._stopping.is_set():
                frame = self._stream.read()  # returns the moment the camera delivers
                self._record(frame)
                self._observe(frame)
        except CameraError as error:
            if not self._stopping.is_set():
                self._error = str(error)
                log.error("%s: %s", self.camera_name, error)
        except Exception as error:
            self._error = (
                f"We are deeply sorry, but capture failed unexpectedly ({type(error).__name__}: {error})."
            )
            log.exception("%s: %s", self.camera_name, self._error)
        finally:
            self._stream.close()  # only this thread ever touches the camera

    def _record(self, frame: Frame) -> None:
        """While recording, sends a frame's marker, then hands the frame to the writer.

        The marker goes first: LSL stamps it as it is pushed, so nothing may come before it.
        Both carry the same index, so frame N of the file is marker N.
        """
        with self._record_lock:
            if self._writer is not None and self._outlet is not None:
                self._outlet.push(self._markers_sent)
                self._writer.add(self._markers_sent, frame)
                self._markers_sent += 1

    def _observe(self, frame: Frame) -> None:
        """Takes a delivered frame into the statistics, and keeps it for the preview."""
        now = time.perf_counter()
        if self._frames_seen:  # an interval needs a frame before it
            self._interval.add(now - self._last_frame_time)
        self._frame_bytes.add(len(frame.data))
        self._last_frame_time = now
        self._latest = (self._frames_seen, frame)
        self._frames_seen += 1


class PreviewMaker:
    """Turns a recorder's newest frame into a small RGB image at the preview rate.

    It runs on a thread of its own, so that decoding and scaling never delay the capture
    thread, and skips frames it has shown already.
    """

    def __init__(self, recorder: CameraRecorder, publish: Callable[[npt.NDArray[np.uint8]], None]) -> None:
        """Prepares the preview thread; it starts with :meth:`start`.

        Args:
            recorder: The recorder whose frames to show.
            publish: Called with each new preview image (see
                :meth:`~timeshot.recording.preview.PreviewSlot.write`).
        """
        self._recorder = recorder
        self._publish = publish
        self._stop = threading.Event()
        self._decoder: av.VideoCodecContext | None = None
        self._thread = threading.Thread(
            target=self._loop, name=f"preview {recorder.camera_name}", daemon=True
        )

    def start(self) -> None:
        """Starts making previews."""
        self._thread.start()

    def stop(self) -> None:
        """Stops making previews, and waits for the thread to end."""
        self._stop.set()
        self._thread.join(STOP_TIMEOUT)

    def render(self, frame: Frame) -> npt.NDArray[np.uint8]:
        """Decodes a frame into a preview image.

        Args:
            frame: The frame.

        Returns:
            The picture as an RGB array, scaled to :func:`~timeshot.recording.preview.preview_size`.

        Raises:
            ValueError: The frame is damaged.
        """
        if self._decoder is None:
            self._decoder = decoder_for(frame.mode)
            self._decoder.thread_type = "AUTO"  # decode large JPEG frames on several cores
        picture = decode(self._decoder, frame)
        return to_rgb(picture, *preview_size(picture.width, picture.height))

    def _loop(self) -> None:
        """The preview thread: renders and publishes each new frame, until :meth:`stop`."""
        shown = -1  # the number of the frame shown last
        while not self._stop.wait(PREVIEW_INTERVAL):
            latest = self._recorder.latest_frame()
            if latest is None or latest[0] == shown:
                continue
            shown, frame = latest
            try:
                image = self.render(frame)
            except (av.FFmpegError, ValueError) as error:
                log.debug("preview of %s skipped a frame: %s", self._recorder.camera_name, error)
                continue
            self._publish(image)


def raise_thread_priority() -> None:
    """Asks the operating system to wake the calling thread promptly when its frame arrives.

    Only on Windows: elsewhere raising a thread's priority needs privileges, and the default
    scheduling serves well.
    """
    if sys.platform != "win32":
        return
    import ctypes  # noqa: PLC0415  (Windows only)
    from ctypes import wintypes  # noqa: PLC0415

    kernel32 = ctypes.WinDLL("kernel32")
    # Declared, so that the thread handle is passed as the 64-bit value it is.
    kernel32.GetCurrentThread.restype = wintypes.HANDLE
    kernel32.SetThreadPriority.argtypes = (wintypes.HANDLE, ctypes.c_int)
    kernel32.SetThreadPriority.restype = wintypes.BOOL
    thread_priority_above_normal = 1
    if not kernel32.SetThreadPriority(kernel32.GetCurrentThread(), thread_priority_above_normal):
        log.debug("could not raise the capture thread's priority")
