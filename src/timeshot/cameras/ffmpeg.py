"""Cameras through FFmpeg's device inputs (PyAV): DirectShow on Windows, AVFoundation on macOS.

FFmpeg lists a camera's modes only in its log, and lists the devices most reliably there too,
so both backends capture that log (:func:`capture_ffmpeg_log`) and read it with the
``parse_*`` functions, which are kept apart so that tests can check them against recorded
output. :class:`FfmpegBackend` holds what the two inputs share; :class:`DshowBackend` and
:class:`AvfoundationBackend` hold their differences.

DirectShow delivers a camera's JPEG frames untouched. AVFoundation always delivers decoded
(raw) frames, which TimeShot then encodes.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import NamedTuple

import av
import av.logging

from timeshot.cameras.base import JPEG, RAW, CameraError, CameraInfo, CameraMode, Frame, frame_rates

log = logging.getLogger(__name__)


# ── FFmpeg's log ─────────────────────────────────────────────────────────────

# FFmpeg's log is process-wide, so it is captured for one request at a time.
_log_lock = threading.Lock()


def capture_ffmpeg_log(url: str, input_format: str, options: dict[str, str]) -> str:
    """Opens an FFmpeg input only for what FFmpeg logs while doing so.

    That is how FFmpeg's device inputs answer listing requests (``list_devices``,
    ``list_options``): they log the answer, then fail to open with an "exit" error.

    Args:
        url: The input to open, e.g. "video=Elgato Facecam MK.2".
        input_format: FFmpeg's name of the input, e.g. "dshow".
        options: The input's options, e.g. ``{"list_options": "true"}``.

    Returns:
        The log, as one text. FFmpeg logs a line in several pieces, which are joined here.
    """
    with _log_lock:
        level, skip_repeated = av.logging.get_level(), av.logging.get_skip_repeated()
        av.logging.set_level(av.logging.INFO)
        av.logging.set_skip_repeated(False)  # mode lines may repeat word for word
        try:
            # The listing requests end in an error: expected, so not raised.
            with av.logging.Capture() as messages, contextlib.suppress(av.FFmpegError):
                av.open(url, format=input_format, options=options).close()
        finally:
            av.logging.set_level(level)
            av.logging.set_skip_repeated(skip_repeated)
    return "".join(message for _level, _name, message in messages)


# ── what the inputs share ────────────────────────────────────────────────────


class _Device(NamedTuple):
    """A device as FFmpeg lists it."""

    id: str  # what FFmpeg opens it by
    name: str
    video: bool  # known to deliver video; False: unknown (FFmpeg could not tell)


class FfmpegBackend(ABC):
    """The cameras of one of FFmpeg's device inputs; a subclass per input supplies its quirks."""

    name: str  # FFmpeg's name of the input, which is also the backend's name

    def __init__(self) -> None:
        """Checks that PyAV's FFmpeg has this backend's input.

        Raises:
            CameraError: It does not (PyAV's wheels have each platform's own input only).
        """
        if self.name not in av.formats_available:
            raise CameraError(f"this FFmpeg build has no {self.name} input")

    def list_cameras(self) -> list[CameraInfo]:
        """The devices that offer at least one mode TimeShot can record.

        Returns:
            The cameras, in the order FFmpeg lists them. A device that delivers video but
            offers no usable mode is left out with a warning in the log.
        """
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
        """Opens a camera.

        Args:
            camera_id: The id :meth:`list_cameras` gave it.
            mode: The mode to open it in.

        Returns:
            The open camera.

        Raises:
            CameraError: FFmpeg could not open it in this mode (or at all).
        """
        options = {"video_size": mode.size_label, "framerate": f"{mode.fps:g}", **self._capture_options(mode)}
        try:
            container = av.open(self._url(camera_id), format=self.name, options=options)
        except av.FFmpegError as error:
            raise CameraError(f"could not open the camera in {mode}: {error}") from error
        return FfmpegStream(container, mode)

    def _ffmpeg_log(self, url: str, options: dict[str, str], what: str) -> str:
        """Captures what FFmpeg logs while opening ``url``, and passes it on to the debug log.

        The debug log is what ``timeshot devices --details`` shows: FFmpeg's own words about
        each device, to find out why a camera is missing.

        Args:
            url: The input to open.
            options: The input's options.
            what: What the log holds, for the debug log, e.g. "DirectShow's device list".

        Returns:
            FFmpeg's log (see :func:`capture_ffmpeg_log`).
        """
        text = capture_ffmpeg_log(url, self.name, options)
        log.debug("%s, as FFmpeg logs it:\n%s", what, text.rstrip() or "(nothing)")
        return text

    @abstractmethod
    def _devices(self) -> list[_Device]:
        """The devices FFmpeg lists that may be cameras."""

    @abstractmethod
    def _modes(self, camera_id: str) -> list[CameraMode]:
        """The modes TimeShot can record of the device with this id."""

    @abstractmethod
    def _url(self, camera_id: str) -> str:
        """The URL FFmpeg opens the device with this id by."""

    @abstractmethod
    def _capture_options(self, mode: CameraMode) -> dict[str, str]:
        """The input's options for capturing in ``mode``, besides its size and frame rate."""


