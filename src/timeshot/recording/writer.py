"""Writes a camera's recorded frames to a video file, in order, on a thread of its own.

The file name's extension chooses the format:

* ``.avi`` and ``.mkv``: Motion-JPEG. A camera's JPEG frames are written exactly as the camera
  delivered them (no decoding, no re-encoding: identical quality at almost no CPU cost); raw
  frames, and frames that get their number burned in, are encoded as JPEG first.
* ``.mp4``: H.264, about ten times smaller, encoded by the computer's hardware encoder if it
  has a working one, else by x264. The MP4 is fragmented, so that a recording cut off by a
  crash stays readable up to its last keyframe.

Either way frame N of the file is the frame of LSL marker N: every frame is written, in order,
and H.264 is encoded without B-frames, so frames are also stored in the order they are shown.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av

from timeshot.cameras.base import JPEG, CameraMode, Frame
from timeshot.frames import (
    ENCODED_PIXEL_FORMAT,
    SOFTWARE_H264,
    H264Encoder,
    available_h264_encoder,
    configure_h264,
    decode,
    decoder_for,
    h264_context,
    jpeg_options,
    to_rgb,
)
from timeshot.recording.label import draw_frame_number

log = logging.getLogger(__name__)

H264 = "h264"


@dataclass(frozen=True, slots=True)
class FileType:
    container: str  # FFmpeg's name for the container format
    codec: str  # JPEG or H264
    options: tuple[tuple[str, str], ...] = ()  # container options


FILE_TYPES = {
    ".avi": FileType("avi", JPEG),
    ".mkv": FileType("matroska", JPEG),
    # Fragmented: an index per fragment instead of one at the end, which a crash would lose.
    ".mp4": FileType("mp4", H264, (("movflags", "frag_keyframe+empty_moov+default_base_moof"),)),
}

# How many seconds of frames may wait to be written before add() makes the capture loop wait.
# That rides out a slow disk flush without losing a frame, while bounding memory; waiting
# delays the next markers, but dropping a frame would shift every later frame off its marker.
MAX_BACKLOG_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class WriterResult:
    """How a recording's file ended up."""

    path: Path
    frames_written: int
    error: str | None = None


