"""Camera modes, the device and mode lists read from FFmpeg's logs, and the synthetic cameras."""

from __future__ import annotations

import logging
import sys
import time
from fractions import Fraction

import av
import numpy as np
import pytest

from timeshot.cameras import ffmpeg
from timeshot.cameras.base import (
    JPEG,
    NO_USABLE_MODE,
    RAW,
    CameraInfo,
    CameraMode,
    Frame,
    frame_rates,
    preferred_mode,
    with_keys,
)
from timeshot.cameras.ffmpeg import (
    parse_avfoundation_devices,
    parse_avfoundation_modes,
    parse_dshow_devices,
    parse_dshow_modes,
)
from timeshot.cameras.synthetic import SyntheticBackend, draw_sequence_number, read_sequence_number
from timeshot.frames import decode, decoder_for, encode_jpeg, jpeg_encoder, to_rgb

# ── modes ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mode",
    [
        CameraMode(1920, 1080, 30.0),
        CameraMode(640, 480, 29.97, RAW, "yuyv422"),
        CameraMode(1280, 720, 60.0, RAW),
    ],
)
def test_mode_key_round_trip(mode: CameraMode) -> None:
    """A mode's text form, as the settings file and the command line hold it, reads back the same."""
    assert CameraMode.parse(mode.key()) == mode


def test_mode_parse_defaults_to_jpeg() -> None:
    """A mode typed without a frame format is JPEG, the format recorded untouched."""
    assert CameraMode.parse("1280x720@25") == CameraMode(1280, 720, 25.0, JPEG)


@pytest.mark.parametrize("text", ["1280x720", "x@30", "640x480@30:h264", "640x480@30:"])
def test_mode_parse_rejects_nonsense(text: str) -> None:
    """Incomplete modes and unknown frame formats are refused, saying what is wrong."""
    with pytest.raises(ValueError, match=r"mode|format"):
        CameraMode.parse(text)


def test_mode_rate_is_exact() -> None:
    """The frame rate as FFmpeg's time bases take it: a fraction, exact for NTSC rates."""
    assert CameraMode(640, 480, 30.0).rate == 30
    assert CameraMode(640, 480, 29.97).rate == Fraction(2997, 100)


def test_preferred_mode_favours_jpeg_then_size_then_rate() -> None:
    """A new camera starts in its largest JPEG mode of at least 25 fps."""
    modes = [
        CameraMode(1920, 1080, 30.0, RAW, "yuyv422"),
        CameraMode(1280, 720, 30.0),
        CameraMode(1920, 1080, 5.0),
        CameraMode(1920, 1080, 30.0),
        CameraMode(1920, 1080, 15.0),
    ]
    assert preferred_mode(modes) == CameraMode(1920, 1080, 30.0)
    assert preferred_mode([]) is None


def test_frame_rates_offer_the_maximum_and_standard_rates_within_range() -> None:
    """A camera's range of rates becomes its maximum plus the standard rates within it."""
    assert frame_rates(5, 30) == [30.0, 25.0, 24.0, 20.0, 15.0, 10.0, 5.0]
    assert frame_rates(29.97, 29.97) == [29.97]


def test_identical_cameras_get_numbered_keys() -> None:
    """Identical cameras keep their settings apart: the second of a name gets " #2"."""
    cameras = [CameraInfo(str(i), name, (), "x") for i, name in enumerate(["C920", "BRIO", "C920"])]
    assert [c.key for c in with_keys(cameras)] == ["C920", "BRIO", "C920 #2"]


# ── FFmpeg logs ──────────────────────────────────────────────────────────────

