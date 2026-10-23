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
# Smoothing of the measured frame rate and frame size (weight of the newest frame).
SMOOTHING = 0.05


@dataclass(frozen=True, slots=True)
class RecorderStats:
    """A snapshot of a recorder's state, for status displays."""

    fps: float  # measured delivery rate
    frames_seen: int  # since the camera was opened
    frame_bytes: float  # average size of a delivered frame
    seconds_since_frame: float
    streaming: bool  # the LSL stream exists
    consumers: bool  # a recorder (LabRecorder) is connected to it
    recording: bool
    markers_sent: int  # in the current or last recording
    backlog: int  # frames waiting to be written
    error: str | None


@dataclass(frozen=True, slots=True)
class RecordingResult:
    """How one camera's recording went. ``markers_sent == file.frames_written`` unless it failed."""

    markers_sent: int
    file: WriterResult

    @property
    def ok(self) -> bool:
        return self.file.error is None and self.file.frames_written == self.markers_sent


class CameraRecorder:
    """Owns an open camera and runs its capture thread from :meth:`start` to :meth:`close`."""

    def __init__(self, stream: CameraStream, *, camera_name: str) -> None:
        self._stream = stream
        self.camera_name = camera_name
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._capture_loop, name=f"capture {camera_name}", daemon=True)

        # Recording state, changed only under _record_lock: a marker and its frame are handed
        # on together, and the writer is never closed between the two.
        self._record_lock = threading.Lock()
        self._outlet: MarkerOutlet | None = None
        self._writer: VideoWriter | None = None
        self._markers_sent = 0

        # Written by the capture thread only; read elsewhere as snapshots.
        self._latest: tuple[int, Frame] | None = None
        self._frames_seen = 0
        self._interval = 0.0
        self._frame_bytes = 0.0
        self._last_frame_time = time.perf_counter()
        self._error: str | None = None

    @property
    def mode(self) -> CameraMode:
        return self._stream.mode

    def start(self) -> None:
        self._thread.start()

    # ── LSL stream and recording ─────────────────────────────────────────────

    def open_stream(self, stream_name: str, video_file: str) -> None:
        """Creates the camera's LSL marker stream, so that LabRecorder can find it."""
        with self._record_lock:
            if self._outlet is not None:
                raise RuntimeError("the stream is already open")
            self._outlet = MarkerOutlet(
                stream_name, camera_name=self.camera_name, video_file=video_file, measured_fps=self.fps
            )

    def close_stream(self) -> None:
        """Withdraws the LSL stream (stopping a recording first)."""
        if self.recording:
            self.stop_recording()
        with self._record_lock:
            if self._outlet is not None:
                self._outlet.close()
                self._outlet = None

    def start_recording(self, path: Path, *, frame_numbers: bool = False) -> None:
        """Records from the next frame on: creates the file and starts sending markers.

        Call it from one thread at a time (the recorder process's command loop)."""
        with self._record_lock:
            if self._outlet is None:
                raise RuntimeError("open the stream before recording")
            if self._writer is not None:
                raise RuntimeError("already recording")
        writer = VideoWriter(path, self._stream.mode, frame_numbers=frame_numbers)
        with self._record_lock:
            self._markers_sent = 0
            self._writer = writer

    def stop_recording(self) -> RecordingResult:
        """Stops sending markers, then finishes the file and returns how the recording went."""
        with self._record_lock:
            writer, self._writer = self._writer, None
        if writer is None:
            raise RuntimeError("not recording")
        return RecordingResult(self._markers_sent, writer.close())

    @property
    def recording(self) -> bool:
        return self._writer is not None

    # ── state for displays ───────────────────────────────────────────────────

    @property
    def fps(self) -> float:
        return 1.0 / self._interval if self._interval else self._stream.mode.fps

    def stats(self) -> RecorderStats:
        outlet, writer = self._outlet, self._writer
        return RecorderStats(
            fps=self.fps,
            frames_seen=self._frames_seen,
            frame_bytes=self._frame_bytes,
            seconds_since_frame=time.perf_counter() - self._last_frame_time,
            streaming=outlet is not None,
            consumers=outlet is not None and outlet.have_consumers(),
            recording=writer is not None,
            markers_sent=self._markers_sent,
            backlog=writer.backlog if writer is not None else 0,
            error=self._error,
        )

    def latest_frame(self) -> tuple[int, Frame] | None:
        """The newest frame and its number since the camera was opened."""
        return self._latest

    # ── shutdown ─────────────────────────────────────────────────────────────

    def close(self) -> RecordingResult | None:
        """Stops a recording (finishing its file), withdraws the stream and stops capturing.

        If the camera has stopped delivering, its read cannot be interrupted: the capture
        thread is then left behind, and the camera is released when the process ends.
        """
        result = self.stop_recording() if self.recording else None
        self.close_stream()
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join(STOP_TIMEOUT)
            if self._thread.is_alive():
                log.warning(
                    "%s: the camera did not stop delivering; leaving it to the process exit.",
                    self.camera_name,
                )
        return result

    # ── capture thread ───────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        raise_thread_priority()
        try:
            while not self._stopping.is_set():
                frame = self._stream.read()  # returns the moment the camera delivers
                with self._record_lock:
                    if self._writer is not None and self._outlet is not None:
                        self._outlet.push(self._markers_sent)
                        self._writer.add(self._markers_sent, frame)
                        self._markers_sent += 1
                self._observe(frame)
        except CameraError as error:
            if not self._stopping.is_set():
                self._error = str(error)
                log.error("%s: %s", self.camera_name, error)
        except Exception as error:
            self._error = f"{type(error).__name__}: {error}"
            log.exception("%s: capture failed", self.camera_name)
        finally:
            self._stream.close()  # only this thread ever touches the camera

    def _observe(self, frame: Frame) -> None:
        now = time.perf_counter()
        if self._frames_seen:
            interval = now - self._last_frame_time
            self._interval = (
                interval if not self._interval else self._interval + SMOOTHING * (interval - self._interval)
            )
        size = len(frame.data)
        self._frame_bytes = (
            size if not self._frame_bytes else self._frame_bytes + SMOOTHING * (size - self._frame_bytes)
        )
        self._last_frame_time = now
        self._latest = (self._frames_seen, frame)
        self._frames_seen += 1


