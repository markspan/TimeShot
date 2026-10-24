"""Cameras: finding them, the modes they offer, and opening them, alike on every platform.

:func:`get_backend` returns the camera interface of this operating system, or the synthetic
test cameras; everything else in TimeShot works with the camera-independent types of
:mod:`timeshot.cameras.base`, which this package passes on.
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

# Set to a number to replace the real cameras by that many synthetic ones (tests, demos). An
# environment variable, so that the recorder processes, which inherit it, use them too.
SYNTHETIC_ENV = "TIMESHOT_SYNTHETIC_CAMERAS"

# How many synthetic cameras there are when SYNTHETIC_ENV is set to something other than a number.
_DEFAULT_SYNTHETIC_COUNT = 2


def default_backend_name() -> str:
    """The name of the backend to use on this computer.

    Returns:
        "synthetic" if :data:`SYNTHETIC_ENV` asks for synthetic cameras, else this platform's
        camera interface: "dshow" (Windows), "avfoundation" (macOS) or "v4l2" (Linux).
    """
    if _synthetic_count() is not None:
        return "synthetic"
    if sys.platform == "win32":
        return "dshow"
    if sys.platform == "darwin":
        return "avfoundation"
    return "v4l2"


def get_backend(name: str | None = None) -> CameraBackend:
    """A camera backend.

    The backends are imported only when asked for: each depends on libraries or an FFmpeg
    input of its own platform.

    Args:
        name: One of the names :func:`default_backend_name` returns; None for that default.

    Returns:
        The backend.

    Raises:
        CameraError: There is no backend of that name, or it cannot work here.
    """
    match name or default_backend_name():
        case "synthetic":
            from timeshot.cameras.synthetic import SyntheticBackend  # noqa: PLC0415

            count = _synthetic_count()
            return SyntheticBackend(_DEFAULT_SYNTHETIC_COUNT if count is None else count)
        case "dshow":
            from timeshot.cameras.ffmpeg import DshowBackend  # noqa: PLC0415

            return DshowBackend()
        case "avfoundation":
            from timeshot.cameras.ffmpeg import AvfoundationBackend  # noqa: PLC0415

            return AvfoundationBackend()
        case "v4l2":
            from timeshot.cameras.v4l2 import V4l2Backend  # noqa: PLC0415

            return V4l2Backend()
        case unknown:
            raise CameraError(f"We humbly apologize, but we know of no camera backend called {unknown!r}.")


def list_cameras(backend: CameraBackend | None = None) -> list[CameraInfo]:
    """The cameras present now, each with its settings key (see :func:`with_keys`).

    Args:
        backend: The backend to ask; None for this computer's default.

    Returns:
        The cameras, in the backend's order.

    Raises:
        CameraError: The backend could not list the cameras.
    """
    return with_keys((backend or get_backend()).list_cameras())


def _synthetic_count() -> int | None:
    """How many synthetic cameras :data:`SYNTHETIC_ENV` asks for.

    Returns:
        None if it is unset or empty (use the real cameras), else the number it holds, or
        the default count if it holds something else.
    """
    value = os.environ.get(SYNTHETIC_ENV, "").strip()
    if not value:
        return None
    return int(value) if value.isdigit() else _DEFAULT_SYNTHETIC_COUNT
