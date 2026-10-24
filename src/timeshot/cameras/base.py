"""Camera-independent descriptions of cameras, their capture modes and their frames.

Every camera backend (one per operating system's camera interface, plus synthetic cameras for
tests and demos) implements :class:`CameraBackend`, and every camera it opens is a
:class:`CameraStream`. Frames are passed on exactly as the camera delivered them, described in
FFmpeg's terms: JPEG frames are written to the video file untouched, raw frames are encoded
first.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from typing import Final, Protocol

JPEG: Final = "mjpeg"  # FFmpeg's codec name for camera JPEG (Motion-JPEG) frames
RAW: Final = "rawvideo"  # uncompressed frames; CameraMode.pixel_format names their layout

# Frame rates offered within a camera's supported range, besides its maximum.
STANDARD_RATES: Final = (60.0, 50.0, 30.0, 25.0, 24.0, 20.0, 15.0, 10.0, 5.0)

# The text form of a mode, as CameraMode.key() writes it: WxH@FPS[:codec[/pixel_format]].
_MODE_KEY = re.compile(
    r"(?P<w>\d+)x(?P<h>\d+)@(?P<fps>\d+(?:\.\d+)?)(?::(?P<codec>[a-z]+)(?:/(?P<pix>\w+))?)?"
)


class CameraError(Exception):
    """A camera could not be listed, opened or read."""


@dataclass(frozen=True, slots=True)
class CameraMode:
    """One way a camera can deliver video: size, frame rate and frame format.

    Attributes:
        width: Frame width in pixels.
        height: Frame height in pixels.
        fps: Frames per second.
        codec: :data:`JPEG` or :data:`RAW`.
        pixel_format: FFmpeg's name of the pixel layout of RAW frames, e.g. "yuyv422". None
            for JPEG frames, and for raw frames whose layout the backend settles when it opens
            the camera (macOS).
    """

    width: int
    height: int
    fps: float
    codec: str = JPEG
    pixel_format: str | None = None

    @property
    def is_jpeg(self) -> bool:
        """Whether the camera delivers JPEG frames, which can be recorded untouched."""
        return self.codec == JPEG

    @property
    def format_label(self) -> str:
        """The frame format as shown to people: "JPEG", or "raw" and the pixel format."""
        return "JPEG" if self.is_jpeg else f"raw {self.pixel_format or 'video'}"

    @property
    def size_label(self) -> str:
        """The frame size as shown to people, and as FFmpeg takes it: "1920x1080"."""
        return f"{self.width}x{self.height}"

    @property
    def rate(self) -> Fraction:
        """The frame rate as the fraction FFmpeg wants for time bases, e.g. 2997/100."""
        return Fraction(self.fps).limit_denominator(1001)

    def __str__(self) -> str:
        """The mode as shown to people: "1920x1080 @ 30 fps, JPEG"."""
        return f"{self.size_label} @ {self.fps:g} fps, {self.format_label}"

    def key(self) -> str:
        """A compact text form, for the settings file and the command line.

        Returns:
            For example "1920x1080@30:mjpeg" or "640x480@30:rawvideo/yuyv422", which
            :meth:`parse` reads back into an equal mode.
        """
        frame_format = self.codec if self.pixel_format is None else f"{self.codec}/{self.pixel_format}"
        return f"{self.size_label}@{self.fps:g}:{frame_format}"

    @classmethod
    def parse(cls, text: str) -> CameraMode:
        """Reads a mode written as ``WxH@FPS[:codec[/pixel_format]]``.

        Args:
            text: For example "1920x1080@30" (JPEG, the default) or
                "640x480@30:rawvideo/yuyv422", as :meth:`key` writes it or a person types it.

        Returns:
            The mode.

        Raises:
            ValueError: ``text`` is not a mode, or names a codec other than JPEG or RAW.
        """
        match = _MODE_KEY.fullmatch(text.strip())
        if not match:
            raise ValueError(
                f"not a camera mode: {text!r} (expected e.g. 1920x1080@30 or 640x480@30:rawvideo)"
            )
        codec = match["codec"] or JPEG
        if codec not in (JPEG, RAW):
            raise ValueError(f"unknown frame format {codec!r} in {text!r}: use {JPEG} or {RAW}")
        return cls(int(match["w"]), int(match["h"]), float(match["fps"]), codec, match["pix"])


def frame_rates(min_fps: float, max_fps: float) -> list[float]:
    """The frame rates to offer for a camera's supported range.

    Cameras report a range of rates per size and format; offering every standard rate within
    it, besides its maximum, lets people choose e.g. 25 fps to match other equipment.

    Args:
        min_fps: The lowest rate the camera supports in this size and format.
        max_fps: The highest.

    Returns:
        The maximum and the :data:`STANDARD_RATES` within the range, fastest first.
    """
    tolerance = 1e-3  # cameras report e.g. 30.000030 for 30
    standard = {rate for rate in STANDARD_RATES if min_fps - tolerance <= rate <= max_fps + tolerance}
    return sorted(standard | {round(max_fps, 3)}, reverse=True)


def preferred_mode(modes: Iterable[CameraMode]) -> CameraMode | None:
    """The mode to suggest for a camera seen for the first time.

    Args:
        modes: The modes the camera offers.

    Returns:
        A JPEG mode if there is one (it is recorded untouched), preferably at 25 fps or more,
        with the most pixels, and of those the fastest; None if there are no modes.
    """

    def rank(mode: CameraMode) -> tuple[bool, bool, int, float]:
        """Sorts modes from least to most preferred (the most preferred ranks highest)."""
        return (mode.is_jpeg, mode.fps >= 25, mode.width * mode.height, mode.fps)

    return max(modes, key=rank, default=None)


@dataclass(frozen=True, slots=True)
class CameraInfo:
    """A camera as a backend found it.

    Attributes:
        id: What the backend opens the camera by: a device path (Linux), a DirectShow device
            name (Windows) or an AVFoundation index (macOS).
        name: The camera's name, as people know it.
        modes: The modes the camera offers.
        backend: The name of the backend that found it (see :func:`timeshot.cameras.get_backend`),
            which also opens it in its recorder process.
        key: The camera's identity in the settings file: its name, numbered from the second
            identical camera on ("Logitech C920 #2"); see :func:`with_keys`.
    """

    id: str
    name: str
    modes: tuple[CameraMode, ...]
    backend: str
    key: str = ""


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame exactly as the camera delivered it.

    Attributes:
        data: JPEG data, or raw pixels laid out as ``mode.pixel_format`` (rows unpadded).
        mode: The mode the camera delivered it in.
    """

    data: bytes
    mode: CameraMode


