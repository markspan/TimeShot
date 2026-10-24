"""Cameras through FFmpeg's device inputs (PyAV): DirectShow on Windows, AVFoundation on macOS.

FFmpeg lists a camera's modes only in its log, so :class:`FfmpegBackend` captures that log
and the ``parse_*`` functions read it; they are kept separate so that tests can check them
against recorded output. DirectShow delivers a camera's JPEG frames untouched; AVFoundation
always delivers decoded (raw) frames, which TimeShot then encodes.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
from collections.abc import Iterator
from typing import NamedTuple

import av
import av.logging

from timeshot.cameras.base import JPEG, RAW, CameraError, CameraInfo, CameraMode, Frame, frame_rates

log = logging.getLogger(__name__)

# Raw pixel formats TimeShot can decode; others are left out of the mode list.
SUPPORTED_RAW = {"yuyv422", "uyvy422", "nv12", "yuv420p", "yuvj420p", "rgb24", "bgr24", "bgr0", "0rgb"}

# DirectShow buffers this much video when the reader is briefly late, instead of dropping frames.
DSHOW_BUFFER = "256M"


# ── DirectShow ───────────────────────────────────────────────────────────────

_DSHOW_MODE = re.compile(
    r"(?:vcodec=(?P<codec>\S+)|pixel_format=(?P<pix>\S+))\s+"
    r"min s=(?P<w0>\d+)x(?P<h0>\d+) fps=(?P<f0>[\d.e+]+)\s+"
    r"max s=(?P<w1>\d+)x(?P<h1>\d+) fps=(?P<f1>[\d.e+]+)"
)


def parse_dshow_modes(log_text: str) -> list[CameraMode]:
    """Reads the modes from the log of opening a DirectShow camera with ``list_options``.

    The relevant lines look like ``vcodec=mjpeg  min s=1920x1080 fps=5 max s=1920x1080 fps=30``
    or ``pixel_format=yuyv422  min s=640x480 fps=5 max s=640x480 fps=30``.
    """
    modes: dict[tuple[str, str | None, int, int, float], CameraMode] = {}
    for match in _DSHOW_MODE.finditer(log_text):
        if match["codec"]:
            if match["codec"] != JPEG:
                continue  # e.g. h264: not a frame-exact format
            codec, pix = JPEG, None
        else:
            if match["pix"] not in SUPPORTED_RAW:
                continue
            codec, pix = RAW, match["pix"]
        width, height = int(match["w1"]), int(match["h1"])
        for fps in frame_rates(float(match["f0"]), float(match["f1"])):
            modes.setdefault((codec, pix, width, height, fps), CameraMode(width, height, fps, codec, pix))
    return list(modes.values())


# ── AVFoundation ─────────────────────────────────────────────────────────────

# Found anywhere in a line: FFmpeg may put its "[AVFoundation indev @ 0x...]" prefix in front.
_AVF_DEVICE = re.compile(r"\[(?P<index>\d+)\] (?P<name>.+?)\s*$")
_AVF_MODE = re.compile(r"(?P<w>\d+)x(?P<h>\d+)@\[(?P<f0>[\d.]+) (?P<f1>[\d.]+)\]fps")


def parse_avfoundation_devices(log_text: str) -> list[tuple[str, str]]:
    """Reads ``(index, name)`` of the video devices from the log of ``list_devices``,
    leaving out screens (AVFoundation lists them as "Capture screen N")."""
    devices = []
    in_video = False
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if "AVFoundation video devices" in line:
            in_video = True
        elif "AVFoundation audio devices" in line:
            in_video = False
        elif (
            in_video
            and (match := _AVF_DEVICE.search(line))
            and not match["name"].startswith("Capture screen")
        ):
            devices.append((match["index"], match["name"]))
    return devices


def parse_avfoundation_modes(log_text: str) -> list[CameraMode]:
    """Reads the modes from the "Supported modes" list AVFoundation logs when a requested
    video size is not supported, e.g. ``1280x720@[1.000000 30.000000]fps``."""
    modes: dict[tuple[int, int, float], CameraMode] = {}
    for match in _AVF_MODE.finditer(log_text):
        width, height = int(match["w"]), int(match["h"])
        for fps in frame_rates(float(match["f0"]), float(match["f1"])):
            # The pixel format is settled when the camera is opened (see AVF_PIXEL_FORMAT).
            modes.setdefault((width, height, fps), CameraMode(width, height, fps, RAW, None))
    return list(modes.values())


# Requested from AVFoundation: every Mac camera offers it. If one does not, FFmpeg picks
# another supported format by itself.
AVF_PIXEL_FORMAT = "uyvy422"


# ── backend ──────────────────────────────────────────────────────────────────


class _Device(NamedTuple):
    """A device as FFmpeg lists it."""

    id: str  # what FFmpeg opens it by
    name: str
    video: bool  # known to deliver video; False: unknown (FFmpeg could not tell)


# FFmpeg's log is process-wide: one capture at a time.
_log_lock = threading.Lock()


def capture_ffmpeg_log(url: str, input_format: str, options: dict[str, str]) -> str:
    """Opens ``url`` only for what FFmpeg logs while doing so (device and mode lists), and
    returns that log as text. FFmpeg logs a line in several pieces, which are joined here."""
    with _log_lock:
        level, skip_repeated = av.logging.get_level(), av.logging.get_skip_repeated()
        av.logging.set_level(av.logging.INFO)
        av.logging.set_skip_repeated(False)  # mode lines may repeat word for word
        try:
            # The listing requests end in an "exit" error: expected.
            with av.logging.Capture() as messages, contextlib.suppress(av.FFmpegError):
                av.open(url, format=input_format, options=options).close()
        finally:
            av.logging.set_level(level)
            av.logging.set_skip_repeated(skip_repeated)
    return "".join(message for _level, _name, message in messages)


class FfmpegBackend:
    """Cameras of one FFmpeg device input: ``"dshow"`` (Windows) or ``"avfoundation"`` (macOS)."""

    def __init__(self, input_format: str) -> None:
        if input_format not in ("dshow", "avfoundation"):
            raise ValueError(f"unsupported FFmpeg camera input {input_format!r}")
        if input_format not in av.formats_available:
            raise CameraError(f"this FFmpeg build has no {input_format} input")
        self.name = input_format

    def list_cameras(self) -> list[CameraInfo]:
        cameras = []
        for device in self._devices():
            modes = self._modes(device.id)
            if modes:
                cameras.append(CameraInfo(device.id, device.name, tuple(modes), self.name))
            elif device.video:
                log.warning("Camera %r offers no mode TimeShot can record; left out.", device.name)
            else:
                log.debug("%r offers no video mode; left out.", device.name)
        return cameras

    def open(self, camera_id: str, mode: CameraMode) -> FfmpegStream:
        options = {"video_size": mode.size_label, "framerate": f"{mode.fps:g}"}
        if self.name == "dshow":
            options["rtbufsize"] = DSHOW_BUFFER
            if mode.is_jpeg:
                options["vcodec"] = JPEG
            elif mode.pixel_format:
                options["pixel_format"] = mode.pixel_format
        else:
            options["pixel_format"] = mode.pixel_format or AVF_PIXEL_FORMAT
        try:
            container = av.open(self._url(camera_id), format=self.name, options=options)
        except av.FFmpegError as error:
            raise CameraError(f"could not open the camera in {mode}: {error}") from error
        return FfmpegStream(container, mode)

    def _url(self, camera_id: str) -> str:
        # DirectShow takes the device's unique name; AVFoundation its index (names repeat for
        # identical cameras), with no audio device.
        return f"video={camera_id}" if self.name == "dshow" else camera_id

    def _devices(self) -> list[_Device]:
        if self.name == "dshow":
            devices = av.enumerate_input_devices("dshow")
            for device in devices:
                log.debug(
                    "DirectShow lists %r: media types %s, id %s",
                    device.description, ", ".join(device.media_types) or "unknown", device.name,
                )  # fmt: skip
            # FFmpeg knows a device's media types only if it could inspect the device; one it
            # could not inspect may still be a camera, so its modes are asked for too.
            return [
                _Device(device.name, device.description or device.name, "video" in device.media_types)
                for device in devices
                if "video" in device.media_types or not device.media_types
            ]
        text = capture_ffmpeg_log("", "avfoundation", {"list_devices": "true"})
        log.debug("AVFoundation's device list:\n%s", text.rstrip())
        return [_Device(index, name, video=True) for index, name in parse_avfoundation_devices(text)]

    def _modes(self, camera_id: str) -> list[CameraMode]:
        if self.name == "dshow":
            text = capture_ffmpeg_log(self._url(camera_id), "dshow", {"list_options": "true"})
            modes = parse_dshow_modes(text)
        else:
            # Asking for an impossible size makes AVFoundation log the sizes it supports.
            text = capture_ffmpeg_log(camera_id, "avfoundation", {"video_size": "1x1"})
            modes = parse_avfoundation_modes(text)
        log.debug("Modes of %s, as FFmpeg lists them:\n%s", camera_id, text.rstrip() or "(none)")
        return modes


class FfmpegStream:
    """An open FFmpeg camera input."""

    def __init__(self, container: av.container.InputContainer, requested: CameraMode) -> None:
        self._container = container
        stream = container.streams.video[0]
        context = stream.codec_context
        jpeg = context.name == JPEG
        rate = stream.average_rate or stream.guessed_rate
        self._mode = CameraMode(
            context.width or requested.width,
            context.height or requested.height,
            float(rate) if rate else requested.fps,
            JPEG if jpeg else RAW,
            None if jpeg or context.format is None else context.format.name,
        )
        self._packets: Iterator[av.Packet[av.VideoStream]] = container.demux(stream)
        self._closed = False

    @property
    def mode(self) -> CameraMode:
        return self._mode

    def read(self) -> Frame:
        try:
            for packet in self._packets:
                if packet.size:
                    return Frame(bytes(packet), self._mode)
        except av.FFmpegError as error:
            raise CameraError(f"the camera stopped delivering frames: {error}") from error
        raise CameraError("the camera stopped delivering frames")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._container.close()
