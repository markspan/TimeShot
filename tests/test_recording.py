"""The parts of a camera's recording: frame label, video writer, preview slot and recorder."""

from __future__ import annotations

import itertools
import multiprocessing
import time
from pathlib import Path

import av
import numpy as np
import pytest
from helpers import (
    assert_frames_match_markers,
    connect_inlet,
    file_sequence_numbers,
    pull_markers,
    wait_until,
)

from timeshot.cameras.base import RAW, CameraMode, Frame
from timeshot.cameras.synthetic import SyntheticBackend, SyntheticStream
from timeshot.recording.label import COLOR, draw_frame_number
from timeshot.recording.preview import MAX_HEIGHT, MAX_WIDTH, PreviewSlot, preview_size
from timeshot.recording.recorder import CameraRecorder, PreviewMaker
from timeshot.recording.writer import VideoWriter

MODE = CameraMode(640, 480, 30.0)
RAW_MODE = CameraMode(640, 480, 30.0, RAW, "yuyv422")


def synthetic_frames(mode: CameraMode, count: int) -> list[Frame]:
    """Frames numbered 0, 1, 2, ... (unpaced: these tests are about the writer, not timing)."""
    stream = SyntheticStream(mode, paced=False)
    try:
        return [stream.read() for _ in range(count)]
    finally:
        stream.close()


# ── frame label ──────────────────────────────────────────────────────────────


def test_frame_number_is_drawn_top_left() -> None:
    rgb = np.zeros((720, 1280, 3), np.uint8)
    draw_frame_number(rgb, 1234)
    painted = np.all(rgb == COLOR, axis=2)
    rows, columns = np.nonzero(painted)
    assert painted.sum() > 500
    assert rows.max() < 100, "within the top-left corner"
    assert columns.max() < 300


def test_different_numbers_look_different_and_tiny_images_survive() -> None:
    images = []
    for number in (1, 7):
        rgb = np.zeros((480, 640, 3), np.uint8)
        draw_frame_number(rgb, number)
        images.append(rgb)
    assert not np.array_equal(images[0], images[1])
    draw_frame_number(np.zeros((8, 8, 3), np.uint8), 123456789)  # clipped, no error


# ── video writer ─────────────────────────────────────────────────────────────


def test_camera_jpeg_frames_are_written_untouched(tmp_path: Path) -> None:
    frames = synthetic_frames(MODE, 20)
    writer = VideoWriter(tmp_path / "out.avi", MODE)
    assert writer.passthrough
    for index, frame in enumerate(frames):
        writer.add(index, frame)
    result = writer.close()
    assert (result.frames_written, result.error) == (20, None)
    with av.open(str(tmp_path / "out.avi")) as container:
        written = [bytes(p) for p in container.demux(video=0) if p.size]
    assert written == [f.data for f in frames]