class FfmpegStream:
    """An open camera of an FFmpeg device input."""

    def __init__(self, container: av.container.InputContainer, requested: CameraMode) -> None:
        """Takes over an opened input.

        Args:
            container: The opened camera input.
            requested: The mode it was opened in; where the input does not report a value
                (size, rate, format), this mode's applies.
        """
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
        """The mode the camera delivers in, as the input reports it."""
        return self._mode

    def read(self) -> Frame:
        """Waits for the camera's next frame.

        Returns:
            The frame, as the camera delivered it.

        Raises:
            CameraError: The camera stopped delivering frames.
        """
        try:
            for packet in self._packets:
                if packet.size:  # the demuxer ends with an empty packet
                    return Frame(bytes(packet), self._mode)
        except av.FFmpegError as error:
            raise CameraError(f"the camera stopped delivering frames: {error}") from error
        raise CameraError("the camera stopped delivering frames")

    def close(self) -> None:
        """Releases the camera. Safe to call more than once."""
        if not self._closed:
            self._closed = True
            self._container.close()


# ── DirectShow (Windows) ─────────────────────────────────────────────────────

# DirectShow buffers this much video when the reader is briefly late, instead of dropping frames.
DSHOW_BUFFER = "256M"

# Raw pixel formats TimeShot can decode; others are left out of the mode list.
SUPPORTED_RAW = {"yuyv422", "uyvy422", "nv12", "yuv420p", "yuvj420p", "rgb24", "bgr24", "bgr0", "0rgb"}

# '"Elgato Facecam MK.2" (video)', or '(none)' when FFmpeg could not inspect the device.
_DSHOW_DEVICE = re.compile(r'"(?P<name>.+)" \((?P<types>[^()]*)\)\s*$')
# '  Alternative name "@device_pnp_..."': the device's unique name, on the line after it.
_DSHOW_DEVICE_ID = re.compile(r'Alternative name "(?P<id>.+)"\s*$')
# 'vcodec=mjpeg  min s=1920x1080 fps=5 max s=1920x1080 fps=30', or 'pixel_format=yuyv422  ...'.
_DSHOW_MODE = re.compile(
    r"(?:vcodec=(?P<codec>\S+)|pixel_format=(?P<pix>\S+))\s+"
    r"min s=(?P<w0>\d+)x(?P<h0>\d+) fps=(?P<f0>[\d.e+]+)\s+"
    r"max s=(?P<w1>\d+)x(?P<h1>\d+) fps=(?P<f1>[\d.e+]+)"
)


