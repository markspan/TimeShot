"""Name templates, the checks before recording, and remembered settings."""

from __future__ import annotations

from pathlib import Path

from timeshot.cameras.base import RAW, CameraInfo, CameraMode
from timeshot.session import CameraSetup, check, expand
from timeshot.settings import CameraSettings, Settings

MODES = (CameraMode(1920, 1080, 30.0), CameraMode(640, 480, 30.0, RAW, "yuyv422"))


def setups(count: int = 2) -> list[CameraSetup]:
    cameras = [
        CameraInfo(str(i), f"Camera model {i}", MODES, "synthetic", f"Camera model {i}") for i in range(count)
    ]
    return [CameraSetup(camera, index) for index, camera in enumerate(cameras)]


def test_templates_fill_in_camera_number_and_session() -> None:
    assert expand("Video_{i}_s{s}.avi", 2, "07") == "Video_2_s07.avi"
    assert setups()[1].names("3") == ("Camera 1", "Stream_1", "Video_1.avi")


def test_new_setups_choose_the_best_mode() -> None:
    assert setups()[0].mode == CameraMode(1920, 1080, 30.0)


def test_default_settings_pass(tmp_path: Path) -> None:
    assert check(setups(), "1", tmp_path) == []


def test_clashing_and_blank_names_are_problems(tmp_path: Path) -> None:
    cameras = setups(3)
    cameras[1].file = "Video_0.avi"  # same file as camera 0
    cameras[2].stream = "Stream_0"  # same stream as camera 0
    cameras[2].name = ""
    problems = check(cameras, "1", tmp_path)
    messages = {(p.camera, p.message) for p in problems}
    assert ("Camera model 0", "Another camera has the same file name (video_0.avi).") in messages
    assert ("Camera model 1", "Another camera has the same file name (video_0.avi).") in messages
    assert ("Camera model 2", "Another camera has the same stream name (Stream_0).") in messages
    assert ("Camera model 2", "The camera name is empty.") in messages
    assert not any(p.confirm for p in problems)


def test_unusable_file_names_are_problems(tmp_path: Path) -> None:
    cameras = setups(3)
    cameras[0].file = "Video_{i}.mp4"
    cameras[1].file = "sub/Video_{i}.avi"
    cameras[2].file = ""
    messages = [p.message for p in check(cameras, "1", tmp_path)]
    assert any("must end in .avi or .mkv" in m for m in messages)
    assert any("not allowed in file names" in m for m in messages)
    assert "The file name is empty." in messages


def test_existing_files_only_need_confirming(tmp_path: Path) -> None:
    (tmp_path / "Video_0.avi").write_bytes(b"old")
    problems = check(setups(), "1", tmp_path)
    assert [(p.camera, p.confirm) for p in problems] == [("Camera model 0", True)]


def test_disabled_cameras_are_ignored_but_one_is_needed(tmp_path: Path) -> None:
    cameras = setups()
    cameras[1].file = "Video_0.avi"
    cameras[1].enabled = False
    assert check(cameras, "1", tmp_path) == []
    cameras[0].enabled = False
    assert [p.message for p in check(cameras, "1", tmp_path)] == ["Select at least one camera."]


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings(session="12", folder=str(tmp_path), wait_for_labrecorder=False, frame_numbers=True)
    cameras = setups()
    cameras[0].enabled = False
    cameras[1].mode = MODES[1]
    cameras[1].file = "Face_{s}.mkv"
    settings.remember(cameras)
    settings.save(path)

    loaded = Settings.load(path)
    assert (loaded.session, loaded.wait_for_labrecorder, loaded.frame_numbers) == ("12", False, True)
    restored = loaded.setups([s.camera for s in cameras])
    assert not restored[0].enabled
    assert restored[1].mode == MODES[1]
    assert restored[1].names("12")[2] == "Face_12.mkv"


def test_a_remembered_mode_the_camera_no_longer_offers_is_replaced(tmp_path: Path) -> None:
    settings = Settings(cameras={"Camera model 0": CameraSettings(mode="320x240@30:mjpeg")})
    assert settings.setups([setups(1)[0].camera])[0].mode == MODES[0]


def test_unreadable_settings_fall_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert Settings.load(path) == Settings()
    path.write_text("{ not json", encoding="utf-8")
    assert Settings.load(path) == Settings()
    path.write_text(
        '{"session": "5", "unknown": 1, "cameras": {"A": {"enabled": false, "extra": 2}}}', encoding="utf-8"
    )
    loaded = Settings.load(path)
    assert loaded.session == "5"
    assert not loaded.cameras["A"].enabled
