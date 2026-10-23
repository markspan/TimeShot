"""Synthetic cameras: paced test-pattern sources for tests, demos and machines without cameras.

Each frame shows a moving bar and its own sequence number as a row of black and white
blocks (:func:`read_sequence_number` decodes it again), so a test can check from the video
file alone that every frame was written, in order. Enabled with ``TIMESHOT_SYNTHETIC_CAMERAS=N``
or ``timeshot --synthetic N``.
"""

from __future__ import annotations

import threading
import time

import av
import numpy as np
import numpy.typing as npt

from timeshot.cameras.base import JPEG, RAW, CameraError, CameraInfo, CameraMode, Frame
from timeshot.frames import encode_jpeg, jpeg_encoder

BACKEND_NAME = "synthetic"

MODES = (
    CameraMode(1280, 720, 30.0),
    CameraMode(640, 480, 30.0),
    CameraMode(640, 480, 15.0),
    CameraMode(640, 480, 30.0, RAW, "yuyv422"),
)

# The sequence number is drawn as SEQUENCE_BITS blocks of BLOCK pixels along the bottom-left
# corner (clear of the optional frame-number label at the top), most significant bit first:
# large and high-contrast enough to survive JPEG.
SEQUENCE_BITS = 16
BLOCK = 16


def draw_sequence_number(rgb: npt.NDArray[np.uint8], number: int) -> None:
    """Draws ``number`` (modulo 2**SEQUENCE_BITS) into the frame's bottom rows."""
    for bit in range(SEQUENCE_BITS):
        on = (number >> (SEQUENCE_BITS - 1 - bit)) & 1
        rgb[-BLOCK:, bit * BLOCK : (bit + 1) * BLOCK] = 255 if on else 0


def read_sequence_number(rgb: npt.NDArray[np.uint8]) -> int:
    """Reads back the number :func:`draw_sequence_number` drew, from a decoded frame."""
    number = 0
    for bit in range(SEQUENCE_BITS):
        block = rgb[-BLOCK + 2 : -2, bit * BLOCK + 2 : (bit + 1) * BLOCK - 2]
        number = (number << 1) | int(block.mean() > 127)
    return number


class SyntheticBackend:
    """``count`` identical test-pattern cameras."""

    name = BACKEND_NAME

    def __init__(self, count: int = 2) -> None:
        self.count = count

    def list_cameras(self) -> list[CameraInfo]:
        return [
            CameraInfo(id=str(i), name=f"Synthetic camera {i + 1}", modes=MODES, backend=self.name)
            for i in range(self.count)
        ]

    def open(self, camera_id: str, mode: CameraMode) -> SyntheticStream:
        if not camera_id.isdigit() or int(camera_id) >= self.count:
            raise CameraError(f"no synthetic camera {camera_id!r}")
        return SyntheticStream(mode, hue=int(camera_id))


class SyntheticStream:
    """Delivers frames at the mode's frame rate, like a camera: a late reader gets the next
    frame due, not a backlog (frames it was too late for are dropped, and numbered as such).

    Unpaced (``paced=False``), it delivers consecutively numbered frames as fast as they are read.
    """

    def __init__(self, mode: CameraMode, hue: int = 0, *, paced: bool = True) -> None:
        if mode.codec == RAW and mode.pixel_format != "yuyv422":
            raise CameraError("synthetic cameras deliver raw frames as yuyv422 only")
        self._mode = mode
        self._hue = hue
        self._interval = 1.0 / mode.fps if paced else 0.0
        self._next_due = time.perf_counter()
        self._sequence = 0
        self._closed = threading.Event()
        # Medium quality: the test pattern only needs to stay legible.
        self._encoder = jpeg_encoder(mode.width, mode.height, quality=5) if mode.codec == JPEG else None

    @property
    def mode(self) -> CameraMode:
        return self._mode

    def read(self) -> Frame:
        now = time.perf_counter()
        if now < self._next_due:
            if self._closed.wait(self._next_due - now):
                raise CameraError("camera closed")
        elif self._interval:
            # Too late for one or more frames: skip them, as a camera with a small buffer does.
            missed = int((now - self._next_due) / self._interval)
            self._sequence += missed
            self._next_due += missed * self._interval
        if self._closed.is_set():
            raise CameraError("camera closed")
        frame = Frame(self._render(self._sequence), self._mode)
        self._sequence += 1
        self._next_due += self._interval
        return frame

    def close(self) -> None:
        self._closed.set()

    def _render(self, number: int) -> bytes:
        mode = self._mode
        rgb = np.zeros((mode.height, mode.width, 3), np.uint8)
        rgb[..., (self._hue % 3)] = 60
        bar = (number * 8) % mode.width
        rgb[mode.height // 3 : 2 * mode.height // 3, bar : bar + 24] = 220
        draw_sequence_number(rgb, number)
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        if self._encoder is None:  # packed, unpadded rows: what a camera's raw frames look like
            return np.ascontiguousarray(frame.reformat(format="yuyv422").to_ndarray()).tobytes()
        return encode_jpeg(self._encoder, frame)
