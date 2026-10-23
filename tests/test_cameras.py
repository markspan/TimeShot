"""Camera modes, mode lists parsed from FFmpeg's logs, and the synthetic cameras."""

from __future__ import annotations

import time

import av
import numpy as np
import pytest

from timeshot.cameras.base import (
    JPEG,
    RAW,
    CameraInfo,
    CameraMode,
    Frame,
    frame_rates,
    preferred_mode,
    with_keys,
)
from timeshot.cameras.ffmpeg import parse_avfoundation_devices, parse_avfoundation_modes, parse_dshow_modes
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
    assert CameraMode.parse(mode.key()) == mode


def test_mode_parse_defaults_to_jpeg() -> None:
    assert CameraMode.parse("1280x720@25") == CameraMode(1280, 720, 25.0, JPEG)


@pytest.mark.parametrize("text", ["1280x720", "x@30", "640x480@30:h264", "640x480@30:"])
def test_mode_parse_rejects_nonsense(text: str) -> None:
    with pytest.raises(ValueError, match=r"mode|format"):
        CameraMode.parse(text)


def test_preferred_mode_favours_jpeg_then_size_then_rate() -> None:
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
    assert frame_rates(5, 30) == [30.0, 25.0, 24.0, 20.0, 15.0, 10.0, 5.0]
    assert frame_rates(29.97, 29.97) == [29.97]


def test_identical_cameras_get_numbered_keys() -> None:
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
    modes = parse_dshow_modes(DSHOW_LOG)
    assert CameraMode(1920, 1080, 30.0) in modes
    assert CameraMode(1920, 1080, 25.0) in modes
    assert CameraMode(1280, 720, 60.0) in modes
    assert CameraMode(640, 480, 30.0, RAW, "yuyv422") in modes
    assert all(m.codec in (JPEG, RAW) for m in modes), "h264 and unknown formats are left out"
    assert len(modes) == len(set(modes))


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
    assert parse_avfoundation_devices(AVFOUNDATION_DEVICES_LOG) == [
        ("0", "FaceTime HD Camera"),
        ("1", "Logitech BRIO"),
    ]


def test_avfoundation_modes_are_raw_with_standard_rates() -> None:
    modes = parse_avfoundation_modes(AVFOUNDATION_MODES_LOG)
    assert CameraMode(1280, 720, 60.0, RAW) in modes
    assert CameraMode(1920, 1080, 25.0, RAW) in modes
    assert not any(m.is_jpeg for m in modes)
    assert all(m.pixel_format is None for m in modes), "decided when the camera is opened"
    assert len(modes) == len(set(modes))


# ── synthetic cameras ────────────────────────────────────────────────────────


def test_sequence_numbers_survive_jpeg() -> None:
    rgb = np.full((240, 320, 3), 90, np.uint8)
    draw_sequence_number(rgb, 0xBEEF)
    jpeg = encode_jpeg(jpeg_encoder(320, 240, quality=8), av.VideoFrame.from_ndarray(rgb, format="rgb24"))
    mode = CameraMode(320, 240, 30.0)
    picture = decode(decoder_for(mode), Frame(jpeg, mode))
    assert read_sequence_number(to_rgb(picture)) == 0xBEEF


def test_synthetic_camera_delivers_numbered_frames_at_its_rate() -> None:
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
    stream = SyntheticBackend(1).open("0", CameraMode(640, 480, 30.0, RAW, "yuyv422"))
    try:
        assert len(stream.read().data) == 640 * 480 * 2
    finally:
        stream.close()