class VideoWriter:
    """Writes frames to ``path`` in the order they are added, never dropping one.

    Call :meth:`add` from one thread (the capture loop) and :meth:`close` once, after the last
    :meth:`add`; the file is complete when close() returns.
    """

    def __init__(self, path: Path, mode: CameraMode, *, frame_numbers: bool = False) -> None:
        file_type = FILE_TYPES.get(path.suffix.lower())
        if file_type is None:
            raise ValueError(f"unsupported video file type {path.suffix!r}: use {', '.join(FILE_TYPES)}")
        self.path = path
        self.mode = mode
        self.passthrough = file_type.codec == JPEG and mode.is_jpeg and not frame_numbers
        self._frame_numbers = frame_numbers
        self._rate = Fraction(mode.fps).limit_denominator(1001)
        # Chosen before the file is created, so that an encoder problem leaves no file behind.
        h264 = self._h264_encoder(mode) if file_type.codec == H264 else None

        self._container = av.open(str(path), "w", format=file_type.container, options=dict(file_type.options))
        stream = self._container.add_stream(h264.name if h264 else JPEG, rate=self._rate)
        if not isinstance(stream, av.VideoStream):
            raise TypeError("not a video stream")
        self._stream = stream
        if h264 is not None:
            configure_h264(stream.codec_context, h264, mode.width, mode.height, self._rate)
            self.description = f"H.264, {h264.label}"
        else:
            stream.width, stream.height, stream.pix_fmt = mode.width, mode.height, ENCODED_PIXEL_FORMAT
            if not self.passthrough:
                stream.codec_context.options = jpeg_options()
            self.description = "camera JPEG, untouched" if self.passthrough else "JPEG"
        # For frames that are encoded: made when the first one arrives.
        self._decoder: av.VideoCodecContext | None = None
        self._last_picture: av.VideoFrame | None = None

        self._queue: queue.Queue[tuple[int, Frame] | None] = queue.Queue(
            maxsize=max(1, round(mode.fps * MAX_BACKLOG_SECONDS))
        )
        self._written = 0
        self._error: str | None = None
        self._closed = False
        self._behind = False  # the backlog filled up and has not drained since (warned once)
        self._thread = threading.Thread(target=self._write_loop, name=f"writer {path.name}", daemon=True)
        self._thread.start()

    @staticmethod
    def _h264_encoder(mode: CameraMode) -> H264Encoder:
        """This computer's best H.264 encoder, or x264 if that one refuses this picture size or
        has no session left (consumer GPUs encode a limited number of streams at once)."""
        encoder = available_h264_encoder()
        try:
            h264_context(encoder, mode.width, mode.height, Fraction(mode.fps).limit_denominator(1001))
        except (av.FFmpegError, ValueError, OSError):
            log.warning("%s cannot encode %s; using %s.", encoder.label, mode.size_label, SOFTWARE_H264.label)
            return SOFTWARE_H264
        return encoder

    @property
    def backlog(self) -> int:
        """Frames waiting to be written."""
        return self._queue.qsize()

    @property
    def frames_written(self) -> int:
        return self._written

    def add(self, index: int, frame: Frame) -> None:
        """Queues ``frame`` as frame ``index`` of the file. Waits only while the writer is
        :data:`MAX_BACKLOG_SECONDS` behind."""
        if self._closed:
            raise RuntimeError("the writer is closed")
        try:
            self._queue.put_nowait((index, frame))
        except queue.Full:
            if not self._behind:
                self._behind = True
                log.warning("%s: writing is %d frames behind; capture waits for it (%s).",
                            self.path.name, self._queue.maxsize, self.description)  # fmt: skip
            self._queue.put((index, frame))
        else:
            if self._behind and self._queue.qsize() < self._queue.maxsize // 2:
                self._behind = False
                log.info("%s: writing has caught up.", self.path.name)

    def close(self) -> WriterResult:
        """Writes what is queued, finishes the file and returns how it went. Idempotent."""
        if not self._closed:
            self._closed = True
            self._queue.put(None)
            self._thread.join()
        return WriterResult(self.path, self._written, self._error)

    # ── writer thread ────────────────────────────────────────────────────────

    def _write_loop(self) -> None:
        try:
            while (item := self._queue.get()) is not None:
                if self._error is None:  # after a failure, keep draining so add() never blocks
                    self._write(*item)
            if self._error is None and not self.passthrough:
                for packet in self._stream.encode(None):  # what the encoder still holds
                    self._container.mux(packet)
        except Exception as error:
            self._fail(error)
        finally:
            try:
                self._container.close()
            except Exception as error:
                self._fail(error)

    def _write(self, index: int, frame: Frame) -> None:
        if index != self._written:
            log.error("%s: frame %d arrived as file frame %d.", self.path.name, index, self._written)
        if self.passthrough:
            packet = av.Packet(frame.data)
            packet.stream = self._stream
            packet.pts = packet.dts = self._written
            packet.time_base = 1 / self._rate
            self._container.mux(packet)
        else:
            picture = self._picture(index, frame)
            if picture is None:
                return
            picture.pts = self._written
            picture.time_base = 1 / self._rate
            for packet in self._stream.encode(picture):
                self._container.mux(packet)
        self._written += 1

    def _picture(self, index: int, frame: Frame) -> av.VideoFrame | None:
        """The picture to encode for ``frame``, in the stream's pixel format: the decoded frame
        (numbered if asked), or the previous picture again if this frame is damaged, so that
        later frames keep their index. None (and the writer fails) if there is no picture yet."""
        if self._decoder is None:
            self._decoder = decoder_for(frame.mode)
        try:
            picture = decode(self._decoder, frame)
        except (av.FFmpegError, ValueError) as error:
            if self._last_picture is None:
                self._fail(error)
                return None
            log.warning("%s: frame %d could not be decoded (%s); repeating the previous one.",
                        self.path.name, index, error)  # fmt: skip
            return self._last_picture
        if self._frame_numbers:
            rgb = to_rgb(picture)
            draw_frame_number(rgb, self._written)
            picture = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        self._last_picture = picture.reformat(format=self._stream.codec_context.pix_fmt)
        return self._last_picture

    def _fail(self, error: Exception) -> None:
        if self._error is None:
            self._error = f"{type(error).__name__}: {error}"
            log.error("%s: writing failed after %d frames: %s", self.path.name, self._written, self._error)
