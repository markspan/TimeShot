"""Cameras through Video4Linux2 (Linux), using linuxpy.

PyAV's Linux wheels come without FFmpeg's V4L2 input, so this backend talks to the kernel
directly. A camera's JPEG frames arrive exactly as the camera produced them.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from timeshot.cameras.base import JPEG, RAW, CameraError, CameraInfo, CameraMode, Frame, frame_rates

log = logging.getLogger(__name__)

# Kernel buffers: enough to ride out a briefly late reader without the camera dropping frames.
BUFFER_COUNT = 4


class PixelFormat(NamedTuple):
    """How a V4L2 pixel format is known in FFmpeg's terms."""

    codec: str  # JPEG or RAW
    pixel_format: str | None  # FFmpeg's name of the layout of RAW frames
    bytes_per_pixel: int | None  # of packed RAW layouts; None for JPEG and planar layouts


# The V4L2 pixel formats TimeShot records, by their fourcc name in linuxpy.
PIXEL_FORMATS: dict[str, PixelFormat] = {
    "MJPEG": PixelFormat(JPEG, None, None),
    "JPEG": PixelFormat(JPEG, None, None),
    "YUYV": PixelFormat(RAW, "yuyv422", 2),
    "UYVY": PixelFormat(RAW, "uyvy422", 2),
    "RGB24": PixelFormat(RAW, "rgb24", 3),
    "BGR24": PixelFormat(RAW, "bgr24", 3),
    "NV12": PixelFormat(RAW, "nv12", None),
    "YUV420": PixelFormat(RAW, "yuv420p", None),
}


def _linuxpy() -> Any:
    """The video module of linuxpy, imported only when cameras are used (it exists on Linux only).

    Raises:
        CameraError: linuxpy is not installed.
    """
    try:
        from linuxpy.video import device  # noqa: PLC0415  (Linux-only dependency)
    except ImportError as error:  # pragma: no cover - only without the dependency
        raise CameraError("linuxpy is needed for cameras on Linux: pip install linuxpy") from error
    return device


class V4l2Backend:
    """The V4L2 capture devices (``/dev/video*``) of this computer."""

    name = "v4l2"

    def list_cameras(self) -> list[CameraInfo]:
        """The capture devices that offer at least one mode TimeShot can record.

        Returns:
            The cameras. A device that cannot be opened is skipped, and one without a usable
            mode left out, each with a warning in the log.
        """
        v4l2 = _linuxpy()
        cameras = []
        for device in v4l2.iter_video_capture_devices():
            try:
                with device:
                    name = device.info.card or str(device.filename)
                    modes = _modes(device)
                    formats = sorted({frame_type.pixel_format.name for frame_type in device.info.frame_types})
                    log.debug("%s (%s) offers %s", name, device.filename, ", ".join(formats) or "nothing")
            except OSError as error:
                log.warning("Skipping %s: %s", device.filename, error)
                continue
            if modes:
                cameras.append(CameraInfo(str(device.filename), name, tuple(modes), self.name))
            else:
                log.warning("Camera %r offers no mode TimeShot can record; left out.", name)
        return cameras

    def open(self, camera_id: str, mode: CameraMode) -> V4l2Stream:
        """Opens a camera.

        Args:
            camera_id: Its device path, e.g. "/dev/video0".
            mode: The mode to open it in.

        Returns:
            The open camera.

        Raises:
            CameraError: It could not be opened in this mode.
        """
        return V4l2Stream(_linuxpy(), camera_id, mode)


def _modes(device: Any) -> list[CameraMode]:
    """The modes TimeShot can record of an open linuxpy device.

    Args:
        device: The device, opened.

    Returns:
        Its modes in the :data:`PIXEL_FORMATS` TimeShot knows, once each, with the frame
        rates :func:`~timeshot.cameras.base.frame_rates` offers.
    """
    modes: dict[tuple[str, str | None, int, int, float], CameraMode] = {}
    for frame_type in device.info.frame_types:
        known = PIXEL_FORMATS.get(frame_type.pixel_format.name)
        if known is None or not frame_type.max_fps:
            continue
        width, height = int(frame_type.width), int(frame_type.height)
        for fps in frame_rates(float(frame_type.min_fps), float(frame_type.max_fps)):
            mode = CameraMode(width, height, fps, known.codec, known.pixel_format)
            modes.setdefault((known.codec, known.pixel_format, width, height, fps), mode)
    return list(modes.values())


