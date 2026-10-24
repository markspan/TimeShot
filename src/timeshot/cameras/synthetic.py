"""Synthetic cameras: test-pattern sources for tests, demos and computers without cameras.

Each frame shows a moving bar and its own sequence number as a row of black and white blocks
(:func:`read_sequence_number` decodes it again), so a test can check from the video file alone
that every frame was written, in order. They are enabled with ``TIMESHOT_SYNTHETIC_CAMERAS=N``
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

# What every synthetic camera offers: JPEG in two sizes and rates, and one raw format.
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

# The JPEG quality of the frames (FFmpeg's scale, 2 to 31): the pattern only needs to stay legible.
JPEG_QUALITY = 5


def draw_sequence_number(rgb: npt.NDArray[np.uint8], number: int) -> None:
    """Draws a number into a frame's bottom rows, as black (0) and white (1) blocks.

    Args:
        rgb: The frame, as an RGB array of at least ``SEQUENCE_BITS * BLOCK`` pixels wide;
            drawn into in place.
        number: The number; only its lowest :data:`SEQUENCE_BITS` bits fit.
    """
    for bit in range(SEQUENCE_BITS):
        on = (number >> (SEQUENCE_BITS - 1 - bit)) & 1
        rgb[-BLOCK:, bit * BLOCK : (bit + 1) * BLOCK] = 255 if on else 0


def read_sequence_number(rgb: npt.NDArray[np.uint8]) -> int:
    """Reads back the number :func:`draw_sequence_number` drew.

    Args:
        rgb: The decoded frame, as an RGB array.

    Returns:
        The number, read from the middle of each block (the edges blur in JPEG).
    """
    number = 0
    for bit in range(SEQUENCE_BITS):
        block = rgb[-BLOCK + 2 : -2, bit * BLOCK + 2 : (bit + 1) * BLOCK - 2]
        number = (number << 1) | int(block.mean() > 127)
    return number


class SyntheticBackend:
    """A number of identical test-pattern cameras."""

    name = "synthetic"

    def __init__(self, count: int = 2) -> None:
        """Creates the backend.

        Args:
            count: How many cameras it has.
        """
        self.count = count

    def list_cameras(self) -> list[CameraInfo]:
        """The cameras, named "Synthetic camera 1" and so on, with ids "0" and so on."""
        return [
            CameraInfo(id=str(i), name=f"Synthetic camera {i + 1}", modes=MODES, backend=self.name)
            for i in range(self.count)
        ]

    def open(self, camera_id: str, mode: CameraMode) -> SyntheticStream:
        """Opens a camera, paced like a real one.

        Args:
            camera_id: Its id, from :meth:`list_cameras`.
            mode: The mode to deliver in.

        Returns:
            The open camera; each camera's background has a hue of its own.

        Raises:
            CameraError: There is no camera with this id, or it cannot deliver this mode.
        """
        if not camera_id.isdigit() or int(camera_id) >= self.count:
            raise CameraError(f"no synthetic camera {camera_id!r}")
        return SyntheticStream(mode, hue=int(camera_id))


class SyntheticStream:
    """Delivers frames at the mode's frame rate, as a camera does.

    A reader that is late gets the frame now due, not a backlog: the frames it was too late
    for are dropped, and their numbers skipped, as a camera with a small buffer does.
    """

    def __init__(self, mode: CameraMode, hue: int = 0, *, paced: bool = True) -> None:
        """Opens the camera.

        Args:
            mode: The mode to deliver in: JPEG, or raw yuyv422.
            hue: Picks the background color (red, green or blue), to tell cameras apart.
            paced: Whether to deliver at the mode's frame rate. Unpaced, consecutively
                numbered frames come as fast as they are read (for tests of the writer).

        Raises:
            CameraError: The mode asks for a raw format other than yuyv422.
        """
        if mode.codec == RAW and mode.pixel_format != "yuyv422":
            raise CameraError("synthetic cameras deliver raw frames as yuyv422 only")
        self._mode = mode
        self._hue = hue
        self._interval = 1.0 / mode.fps if paced else 0.0
        self._next_due = time.perf_counter()
        self._sequence = 0
        self._closed = threading.Event()
        self._encoder = jpeg_encoder(mode.width, mode.height, JPEG_QUALITY) if mode.codec == JPEG else None

    @property
    def mode(self) -> CameraMode:
        """The mode it was opened in."""
        return self._mode

    def read(self) -> Frame:
        """Waits until the next frame is due, then makes it.

        Returns:
            The frame, showing its sequence number.

        Raises:
            CameraError: The camera was closed.
        """
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
        """Closes the camera, ending a :meth:`read` that waits."""
        self._closed.set()

    def _render(self, number: int) -> bytes:
        """Draws frame ``number`` of the test pattern.

        Args:
            number: The frame's sequence number.

        Returns:
            The frame's data: JPEG, or packed yuyv422 pixels without row padding, which is
            what a camera's raw frames look like.
        """
        mode = self._mode
        rgb = np.zeros((mode.height, mode.width, 3), np.uint8)
        rgb[..., self._hue % 3] = 60
        bar = (number * 8) % mode.width  # moves 8 pixels per frame
        rgb[mode.height // 3 : 2 * mode.height // 3, bar : bar + 24] = 220
        draw_sequence_number(rgb, number)
        picture = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        if self._encoder is None:
            return np.ascontiguousarray(picture.reformat(format="yuyv422").to_ndarray()).tobytes()
        return encode_jpeg(self._encoder, picture)
