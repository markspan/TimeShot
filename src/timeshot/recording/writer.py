"""Writes a camera's recorded frames to a video file, in order, on a thread of its own.

By default a camera's JPEG frames are written exactly as the camera delivered them (no
decoding, no re-encoding: identical quality at almost no CPU cost). Raw frames, and frames
that get their number burned in, are encoded as JPEG first. Either way the file is
Motion-JPEG, in which every frame stands alone, so frame N of the file is exactly the frame
of LSL marker N. The container (AVI or Matroska) follows the file name's extension.
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
from timeshot.frames import ENCODED_PIXEL_FORMAT, decode, decoder_for, encode_jpeg, jpeg_encoder, to_rgb
from timeshot.recording.label import draw_frame_number

log = logging.getLogger(__name__)

CONTAINERS = {".avi": "avi", ".mkv": "matroska"}

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
    :meth:`add`; the file is complete (index and duration written) when close() returns.
    """

    def __init__(self, path: Path, mode: CameraMode, *, frame_numbers: bool = False) -> None:
        container_format = CONTAINERS.get(path.suffix.lower())
        if container_format is None:
            raise ValueError(f"unsupported video file type {path.suffix!r}: use {', '.join(CONTAINERS)}")
        self.path = path
        self.mode = mode
        self.passthrough = mode.is_jpeg and not frame_numbers
        self._frame_numbers = frame_numbers
        self._rate = Fraction(mode.fps).limit_denominator(1001)

        self._container = av.open(str(path), "w", format=container_format)
        self._stream = self._container.add_stream(JPEG, rate=self._rate)
        self._stream.width = mode.width
        self._stream.height = mode.height
        self._stream.pix_fmt = ENCODED_PIXEL_FORMAT
        # For frames that are not written untouched; made when the first one arrives.
        self._decoder: av.VideoCodecContext | None = None
        self._encoder: av.VideoCodecContext | None = None

        self._queue: queue.Queue[tuple[int, Frame] | None] = queue.Queue(
            maxsize=max(1, round(mode.fps * MAX_BACKLOG_SECONDS))
        )
        self._written = 0
        self._last_packet: bytes | None = None
        self._error: str | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._write_loop, name=f"writer {path.name}", daemon=True)
        self._thread.start()

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
            log.warning(
                "%s: writing is %d frames behind; capture waits for it.", self.path.name, self._queue.maxsize
            )
            self._queue.put((index, frame))

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
        try:
            data = frame.data if self.passthrough else self._encode(frame)
        except (av.FFmpegError, ValueError, RuntimeError) as error:
            if self._last_packet is None:
                return self._fail(error)
            # A damaged camera frame: repeat the previous one, so later frames keep their index.
            log.warning(
                "%s: frame %d could not be decoded (%s); repeating the previous frame.",
                self.path.name,
                index,
                error,
            )
            data = self._last_packet
        self._mux(data)

    def _encode(self, frame: Frame) -> bytes:
        if self._decoder is None or self._encoder is None:
            self._decoder = decoder_for(frame.mode)
            self._encoder = jpeg_encoder(frame.mode.width, frame.mode.height)
        picture = decode(self._decoder, frame)
        if self._frame_numbers:
            rgb = to_rgb(picture)
            draw_frame_number(rgb, self._written)
            picture = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        return encode_jpeg(self._encoder, picture)

    def _mux(self, data: bytes) -> None:
        packet = av.Packet(data)
        packet.stream = self._stream
        packet.pts = packet.dts = self._written
        packet.time_base = 1 / self._rate
        self._container.mux(packet)
        self._last_packet = data
        self._written += 1

    def _fail(self, error: Exception) -> None:
        if self._error is None:
            self._error = f"{type(error).__name__}: {error}"
            log.error("%s: writing failed after %d frames: %s", self.path.name, self._written, self._error)