def parse_dshow_devices(log_text: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """Reads the devices from DirectShow's log of ``list_devices``.

    Each device takes two lines: ``"Elgato Facecam MK.2" (video)`` and then
    ``Alternative name "@device_pnp_..."``, its unique name.

    Args:
        log_text: The log (see :func:`capture_ffmpeg_log`).

    Returns:
        ``(id, name, media types)`` of every device, video and audio alike. The media types
        are empty for a device FFmpeg could not inspect (it logs "none" for them).
    """
    devices = []
    pending: tuple[str, tuple[str, ...]] | None = None  # a device line still waiting for its id
    for line in log_text.splitlines():
        if match := _DSHOW_DEVICE.search(line):
            types = tuple(t for t in (part.strip() for part in match["types"].split(",")) if t != "none")
            pending = (match["name"], types)
        elif pending and (match := _DSHOW_DEVICE_ID.search(line)):
            devices.append((match["id"], *pending))
            pending = None
    return devices


def parse_dshow_modes(log_text: str) -> list[CameraMode]:
    """Reads the modes from DirectShow's log of opening a camera with ``list_options``.

    Args:
        log_text: The log (see :func:`capture_ffmpeg_log`), with lines such as
            ``vcodec=mjpeg  min s=1920x1080 fps=5 max s=1920x1080 fps=30`` or
            ``pixel_format=yuyv422  min s=640x480 fps=5 max s=640x480 fps=30``.

    Returns:
        Every JPEG and supported raw mode, once each, with the frame rates
        :func:`~timeshot.cameras.base.frame_rates` offers. Other compressed formats (such as
        H.264, which is not frame-exact) and unknown pixel formats are left out.
    """
    modes: dict[tuple[str, str | None, int, int, float], CameraMode] = {}
    for match in _DSHOW_MODE.finditer(log_text):
        if match["codec"]:
            if match["codec"] != JPEG:
                continue
            codec, pixel_format = JPEG, None
        else:
            if match["pix"] not in SUPPORTED_RAW:
                continue
            codec, pixel_format = RAW, match["pix"]
        width, height = int(match["w1"]), int(match["h1"])
        for fps in frame_rates(float(match["f0"]), float(match["f1"])):
            mode = CameraMode(width, height, fps, codec, pixel_format)
            modes.setdefault((codec, pixel_format, width, height, fps), mode)
    return list(modes.values())


class DshowBackend(FfmpegBackend):
    """The cameras of Windows, through FFmpeg's DirectShow input."""

    name = "dshow"

    def _devices(self) -> list[_Device]:
        """The video devices, and the devices FFmpeg could not inspect (they may be cameras)."""
        # From FFmpeg's log rather than av.enumerate_input_devices(): FFmpeg's device list API
        # fails as a whole when the computer has no audio capture device, losing the cameras.
        text = self._ffmpeg_log("dummy", {"list_devices": "true"}, "DirectShow's device list")
        return [
            _Device(device_id, name, "video" in types)
            for device_id, name, types in parse_dshow_devices(text)
            if "video" in types or not types
        ]

    def _modes(self, camera_id: str) -> list[CameraMode]:
        """The modes the camera logs when opened with ``list_options``."""
        text = self._ffmpeg_log(self._url(camera_id), {"list_options": "true"}, f"The modes of {camera_id}")
        return parse_dshow_modes(text)

    def _url(self, camera_id: str) -> str:
        """``video=`` and the device's unique name, which tells identical cameras apart."""
        return f"video={camera_id}"

    def _capture_options(self, mode: CameraMode) -> dict[str, str]:
        """A large buffer, and the frame format to deliver: JPEG, or a raw pixel format."""
        options = {"rtbufsize": DSHOW_BUFFER}
        if mode.is_jpeg:
            options["vcodec"] = JPEG
        elif mode.pixel_format:
            options["pixel_format"] = mode.pixel_format
        return options


# ── AVFoundation (macOS) ─────────────────────────────────────────────────────

# Requested from AVFoundation, which every Mac camera offers. Should one not, FFmpeg picks
# another supported format by itself.
AVF_PIXEL_FORMAT = "uyvy422"

# '[0] FaceTime HD Camera', anywhere in a line: FFmpeg may put "[AVFoundation indev @ 0x...]" before it.
_AVF_DEVICE = re.compile(r"\[(?P<index>\d+)\] (?P<name>.+?)\s*$")
# '1280x720@[1.000000 30.000000]fps': a size, and its range of frame rates.
_AVF_MODE = re.compile(r"(?P<w>\d+)x(?P<h>\d+)@\[(?P<f0>[\d.]+) (?P<f1>[\d.]+)\]fps")


def parse_avfoundation_devices(log_text: str) -> list[tuple[str, str]]:
    """Reads the video devices from AVFoundation's log of ``list_devices``.

    Args:
        log_text: The log (see :func:`capture_ffmpeg_log`): a list of video devices, then
            one of audio devices.

    Returns:
        ``(index, name)`` of every video device, less the screens (AVFoundation lists them
        as "Capture screen N").
    """
    devices = []
    in_video = False  # between the video devices' heading and the audio devices' one
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
    """Reads the modes from the list AVFoundation logs when a requested size is not supported.

    Args:
        log_text: The log (see :func:`capture_ffmpeg_log`), with lines such as
            ``1280x720@[1.000000 30.000000]fps``.

    Returns:
        Every size, once per frame rate :func:`~timeshot.cameras.base.frame_rates` offers,
        as raw modes without a pixel format (see :data:`AVF_PIXEL_FORMAT`).
    """
    modes: dict[tuple[int, int, float], CameraMode] = {}
    for match in _AVF_MODE.finditer(log_text):
        width, height = int(match["w"]), int(match["h"])
        for fps in frame_rates(float(match["f0"]), float(match["f1"])):
            modes.setdefault((width, height, fps), CameraMode(width, height, fps, RAW, None))
    return list(modes.values())


class AvfoundationBackend(FfmpegBackend):
    """The cameras of macOS, through FFmpeg's AVFoundation input."""

    name = "avfoundation"

    def _devices(self) -> list[_Device]:
        """The video devices, less the screens."""
        text = self._ffmpeg_log("", {"list_devices": "true"}, "AVFoundation's device list")
        return [_Device(index, name, video=True) for index, name in parse_avfoundation_devices(text)]

    def _modes(self, camera_id: str) -> list[CameraMode]:
        """The sizes AVFoundation logs when asked for one the camera cannot deliver."""
        text = self._ffmpeg_log(
            self._url(camera_id), {"video_size": "1x1"}, f"The modes of camera {camera_id}"
        )
        return parse_avfoundation_modes(text)

    def _url(self, camera_id: str) -> str:
        """The camera's index (names repeat for identical cameras), with no audio device."""
        return camera_id

    def _capture_options(self, mode: CameraMode) -> dict[str, str]:
        """The pixel format to deliver: the mode's own, else :data:`AVF_PIXEL_FORMAT`."""
        return {"pixel_format": mode.pixel_format or AVF_PIXEL_FORMAT}
