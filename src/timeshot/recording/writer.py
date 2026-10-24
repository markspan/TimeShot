"""Writes a camera's recorded frames to a video file, in order, on a thread of its own.

The file name's extension chooses the format (see :data:`FILE_TYPES`):

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
from dataclasses import dataclass, field
from pathlib import Path

import av

from timeshot import h264
from timeshot.cameras.base import JPEG, CameraMode, Frame
from timeshot.frames import ENCODED_PIXEL_FORMAT, decode, decoder_for, jpeg_options, to_rgb
from timeshot.recording.label import draw_frame_number

log = logging.getLogger(__name__)

H264 = "h264"

# How many seconds of frames may wait to be written before add() makes the capture loop wait.
# That rides out a slow disk flush without losing a frame, while bounding memory; waiting
# delays the next markers, but dropping a frame would shift every later frame off its marker.
MAX_BACKLOG_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class FileType:
    """A video file format.

    Attributes:
        label: The format as offered to people, e.g. "Camera JPEG (.avi)".
        container: FFmpeg's name of the container format.
        codec: How frames are stored: :data:`~timeshot.cameras.base.JPEG` or :data:`H264`.
        options: The container's options.
    """

    label: str
    container: str
    codec: str
    options: dict[str, str] = field(default_factory=dict)


# The formats TimeShot writes, by file name extension.
FILE_TYPES = {
    ".avi": FileType("Camera JPEG (.avi)", "avi", JPEG),
    ".mkv": FileType("Camera JPEG (.mkv)", "matroska", JPEG),
    # Fragmented: an index per fragment instead of one at the end, which a crash would lose.
    ".mp4": FileType("H.264 (.mp4)", "mp4", H264, {"movflags": "frag_keyframe+empty_moov+default_base_moof"}),
}


@dataclass(frozen=True, slots=True)
class WriterResult:
    """How a recording's file ended up.

    Attributes:
        path: The file.
        frames_written: How many frames it holds.
        error: Why writing stopped early, or None if every frame was written.
    """

    path: Path
    frames_written: int
    error: str | None = None


class VideoWriter:
    """Writes frames to a video file in the order they are added, never dropping one.

    Call :meth:`add` from one thread (the capture loop) and :meth:`close` once, after the last
    :meth:`add`; the file is complete when :meth:`close` returns.
    """

    def __init__(self, path: Path, mode: CameraMode, *, frame_numbers: bool = False) -> None:
        """Creates the file and starts the writer thread.

        Args:
            path: The file to write; its extension chooses the format (see :data:`FILE_TYPES`).
            mode: The mode the camera delivers its frames in.
            frame_numbers: Whether to burn each frame's number into its picture.

        Raises:
            ValueError: The extension is not one of :data:`FILE_TYPES`.
            av.FFmpegError: The file could not be created.
        """
        file_type = FILE_TYPES.get(path.suffix.lower())
        if file_type is None:
            raise ValueError(
                f"We are so sorry, but we cannot write {path.suffix!r} files."
                f" Would you be so kind as to use {', '.join(FILE_TYPES)}?"
            )
        self.path = path
        self.mode = mode
        # Camera JPEG into a JPEG file, without numbers: the frames go into the file untouched.
        self.passthrough = file_type.codec == JPEG and mode.is_jpeg and not frame_numbers
        self._frame_numbers = frame_numbers
        self._time_base = 1 / mode.rate
        # Chosen before the file is created, so that an encoder problem leaves no file behind.
        encoder = h264.encoder_for(mode) if file_type.codec == H264 else None

        self._container = av.open(str(path), "w", format=file_type.container, options=dict(file_type.options))
        stream = self._container.add_stream(encoder.name if encoder else JPEG, rate=mode.rate)
        if not isinstance(stream, av.VideoStream):
            raise TypeError("Our apologies: FFmpeg gave us something other than a video stream.")
        self._stream = stream
        if encoder is not None:
            h264.configure(stream.codec_context, encoder, mode)
            self.description = f"H.264, {encoder.label}"  # how frames are written, for the status line
        else:
            stream.width, stream.height, stream.pix_fmt = mode.width, mode.height, ENCODED_PIXEL_FORMAT
            if not self.passthrough:
                stream.codec_context.options = jpeg_options()
            self.description = "camera JPEG, untouched" if self.passthrough else "JPEG"

        # For frames that are encoded: the decoder is made when the first frame arrives.
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

    @property
    def backlog(self) -> int:
        """How many frames wait to be written."""
        return self._queue.qsize()

    @property
    def frames_written(self) -> int:
        """How many frames are in the file so far."""
        return self._written

    def add(self, index: int, frame: Frame) -> None:
        """Queues a frame to be written.

        Returns at once, unless the writer is :data:`MAX_BACKLOG_SECONDS` behind: it then
        waits for room, rather than drop a frame.

        Args:
            index: The frame's index in the file, which its marker carries.
            frame: The frame, as the camera delivered it.

        Raises:
            RuntimeError: The writer is closed.
        """
        if self._closed:
            raise RuntimeError("We are sorry, but the writer has already been closed.")
        try:
            self._queue.put_nowait((index, frame))
        except queue.Full:
            if not self._behind:
                self._behind = True
                log.warning("%s: we are terribly sorry, but writing has fallen %d frames behind, so capture"
                            " must politely wait for it (%s).",
                            self.path.name, self._queue.maxsize, self.description)  # fmt: skip
            self._queue.put((index, frame))
        else:
            if self._behind and self._queue.qsize() < self._queue.maxsize // 2:
                self._behind = False
                log.info("%s: writing has caught up. Thank you so much for your patience.", self.path.name)

    def close(self) -> WriterResult:
        """Writes what is queued and completes the file. Safe to call more than once.

        Returns:
            How the file ended up.
        """
        if not self._closed:
            self._closed = True
            self._queue.put(None)  # tells the writer thread that no more frames come
            self._thread.join()
        return WriterResult(self.path, self._written, self._error)

    # ── writer thread ────────────────────────────────────────────────────────

    def _write_loop(self) -> None:
        """The writer thread: writes queued frames until :meth:`close`, then completes the file."""
        try:
            while (item := self._queue.get()) is not None:
                if self._error is None:  # after a failure, keep draining, so that add() never waits
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
        """Writes one frame as the file's next frame.

        Args:
            index: The frame's index, which must be the file's next.
            frame: The frame.
        """
        if index != self._written:
            log.error(
                "%s: with our apologies, frame %d arrived as file frame %d.",
                self.path.name,
                index,
                self._written,
            )
        if self.passthrough:
            self._mux(frame)
        else:
            picture = self._picture(index, frame)
            if picture is None:  # undecodable, and no picture before it to repeat: writing failed
                return
            self._encode(picture)
        self._written += 1

    def _mux(self, frame: Frame) -> None:
        """Puts a camera JPEG frame into the file untouched, as the next frame."""
        packet = av.Packet(frame.data)
        packet.stream = self._stream
        packet.pts = packet.dts = self._written
        packet.time_base = self._time_base
        self._container.mux(packet)

    def _encode(self, picture: av.VideoFrame) -> None:
        """Encodes a picture as the next frame, and puts what the encoder returns into the file."""
        picture.pts = self._written
        picture.time_base = self._time_base
        for packet in self._stream.encode(picture):
            self._container.mux(packet)

    def _picture(self, index: int, frame: Frame) -> av.VideoFrame | None:
        """The picture to encode for a frame, in the stream's pixel format.

        Args:
            index: The frame's index, for the log.
            frame: The frame.

        Returns:
            The decoded frame, numbered if asked; for a damaged frame the previous picture
            again, so that later frames keep their index; None (and the writer fails) for a
            damaged first frame, which has no previous picture.
        """
        if self._decoder is None:
            self._decoder = decoder_for(frame.mode)
        try:
            picture = decode(self._decoder, frame)
        except (av.FFmpegError, ValueError) as error:
            if self._last_picture is None:
                self._fail(error)
                return None
            log.warning("%s: we are sorry, but frame %d could not be decoded (%s), so we have repeated the"
                        " previous one in its place.", self.path.name, index, error)  # fmt: skip
            return self._last_picture
        if self._frame_numbers:
            rgb = to_rgb(picture)
            draw_frame_number(rgb, self._written)
            picture = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        self._last_picture = picture.reformat(format=self._stream.codec_context.pix_fmt)
        return self._last_picture

    def _fail(self, error: Exception) -> None:
        """Records the first error, which stops writing (later frames are drained, unwritten)."""
        if self._error is None:
            self._error = (
                f"We deeply regret that writing the video failed after {self._written} frames"
                f" ({type(error).__name__}: {error})."
            )
            log.error("%s: %s", self.path.name, self._error)
