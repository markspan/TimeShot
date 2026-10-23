"""Cameras: finding them, their modes, and opening them, the same way on every platform.

:func:`get_backend` returns the camera interface of this operating system (or the
synthetic test cameras); everything else in TimeShot works with the camera-independent
types from :mod:`timeshot.cameras.base`.
"""

from __future__ import annotations

import os
import sys

from timeshot.cameras.base import (
    JPEG,
    RAW,
    CameraBackend,
    CameraError,
    CameraInfo,
    CameraMode,
    CameraStream,
    Frame,
    preferred_mode,
    with_keys,
)

__all__ = [
    "JPEG",
    "RAW",
    "SYNTHETIC_ENV",
    "CameraBackend",
    "CameraError",
    "CameraInfo",
    "CameraMode",
    "CameraStream",
    "Frame",
    "default_backend_name",
    "get_backend",
    "list_cameras",
    "preferred_mode",
]

# Set to a number to replace the real cameras by that many synthetic ones (tests, demos).
SYNTHETIC_ENV = "TIMESHOT_SYNTHETIC_CAMERAS"


def default_backend_name() -> str:
    """The backend to use here: synthetic cameras if asked for, else this platform's own."""
    if os.environ.get(SYNTHETIC_ENV, "").strip():
        return "synthetic"
    if sys.platform == "win32":
        return "dshow"
    if sys.platform == "darwin":
        return "avfoundation"
    return "v4l2"


def get_backend(name: str | None = None) -> CameraBackend:
    """The backend called ``name`` (see :func:`default_backend_name` for the choices)."""
    name = name or default_backend_name()
    if name == "synthetic":
        from timeshot.cameras.synthetic import SyntheticBackend  # noqa: PLC0415

        count = os.environ.get(SYNTHETIC_ENV, "").strip()
        return SyntheticBackend(int(count) if count.isdigit() else 2)
    if name in ("dshow", "avfoundation"):
        from timeshot.cameras.ffmpeg import FfmpegBackend  # noqa: PLC0415

        return FfmpegBackend(name)
    if name == "v4l2":
        from timeshot.cameras.v4l2 import V4l2Backend  # noqa: PLC0415

        return V4l2Backend()
    raise CameraError(f"unknown camera backend {name!r}")


def list_cameras(backend: CameraBackend | None = None) -> list[CameraInfo]:
    """The cameras present now, each with a settings key that tells identical cameras apart."""
    return with_keys((backend or get_backend()).list_cameras())