class PreviewMaker:
    """Turns a recorder's newest frame into a small RGB image at the preview rate, on a thread
    of its own, and hands it to ``publish``."""

    def __init__(self, recorder: CameraRecorder, publish: Callable[[npt.NDArray[np.uint8]], None]) -> None:
        self._recorder = recorder
        self._publish = publish
        self._stop = threading.Event()
        self._decoder: av.VideoCodecContext | None = None
        self._thread = threading.Thread(
            target=self._loop, name=f"preview {recorder.camera_name}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(STOP_TIMEOUT)

    def _loop(self) -> None:
        shown = -1
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

    def render(self, frame: Frame) -> npt.NDArray[np.uint8]:
        """Decodes a frame into a preview-sized RGB image."""
        if self._decoder is None:
            self._decoder = decoder_for(frame.mode)
            self._decoder.thread_type = "AUTO"
        picture = decode(self._decoder, frame)
        return to_rgb(picture, *preview_size(picture.width, picture.height))


def raise_thread_priority() -> None:
    """Asks the operating system to wake this thread promptly (Windows; elsewhere raising
    priority needs privileges, and the default scheduling serves well)."""
    if sys.platform != "win32":
        return
    import ctypes  # noqa: PLC0415

    kernel32 = ctypes.windll.kernel32
    thread_priority_above_normal = 1
    if not kernel32.SetThreadPriority(kernel32.GetCurrentThread(), thread_priority_above_normal):
        log.debug("could not raise the capture thread's priority")