# As FFmpeg's DirectShow input logs a camera's options; each line arrives in several pieces.
DSHOW_LOG = "".join(
    [
        "DirectShow video device options (from video devices)\n",
        ' Pin "Capture" (alternative pin name "0")\n',
        "  ", "vcodec=mjpeg", "  min s=1920x1080 fps=5 max s=1920x1080 fps=30",
        " (tv, bt470bg/bt709/unknown, topleft)\n",
        "  ", "vcodec=mjpeg", "  min s=1280x720 fps=5 max s=1280x720 fps=60", "\n",
        "  ", "pixel_format=yuyv422", "  min s=640x480 fps=5 max s=640x480 fps=30", "\n",
        "  ", "vcodec=h264", "  min s=1920x1080 fps=5 max s=1920x1080 fps=30", "\n",
        "  ", "unknown compression type 0x3231564E", "  min s=640x480 fps=5 max s=640x480 fps=30", "\n",
    ]
)  # fmt: skip


def test_dshow_modes() -> None:
    """DirectShow's mode list gives JPEG and known raw modes, each frame rate once."""
    modes = parse_dshow_modes(DSHOW_LOG)
    assert CameraMode(1920, 1080, 30.0) in modes
    assert CameraMode(1920, 1080, 25.0) in modes
    assert CameraMode(1280, 720, 60.0) in modes
    assert CameraMode(640, 480, 30.0, RAW, "yuyv422") in modes
    assert all(m.codec in (JPEG, RAW) for m in modes), "h264 and unknown formats are left out"
    assert len(modes) == len(set(modes))


# As FFmpeg's DirectShow input logs its device list: first the video devices...
DSHOW_CAMERAS_LOG = "".join(
    [
        '"Elgato Facecam MK.2"', " (none)", "\n",  # FFmpeg could not inspect it
        '  Alternative name "@device_pnp_\\\\?\\usb#vid_0fd9&pid_0093&mi_00#7&2b#{65e8773d}\\global"\n',
        '"USB Camera (1080p)"', " (video", ")", "\n",
        '  Alternative name "@device_pnp_usbcam"\n',
        '"Broken camera"', " (video", ")", "\n",
        '  Alternative name "@device_pnp_broken"\n',
    ]
)  # fmt: skip
# ... then the audio devices,
DSHOW_DEVICES_LOG = (
    DSHOW_CAMERAS_LOG + '"Headset" (audio)\n  Alternative name "@device_cm_{33D9A762}\\wave_{0F2A}"\n'
)
# or, on a computer without a microphone, an error.
DSHOW_NO_MICROPHONE_LOG = DSHOW_CAMERAS_LOG + "Could not enumerate audio only devices (or none found).\n"


def test_dshow_devices() -> None:
    """DirectShow's device list is read alike with or without a microphone on the computer."""
    cameras = [
        ("@device_pnp_\\\\?\\usb#vid_0fd9&pid_0093&mi_00#7&2b#{65e8773d}\\global", "Elgato Facecam MK.2", ()),
        ("@device_pnp_usbcam", "USB Camera (1080p)", ("video",)),
        ("@device_pnp_broken", "Broken camera", ("video",)),
    ]
    assert parse_dshow_devices(DSHOW_NO_MICROPHONE_LOG) == cameras
    assert parse_dshow_devices(DSHOW_DEVICES_LOG) == [
        *cameras,
        ("@device_cm_{33D9A762}\\wave_{0F2A}", "Headset", ("audio",)),
    ]