class CameraStream(Protocol):
    """An open camera, delivering frames."""

    @property
    def mode(self) -> CameraMode:
        """The mode actually in use: a backend may settle on the nearest the camera supports."""
        ...

    def read(self) -> Frame:
        """Waits for the camera's next frame and returns it as soon as it arrives.

        Returns:
            The frame, untouched.

        Raises:
            CameraError: The camera stopped delivering (unplugged, or closed).
        """
        ...

    def close(self) -> None:
        """Releases the camera, ending a :meth:`read` that waits. Safe to call more than once."""
        ...


class CameraBackend(Protocol):
    """Finds and opens the cameras of one kind: one operating system's camera interface."""

    name: str  # how get_backend() knows it, e.g. "dshow"

    def list_cameras(self) -> list[CameraInfo]:
        """The cameras present now, each with the modes it offers (and no key yet)."""
        ...

    def open(self, camera_id: str, mode: CameraMode) -> CameraStream:
        """Opens a camera.

        Args:
            camera_id: The :attr:`CameraInfo.id` of a camera :meth:`list_cameras` found.
            mode: The mode to open it in; the camera may settle on the nearest it supports.

        Returns:
            The open camera.

        Raises:
            CameraError: The camera could not be opened.
        """
        ...


def with_keys(cameras: Iterable[CameraInfo]) -> list[CameraInfo]:
    """Gives every camera its settings key, which tells identical cameras apart.

    Args:
        cameras: Cameras as a backend lists them, in its order.

    Returns:
        The same cameras, each keyed by its name, with " #2", " #3" and so on added from the
        second camera of the same name on.
    """
    seen: dict[str, int] = {}
    keyed = []
    for camera in cameras:
        seen[camera.name] = count = seen.get(camera.name, 0) + 1
        key = camera.name if count == 1 else f"{camera.name} #{count}"
        keyed.append(dataclasses.replace(camera, key=key))
    return keyed
