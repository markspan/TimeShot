"""Name templates, and the checks before recording."""

from __future__ import annotations

from pathlib import Path

from helpers import camera_setups, set_file

from timeshot.cameras.base import CameraMode
from timeshot.setup import Names, NameTemplates, check, expand, with_extension


def test_templates_fill_in_camera_number_and_session() -> None:
    """``{i}`` becomes the camera's number and ``{s}`` the session, as in TimeShot 1.x."""
    assert expand("Video_{i}_s{s}.avi", 2, "07") == "Video_2_s07.avi"
    assert camera_setups()[1].names("3") == Names("Camera 1", "Stream_1", "Video_1.avi")


def test_new_setups_choose_the_best_mode() -> None:
    """A camera without a chosen mode starts in its preferred one."""
    assert camera_setups()[0].mode == CameraMode(1920, 1080, 30.0)


def test_default_settings_pass(tmp_path: Path) -> None:
    """Cameras with the default templates can record together: their numbers keep them apart."""
    assert check(camera_setups(), "1", tmp_path) == []


def test_clashing_and_blank_names_are_problems(tmp_path: Path) -> None:
    """Two cameras may not share a stream or a file, and no name may be empty."""
    cameras = camera_setups(3)
    set_file(cameras[1], "Video_0.avi")  # the file of camera 0
    cameras[2].templates = NameTemplates(name="", stream="Stream_0")  # the stream of camera 0
    problems = check(cameras, "1", tmp_path)
    messages = {(p.camera, p.message) for p in problems}
    assert ("Camera model 0", "Another camera has the same file name (video_0.avi).") in messages
    assert ("Camera model 1", "Another camera has the same file name (video_0.avi).") in messages
    assert ("Camera model 2", "Another camera has the same stream name (Stream_0).") in messages
    assert ("Camera model 2", "The camera name is empty.") in messages
    assert not any(p.confirm for p in problems)


def test_unusable_file_names_are_problems(tmp_path: Path) -> None:
    """A file name needs a video extension, and no folder or forbidden character."""
    cameras = camera_setups(3)
    set_file(cameras[0], "Video_{i}.mov")
    set_file(cameras[1], "sub/Video_{i}.avi")
    set_file(cameras[2], "")
    messages = [p.message for p in check(cameras, "1", tmp_path)]
    assert any("must end in .avi, .mkv or .mp4" in m for m in messages)
    assert any("not allowed in file names" in m for m in messages)
    assert "The file name is empty." in messages


def test_existing_files_only_need_confirming(tmp_path: Path) -> None:
    """Overwriting a recording is allowed, once confirmed."""
    (tmp_path / "Video_0.avi").write_bytes(b"old")
    problems = check(camera_setups(), "1", tmp_path)
    assert [(p.camera, p.confirm) for p in problems] == [("Camera model 0", True)]


def test_disabled_cameras_are_ignored_but_one_is_needed(tmp_path: Path) -> None:
    """Only the cameras that record count, and at least one must record."""
    cameras = camera_setups()
    set_file(cameras[1], "Video_0.avi")
    cameras[1].enabled = False
    assert check(cameras, "1", tmp_path) == []
    cameras[0].enabled = False
    assert [p.message for p in check(cameras, "1", tmp_path)] == ["Select at least one camera."]


def test_a_file_template_gets_another_video_extension() -> None:
    """Choosing a format replaces a video extension, and keeps anything else."""
    assert with_extension("Video_{i}.avi", ".mp4") == "Video_{i}.mp4"
    assert with_extension("Face", ".mkv") == "Face.mkv"
    assert with_extension("lab.v2_{i}", ".mp4") == "lab.v2_{i}.mp4", "only a video extension is replaced"
    assert NameTemplates().with_extension(".mkv") == NameTemplates(file="Video_{i}.mkv")