def test_dshow_also_tries_devices_whose_media_types_ffmpeg_could_not_tell(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A device FFmpeg could not inspect (as the Elgato Facecam MK.2) is still asked for modes."""
    facecam, usb_camera = (
        device_id for device_id, _name, _types in parse_dshow_devices(DSHOW_DEVICES_LOG)[:2]
    )
    asked: list[str] = []

    def ffmpeg_log(url: str, input_format: str, options: dict[str, str]) -> str:
        """Plays FFmpeg: the device list, a camera's modes, or a device without video."""
        if "list_devices" in options:
            return DSHOW_DEVICES_LOG
        asked.append(url)
        return (
            DSHOW_LOG
            if url in (f"video={facecam}", f"video={usb_camera}")
            else "Could not find any video pin\n"
        )

    monkeypatch.setattr(av, "formats_available", {*av.formats_available, "dshow"})
    monkeypatch.setattr(ffmpeg, "capture_ffmpeg_log", ffmpeg_log)
    with caplog.at_level(logging.DEBUG, logger="timeshot.cameras"):
        cameras = ffmpeg.DshowBackend().list_cameras()

    assert [c.name for c in cameras] == ["Elgato Facecam MK.2", "USB Camera (1080p)"]
    assert not any("wave_" in url for url in asked), "audio devices are not asked for video modes"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [NO_USABLE_MODE % "Broken camera"], "the only camera without a mode, apologized for"
    assert any("Could not find any video pin" in r.getMessage() for r in caplog.records), "FFmpeg's own words"


@pytest.mark.skipif(sys.platform != "win32", reason="DirectShow")
def test_dshow_listing_works_without_cameras_or_microphones() -> None:
    """The CI machine has neither: FFmpeg's device list API fails there, the logged list not."""
    assert isinstance(ffmpeg.DshowBackend().list_cameras(), list)


AVFOUNDATION_DEVICES_LOG = """AVFoundation indev @ 0x7f8: AVFoundation video devices:
[0] FaceTime HD Camera
[1] Logitech BRIO
[2] Capture screen 0
AVFoundation audio devices:
[0] MacBook Pro Microphone
"""

AVFOUNDATION_MODES_LOG = """Selected video size (1x1) is not supported by the device.
Supported modes:
  1920x1080@[1.000000 30.000000]fps
  1280x720@[1.000000 60.000000]fps
  640x480@[1.000000 30.000000]fps
  640x480@[1.000000 30.000000]fps
"""


def test_avfoundation_devices_leave_out_screens_and_audio() -> None:
    """Of AVFoundation's device list, only the cameras remain."""
    assert parse_avfoundation_devices(AVFOUNDATION_DEVICES_LOG) == [
        ("0", "FaceTime HD Camera"),
        ("1", "Logitech BRIO"),
    ]


def test_avfoundation_modes_are_raw_with_standard_rates() -> None:
    """AVFoundation's modes are raw, each size with the standard rates in its range."""
    modes = parse_avfoundation_modes(AVFOUNDATION_MODES_LOG)
    assert CameraMode(1280, 720, 60.0, RAW) in modes
    assert CameraMode(1920, 1080, 25.0, RAW) in modes
    assert not any(m.is_jpeg for m in modes)
    assert all(m.pixel_format is None for m in modes), "decided when the camera is opened"
    assert len(modes) == len(set(modes))


# ── synthetic cameras ────────────────────────────────────────────────────────


def test_sequence_numbers_survive_jpeg() -> None:
    """The number a synthetic frame carries reads back after JPEG encoding."""
    rgb = np.full((240, 320, 3), 90, np.uint8)
    draw_sequence_number(rgb, 0xBEEF)
    jpeg = encode_jpeg(jpeg_encoder(320, 240, quality=8), av.VideoFrame.from_ndarray(rgb, format="rgb24"))
    mode = CameraMode(320, 240, 30.0)
    picture = decode(decoder_for(mode), Frame(jpeg, mode))
    assert read_sequence_number(to_rgb(picture)) == 0xBEEF


def test_synthetic_camera_delivers_numbered_frames_at_its_rate() -> None:
    """A synthetic camera is paced like a real one, delivering JPEG frames."""
    stream = SyntheticBackend(1).open("0", CameraMode(640, 480, 30.0))
    try:
        start = time.perf_counter()
        frames = [stream.read() for _ in range(15)]
        elapsed = time.perf_counter() - start
    finally:
        stream.close()
    assert all(f.data[:2] == b"\xff\xd8" for f in frames), "JPEG frames"
    assert 0.3 < elapsed < 1.5  # 14 intervals of 1/30 s, give or take a busy test machine


def test_synthetic_raw_frames_are_packed() -> None:
    """Synthetic raw frames have no row padding, as a camera's raw frames."""
    stream = SyntheticBackend(1).open("0", CameraMode(640, 480, 30.0, RAW, "yuyv422"))
    try:
        assert len(stream.read().data) == 640 * 480 * 2
    finally:
        stream.close()
