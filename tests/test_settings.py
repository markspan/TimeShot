"""The settings remembered between runs, in their JSON file."""

from __future__ import annotations

import json
from pathlib import Path

from helpers import MODES, camera_setups, set_file

from timeshot.settings import CameraSettings, Settings
from timeshot.setup import NameTemplates


def test_settings_round_trip(tmp_path: Path) -> None:
    """What is saved comes back: the options, and each camera's settings."""
    path = tmp_path / "settings.json"
    settings = Settings(session="12", folder=str(tmp_path), wait_for_labrecorder=False, frame_numbers=True)
    cameras = camera_setups()
    cameras[0].enabled = False
    cameras[1].mode = MODES[1]
    set_file(cameras[1], "Face_{s}.mkv")
    cameras[1].number = 7
    settings.remember(cameras)
    settings.save(path)

    loaded = Settings.load(path)
    assert (loaded.session, loaded.wait_for_labrecorder, loaded.frame_numbers) == ("12", False, True)
    restored = loaded.setups([s.camera for s in cameras])
    assert not restored[0].enabled
    assert restored[1].mode == MODES[1]
    assert restored[1].names("12").file == "Face_12.mkv"
    assert restored[1].number == 7


def test_cameras_keep_their_number_and_new_ones_get_a_free_one() -> None:
    """A camera's number, and so its names, stay its own; a new camera's never clash with them."""
    cameras = [s.camera for s in camera_setups(3)]
    assert [s.number for s in Settings().setups(cameras)] == [0, 1, 2]
    remembered = {"Camera model 1": CameraSettings(number=0), "Unplugged camera": CameraSettings(number=2)}
    numbers = [s.number for s in Settings(cameras=remembered).setups(cameras)]
    assert numbers == [1, 0, 3], "never a number another camera has, even an unplugged one"


def test_the_settings_file_holds_the_templates_new_cameras_start_with(tmp_path: Path) -> None:
    """New cameras start with the file's templates; a missing one falls back to the built-in one."""
    path = tmp_path / "settings.json"
    Settings().save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["templates"] == {"name": "Camera {i}", "stream": "Stream_{i}", "file": "Video_{i}.avi"}

    path.write_text('{"templates": {"stream": "Lab_{s}_{i}", "file": "Rec_{s}_{i}.mp4"}}', encoding="utf-8")
    setup = Settings.load(path).setups([camera_setups(1)[0].camera])[0]
    assert setup.templates == NameTemplates("Camera {i}", "Lab_{s}_{i}", "Rec_{s}_{i}.mp4")


def test_a_remembered_mode_the_camera_no_longer_offers_is_replaced() -> None:
    """A mode the camera lost (another firmware, another driver) gives way to its preferred one."""
    settings = Settings(cameras={"Camera model 0": CameraSettings(mode="320x240@30:mjpeg")})
    assert settings.setups([camera_setups(1)[0].camera])[0].mode == MODES[0]


def test_unreadable_settings_fall_back_to_defaults(tmp_path: Path) -> None:
    """A missing or broken file gives the defaults, and unknown entries are ignored."""
    path = tmp_path / "settings.json"
    assert Settings.load(path) == Settings()
    path.write_text("{ not json", encoding="utf-8")
    assert Settings.load(path) == Settings()
    path.write_text("[1, 2]", encoding="utf-8")
    assert Settings.load(path) == Settings()
    path.write_text(
        '{"session": "5", "unknown": 1, "cameras": {"A": {"enabled": false, "extra": 2}}}', encoding="utf-8"
    )
    loaded = Settings.load(path)
    assert loaded.session == "5"
    assert not loaded.cameras["A"].enabled


def test_hand_edits_of_the_wrong_type_are_ignored(tmp_path: Path) -> None:
    """A value of the wrong type, as a number in quotes, gives way to the default, not a crash."""
    path = tmp_path / "settings.json"
    path.write_text(
        '{"session": 5, "frame_numbers": "yes", "templates": {"file": 3},'
        ' "cameras": {"A": {"number": "3", "name": "Face {i}"}, "B": "nonsense"}}',
        encoding="utf-8",
    )
    loaded = Settings.load(path)
    assert (loaded.session, loaded.frame_numbers, loaded.templates) == ("1", False, NameTemplates())
    assert loaded.cameras == {"A": CameraSettings(name="Face {i}")}