@pytest.mark.parametrize("suffix", [".avi", ".mkv"])
def test_raw_frames_are_encoded_in_order(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"out{suffix}"
    writer = VideoWriter(path, RAW_MODE)
    assert not writer.passthrough
    for index, frame in enumerate(synthetic_frames(RAW_MODE, 15)):
        writer.add(index, frame)
    assert writer.close().frames_written == 15
    assert file_sequence_numbers(path) == list(range(15))


def test_frame_numbers_are_burned_in_when_asked(tmp_path: Path) -> None:
    path = tmp_path / "numbered.avi"
    writer = VideoWriter(path, MODE, frame_numbers=True)
    assert not writer.passthrough
    for index, frame in enumerate(synthetic_frames(MODE, 3)):
        writer.add(index, frame)
    writer.close()
    with av.open(str(path)) as container:
        picture = next(container.decode(video=0)).to_ndarray(format="rgb24")
    corner = picture[:60, :60].astype(int)
    reddish = (corner[..., 0] > 150) & (corner[..., 1] < 110) & (corner[..., 2] < 110)
    assert reddish.sum() > 50


def test_a_damaged_frame_is_replaced_by_the_previous_one(tmp_path: Path) -> None:
    frames = synthetic_frames(MODE, 5)
    frames[2] = Frame(b"\xff\xd8 not a JPEG \xff\xd9", MODE)
    path = tmp_path / "damaged.avi"
    writer = VideoWriter(path, MODE, frame_numbers=True)  # decodes, so it notices
    for index, frame in enumerate(frames):
        writer.add(index, frame)
    result = writer.close()
    assert result.frames_written == 5, "later frames keep their index"
    assert file_sequence_numbers(path) == [0, 1, 1, 3, 4]


def test_unsupported_file_types_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported video file type"):
        VideoWriter(tmp_path / "out.mp4", MODE)
    assert not (tmp_path / "out.mp4").exists()


# ── preview slot ─────────────────────────────────────────────────────────────


def test_preview_slot_hands_over_the_newest_image() -> None:
    slot = PreviewSlot.create(multiprocessing.get_context("spawn"))
    try:
        assert slot.read() is None
        image = np.random.default_rng(1).integers(0, 255, (360, 480, 3), dtype=np.uint8)
        slot.write(image)
        read = slot.read()
        assert read is not None
        assert np.array_equal(read, image)
        assert slot.read() is None, "nothing newer"
        with pytest.raises(ValueError, match="exceeds"):
            slot.write(np.zeros((MAX_HEIGHT + 2, MAX_WIDTH, 3), np.uint8))
    finally:
        slot.close()


def test_preview_size_fits_the_box_and_keeps_the_aspect_ratio() -> None:
    assert preview_size(1920, 1080) == (640, 360)
    assert preview_size(640, 480) == (480, 360)
    assert preview_size(320, 240) == (320, 240)


# ── recorder ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("mode", "frame_numbers"), [(MODE, False), (RAW_MODE, True)])
def test_recorder_sends_one_marker_per_written_frame(
    tmp_path: Path, stream_name: str, mode: CameraMode, frame_numbers: bool
) -> None:
    recorder = CameraRecorder(SyntheticBackend(1).open("0", mode), camera_name="Synthetic")
    recorder.start()
    try:
        recorder.open_stream(stream_name, "out.avi")
        inlet = connect_inlet(stream_name)
        assert wait_until(lambda: recorder.stats().consumers)
        recorder.start_recording(tmp_path / "out.avi", frame_numbers=frame_numbers)
        time.sleep(1.5)
        result = recorder.stop_recording()
        markers = pull_markers(inlet)
    finally:
        recorder.close()
    assert result.ok
    assert result.markers_sent > 20
    assert_frames_match_markers(tmp_path / "out.avi", [index for index, _ in markers])
    stamps = [stamp for _, stamp in markers]
    assert all(b > a for a, b in itertools.pairwise(stamps)), "LSL stamps rise with the frames"


def test_recording_needs_an_open_stream_and_creates_no_file_otherwise(tmp_path: Path) -> None:
    recorder = CameraRecorder(SyntheticBackend(1).open("0", MODE), camera_name="Synthetic")
    recorder.start()
    try:
        with pytest.raises(RuntimeError, match="open the stream"):
            recorder.start_recording(tmp_path / "out.avi")
        assert not (tmp_path / "out.avi").exists()
        with pytest.raises(RuntimeError, match="not recording"):
            recorder.stop_recording()
    finally:
        recorder.close()
    recorder.close()  # idempotent


def test_preview_maker_publishes_small_images() -> None:
    recorder = CameraRecorder(
        SyntheticBackend(1).open("0", CameraMode(1280, 720, 30.0)), camera_name="Synthetic"
    )
    images: list[np.ndarray] = []
    previews = PreviewMaker(recorder, images.append)
    recorder.start()
    previews.start()
    try:
        assert wait_until(lambda: len(images) >= 3, timeout=10)
    finally:
        previews.stop()
        recorder.close()
    assert images[-1].shape == (360, 640, 3)
