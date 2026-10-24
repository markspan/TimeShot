"""Cameras through Video4Linux2 (Linux), using linuxpy.

PyAV's Linux wheels come without FFmpeg's V4L2 input, so this backend talks to the kernel
directly. A camera's JPEG frames arrive exactly as the camera produced them.
"""

from __future__ import annotations

import logging
from typing import Any

from timeshot.cameras.base import JPEG, RAW, CameraError, CameraInfo, CameraMode, Frame, frame_rates

log = logging.getLogger(__name__)

BACKEND_NAME = "v4l2"

# Kernel buffers: enough to ride out a briefly late reader without the camera dropping frames.
BUFFER_COUNT = 4

# V4L2 pixel format (fourcc name in linuxpy) -> (FFmpeg codec, FFmpeg pixel format, bytes per pixel
# of packed formats or None for planar ones).
PIXEL_FORMATS: dict[str, tuple[str, str | None, int | None]] = {
    "MJPEG": (JPEG, None, None),
    "JPEG": (JPEG, None, None),
    "YUYV": (RAW, "yuyv422", 2),
    "UYVY": (RAW, "uyvy422", 2),
    "RGB24": (RAW, "rgb24", 3),
    "BGR24": (RAW, "bgr24", 3),
    "NV12": (RAW, "nv12", None),
    "YUV420": (RAW, "yuv420p", None),
}


def _linuxpy() -> Any:
    try:
        from linuxpy.video import device  # noqa: PLC0415  (Linux-only dependency)
    except ImportError as error:  # pragma: no cover - only without the dependency
        raise CameraError("linuxpy is needed for cameras on Linux: pip install linuxpy") from error
    return device


class V4l2Backend:
    """The V4L2 capture devices (``/dev/video*``) of this computer."""

    name = BACKEND_NAME

    def list_cameras(self) -> list[CameraInfo]:
        v4l2 = _linuxpy()
        cameras = []
        for device in v4l2.iter_video_capture_devices():
            try:
                with device:
                    name = device.info.card or str(device.filename)
                    modes = _modes(v4l2, device)
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
        return V4l2Stream(_linuxpy(), camera_id, mode)


def _modes(v4l2: Any, device: Any) -> list[CameraMode]:
    modes: dict[tuple[str, str | None, int, int, float], CameraMode] = {}
    for frame_type in device.info.frame_types:
        known = PIXEL_FORMATS.get(frame_type.pixel_format.name)
        if known is None or not frame_type.max_fps:
            continue
        codec, pix, _bpp = known
        width, height = int(frame_type.width), int(frame_type.height)
        for fps in frame_rates(float(frame_type.min_fps), float(frame_type.max_fps)):
            modes.setdefault((codec, pix, width, height, fps), CameraMode(width, height, fps, codec, pix))
    return list(modes.values())


class V4l2Stream:
    """An open V4L2 camera, streaming through memory-mapped kernel buffers."""

    def __init__(self, v4l2: Any, path: str, requested: CameraMode) -> None:
        fourcc = next(
            (
                name
                for name, (codec, pix, _) in PIXEL_FORMATS.items()
                if codec == requested.codec and (codec == JPEG or pix == requested.pixel_format)
            ),
            None,
        )
        if fourcc is None:
            raise CameraError(f"V4L2 has no pixel format for {requested.format_label}")
        capture_type = v4l2.BufferType.VIDEO_CAPTURE
        self._device = v4l2.Device(path)
        try:
            self._device.open()
            self._device.set_format(capture_type, requested.width, requested.height, v4l2.PixelFormat[fourcc])
            self._device.set_fps(capture_type, requested.fps)
            fmt = self._device.get_format(capture_type)
            fps = float(self._device.get_fps(capture_type) or requested.fps)
            codec, pix, bytes_per_pixel = PIXEL_FORMATS.get(fmt.pixel_format.name, (None, None, None))
            if codec is None:
                raise CameraError(f"the camera switched to the unsupported format {fmt.pixel_format.name}")
            self._mode = CameraMode(int(fmt.width), int(fmt.height), fps, codec, pix)
            # Raw rows may be padded; frames go on unpadded (see read()).
            self._row_bytes = int(fmt.bytes_per_line)
            self._packed_row_bytes = int(fmt.width) * bytes_per_pixel if bytes_per_pixel else None
            if codec == RAW and self._packed_row_bytes is None and self._row_bytes not in (0, int(fmt.width)):
                raise CameraError(f"padded {pix} frames are not supported")
            self._capture = v4l2.VideoCapture(self._device, size=BUFFER_COUNT)
            self._capture.open()
        except (OSError, ValueError) as error:
            self._device.close()
            raise CameraError(f"could not open {path} in {requested}: {error}") from error
        self._closed = False

    @property
    def mode(self) -> CameraMode:
        return self._mode

    def read(self) -> Frame:
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
        if not self._closed:
            self._closed = True
            try:
                self._capture.close()
            finally:
                self._device.close()
