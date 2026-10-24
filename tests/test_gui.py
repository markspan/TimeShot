"""The desktop app, driven offscreen with synthetic cameras."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication

from timeshot.cameras.base import CameraInfo, CameraMode
from timeshot.gui.camera_card import describe
from timeshot.gui.main_window import MainWindow
from timeshot.gui.theme import app_icon, apply_theme
from timeshot.recording.recorder import RecorderStats
from timeshot.recording.worker import RecorderProcess
from timeshot.session import CameraSetup, CameraState, Phase


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication([])
    assert isinstance(application, QApplication)
    apply_theme(application)
    return application


@pytest.fixture
def window(app: QApplication, tmp_path: Path, synthetic_cameras: int) -> Iterator[MainWindow]:
    main = MainWindow(tmp_path / "settings.json")
    main.folder_edit.setText(str(tmp_path / "videos"))
    main.show()
    yield main
    main.close()


def pump(app: QApplication, condition: Callable[[], bool], timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_icon_renders(app: QApplication) -> None:
    assert not app_icon().isNull()


def test_record_and_stop_from_the_window(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    session = window.session
    assert pump(app, lambda: len(session.cameras) == 2 and all(s.ready for s in session.cameras.values()))
    assert pump(app, window.record_button.isEnabled)
    window.wait_box.setChecked(False)
    assert window.start_recording(confirm=False)
    assert pump(app, lambda: all(s.stats and s.stats.markers_sent > 15 for s in session.cameras.values()))
    assert not window.session_edit.isEnabled(), "settings are locked while recording"
    session.stop()
    assert pump(app, lambda: session.phase is Phase.PREVIEW)
    assert pump(app, lambda: "every frame with its marker" in window.phase_label.text())
    assert sorted(p.name for p in (tmp_path / "videos").iterdir()) == ["Video_0.avi", "Video_1.avi"]


def test_the_format_choice_sets_every_file_extension(app: QApplication, window: MainWindow) -> None:
    assert pump(app, lambda: bool(window.session.cameras))
    mp4 = window.format_box.findData(".mp4")
    window.format_box.setCurrentIndex(mp4)
    window.format_box.activated.emit(mp4)
    assert all(setup.file.endswith(".mp4") for setup in window._setups)
    assert window._settings.templates.file == "Video_{i}.mp4", "new cameras start with it too"
    card = next(iter(window._cards.values()))
    card.file.setText("Face_{s}.mkv")
    card.file.textEdited.emit(card.file.text())
    assert window.format_box.currentIndex() == -1, "the cameras' formats differ"


def test_settings_are_remembered(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    assert pump(app, lambda: bool(window.session.cameras))
    window.session_edit.setText("42")
    window.close()
    assert '"session": "42"' in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_the_camera_number_fills_in_the_names(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    assert pump(app, lambda: bool(window.session.cameras))
    card = next(iter(window._cards.values()))
    card.number.setValue(7)
    assert card.setup.number == 7
    assert "Video_7.avi" in card.result.text()
    window.close()
    assert '"number": 7' in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_a_cameras_names_can_become_the_ones_new_cameras_start_with(
    app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    assert pump(app, lambda: len(window._cards) == 2)
    first, second = window._cards.values()
    assert not first.default_button.isEnabled(), "its names are those new cameras start with already"
    first.file.setText("Face_{s}_{i}.mkv")
    first.file.textEdited.emit(first.file.text())
    assert first.default_button.isEnabled()

    first.default_button.click()
    assert window._settings.templates.file == "Face_{s}_{i}.mkv"
    assert not first.default_button.isEnabled()
    assert second.default_button.isEnabled(), "the other camera's names now differ from them"
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["templates"]["file"] == "Face_{s}_{i}.mkv"


def test_the_cards_scroll_only_as_far_as_they_reach(app: QApplication, window: MainWindow) -> None:
    assert pump(app, lambda: len(window._cards) == 2)
    for size in ((1240, 780), (1240, 600), (700, 700)):
        window.resize(*size)
        pump(app, lambda: False, timeout=0.3)  # let the layout settle
        reach = max(card.geometry().bottom() + 1 for card in window._cards.values())
        reach += window.grid.contentsMargins().bottom()
        overflow = max(0, reach - window._scroll.viewport().height())
        assert window._scroll.verticalScrollBar().maximum() <= overflow, size


def test_status_line_describes_the_camera() -> None:
    mode = CameraMode(640, 480, 30.0)
    setup = CameraSetup(CameraInfo("0", "Cam", (mode,), "synthetic", "Cam"), 0)
    state = CameraState(setup, process=cast(RecorderProcess, None), mode=mode)  # describe() never uses it
    state.stats = RecorderStats(29.97, 100, 1e5, 0.01, True, False, True, 42, 0, None)
    text, message = describe(state, Phase.RECORDING, mode)
    assert "29.97 fps" in text
    assert "42 frames" in text
    assert "markers are not being recorded" in text
    assert message is None
    state.stats = RecorderStats(30.0, 100, 1e5, 3.0, False, False, False, 0, 0, None)
    assert describe(state, Phase.PREVIEW, mode)[1] == "The camera stopped delivering frames"
