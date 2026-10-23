"""Camera-independent descriptions of cameras, their capture modes and their frames.

Every camera backend (one per operating system, plus a synthetic one for tests and demos)
implements :class:`CameraBackend`. Frames are passed on exactly as the camera delivered
them, in FFmpeg terms: JPEG frames are written to the video file untouched, raw frames are
encoded first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol

JPEG: Final = "mjpeg"  # FFmpeg's codec name for camera JPEG (Motion-JPEG) frames
RAW: Final = "rawvideo"  # uncompressed frames; CameraMode.pixel_format names their layout


class CameraError(Exception):
    """A camera could not be listed, opened or read."""


@dataclass(frozen=True, slots=True)
class CameraMode:
    """One way a camera can deliver video: size, frame rate and frame format."""

    width: int
    height: int
    fps: float
    codec: str = JPEG
    # FFmpeg pixel format of RAW frames, e.g. "yuyv422". None when the backend decides it on
    # opening the camera (macOS), and for JPEG frames.
    pixel_format: str | None = None

    @property
    def is_jpeg(self) -> bool:
        return self.codec == JPEG

    @property
    def format_label(self) -> str:
        """The frame format as shown to people: "JPEG", or the raw pixel format."""
        return "JPEG" if self.is_jpeg else f"raw {self.pixel_format or 'video'}"

    @property
    def size_label(self) -> str:
        return f"{self.width}x{self.height}"

    def __str__(self) -> str:
        return f"{self.size_label} @ {self.fps:g} fps, {self.format_label}"

    def key(self) -> str:
        """A compact text form that :meth:`parse` reads back, for settings and the command line."""
        fmt = self.codec if self.pixel_format is None else f"{self.codec}/{self.pixel_format}"
        return f"{self.width}x{self.height}@{self.fps:g}:{fmt}"

    @classmethod
    def parse(cls, text: str) -> CameraMode:
        """Reads ``WxH@FPS[:codec[/pixel_format]]``.

        For example ``1920x1080@30`` (JPEG) or ``640x480@30:rawvideo/yuyv422``.
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


_MODE_KEY = re.compile(
    r"(?P<w>\d+)x(?P<h>\d+)@(?P<fps>\d+(?:\.\d+)?)(?::(?P<codec>[a-z]+)(?:/(?P<pix>\w+))?)?"
)


# Frame rates offered within a camera's supported range, besides its maximum.
STANDARD_RATES = (60.0, 50.0, 30.0, 25.0, 24.0, 20.0, 15.0, 10.0, 5.0)


def frame_rates(min_fps: float, max_fps: float) -> list[float]:
    """The rates to offer for a supported range: its maximum, and the standard rates within it."""
    rates = {round(max_fps, 3)} | {r for r in STANDARD_RATES if min_fps - 1e-3 <= r <= max_fps + 1e-3}
    return sorted(rates, reverse=True)


def preferred_mode(modes: tuple[CameraMode, ...] | list[CameraMode]) -> CameraMode | None:
    """The mode to suggest first: JPEG if offered (written untouched), at 25 fps or more,
    with the most pixels; then the fastest mode of that size."""
    if not modes:
        return None

    def rank(mode: CameraMode) -> tuple[bool, bool, int, float]:
        return (mode.is_jpeg, mode.fps >= 25, mode.width * mode.height, mode.fps)

    return max(modes, key=rank)


@dataclass(frozen=True, slots=True)
class CameraInfo:
    """A camera as found by a backend."""

    id: str  # what the backend needs to open it (device path, DirectShow name, AVFoundation index)
    name: str  # human-readable name
    modes: tuple[CameraMode, ...]
    backend: str  # registry name of the backend that found it (see timeshot.cameras.get_backend)
    # Stable identity for remembering settings: the name, plus " #2" etc. for identical cameras.
    key: str = ""


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame exactly as the camera delivered it: JPEG data, or raw pixels in ``mode.pixel_format``."""

    data: bytes
    mode: CameraMode


class CameraStream(Protocol):
    """An open camera delivering frames."""

    @property
    def mode(self) -> CameraMode:
        """The mode actually in use: a backend may settle on the nearest the camera supports."""
        ...

    def read(self) -> Frame:
        """Waits for the camera's next frame and returns it as soon as it arrives.

        Raises :class:`CameraError` when the camera stops delivering (unplugged, closed).
        """
        ...

    def close(self) -> None:
        """Releases the camera. Safe to call more than once."""
        ...


class CameraBackend(Protocol):
    """Finds and opens the cameras of one kind (one operating system's camera interface)."""

    name: str

    def list_cameras(self) -> list[CameraInfo]:
        """The cameras present now, each with the modes it offers."""
        ...

    def open(self, camera_id: str, mode: CameraMode) -> CameraStream:
        """Opens a camera found by :meth:`list_cameras` in (or as near as possible to) ``mode``."""
        ...


def with_keys(cameras: list[CameraInfo]) -> list[CameraInfo]:
    """Gives every camera its settings key: its name, numbered from the second identical one on."""
    seen: dict[str, int] = {}
    keyed = []
    for camera in cameras:
        seen[camera.name] = seen.get(camera.name, 0) + 1
        key = camera.name if seen[camera.name] == 1 else f"{camera.name} #{seen[camera.name]}"
        keyed.append(CameraInfo(camera.id, camera.name, camera.modes, camera.backend, key))
    return keyed