def _fourcc_for(mode: CameraMode) -> str:
    """The V4L2 pixel format that delivers frames in ``mode``.

    Args:
        mode: The mode to capture in.

    Returns:
        The format's fourcc name in linuxpy.

    Raises:
        CameraError: V4L2 has no format for the mode's frame format.
    """
    for fourcc, known in PIXEL_FORMATS.items():
        if known.codec == mode.codec and (mode.is_jpeg or known.pixel_format == mode.pixel_format):
            return fourcc
    raise CameraError(f"V4L2 has no pixel format for {mode.format_label}")


class V4l2Stream:
    """An open V4L2 camera, streaming through memory-mapped kernel buffers."""

    def __init__(self, v4l2: Any, path: str, requested: CameraMode) -> None:
        """Opens the camera and starts streaming.

        Args:
            v4l2: linuxpy's video module.
            path: The camera's device path.
            requested: The mode to open it in; the driver may settle on the nearest it supports.

        Raises:
            CameraError: The camera could not be opened in a format TimeShot can record.
        """
        fourcc = _fourcc_for(requested)  # before the device opens: nothing to release if it raises
        capture_type = v4l2.BufferType.VIDEO_CAPTURE
        self._device = v4l2.Device(path)
        try:
            self._device.open()
            # Ask for the mode, then read back what the driver settled on.
            self._device.set_format(capture_type, requested.width, requested.height, v4l2.PixelFormat[fourcc])
            self._device.set_fps(capture_type, requested.fps)
            granted = self._device.get_format(capture_type)
            fps = float(self._device.get_fps(capture_type) or requested.fps)
            known = PIXEL_FORMATS.get(granted.pixel_format.name)
            if known is None:
                raise CameraError(
                    f"the camera switched to the unsupported format {granted.pixel_format.name}"
                )
            width = int(granted.width)
            self._mode = CameraMode(width, int(granted.height), fps, known.codec, known.pixel_format)

            # Raw rows may be padded at their end; frames are passed on unpadded (see read()).
            self._row_bytes = int(granted.bytes_per_line)
            self._packed_row_bytes = width * known.bytes_per_pixel if known.bytes_per_pixel else None
            if known.codec == RAW and self._packed_row_bytes is None and self._row_bytes not in (0, width):
                raise CameraError(f"padded {known.pixel_format} frames are not supported")

            self._capture = v4l2.VideoCapture(self._device, size=BUFFER_COUNT)
            self._capture.open()
        except CameraError:
            self._device.close()
            raise
        except (OSError, ValueError) as error:
            self._device.close()
            raise CameraError(f"could not open {path} in {requested}: {error}") from error
        self._closed = False

    @property
    def mode(self) -> CameraMode:
        """The mode the driver settled on."""
        return self._mode

    def read(self) -> Frame:
        """Waits for the camera's next frame.

        Returns:
            The frame: JPEG data untouched, or raw pixels with any row padding removed.

        Raises:
            CameraError: The camera stopped delivering frames.
        """
        try:
            frame = self._capture.buffer.read()
        except (OSError, AttributeError) as error:  # AttributeError: buffer gone after close()
            raise CameraError(f"the camera stopped delivering frames: {error}") from error
        data = bytes(frame.data)
        packed = self._packed_row_bytes
        if packed and self._row_bytes > packed:
            rows = len(data) // self._row_bytes
            data = b"".join(data[r * self._row_bytes : r * self._row_bytes + packed] for r in range(rows))
        return Frame(data, self._mode)

    def close(self) -> None:
        """Stops streaming and releases the camera. Safe to call more than once."""
        if not self._closed:
            self._closed = True
            try:
                self._capture.close()
            finally:
                self._device.close()
