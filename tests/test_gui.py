"""The desktop app, driven offscreen with synthetic cameras."""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication

from timeshot.cameras.base import CameraInfo, CameraMode
from timeshot.gui.camera_card import CameraCard
from timeshot.gui.camera_status import STALLED, camera_status
from timeshot.gui.main_window import MainWindow
from timeshot.gui.theme import app_icon, apply_theme
from timeshot.recording.recorder import RecorderStats
from timeshot.recording.worker import RecorderProcess
from timeshot.rig import CameraState, Phase
from timeshot.setup import CameraSetup


@pytest.fixture(scope="module")
def app() -> QApplication:
    """The application, themed as the app starts it."""
    application = QApplication.instance() or QApplication([])
    assert isinstance(application, QApplication)
    apply_theme(application)
    return application


@pytest.fixture
def window(app: QApplication, tmp_path: Path, synthetic_cameras: int) -> Iterator[MainWindow]:
    """The main window with two synthetic cameras, a settings file and a video folder of its own."""
    main = MainWindow(tmp_path / "settings.json")
    main.session_bar.folder_edit.setText(str(tmp_path / "videos"))
    main.show()
    yield main
    main.close()


def pump(app: QApplication, condition: Callable[[], bool], timeout: float = 30.0) -> bool:
    """Runs the event loop until a condition holds.

    Args:
        app: The application.
        condition: Checked after each round of events.
        timeout: How many seconds to wait at most.

    Returns:
        Whether the condition held in time.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def cards(window: MainWindow) -> list[CameraCard]:
    """The window's camera cards, in their order."""
    return list(window._cards.values())


def edit_file(card: CameraCard, file: str) -> None:
    """Types a file name template into a card.

    Args:
        card: The card.
        file: The template.
    """
    card.file_field.setText(file)
    card.file_field.textEdited.emit(file)


def test_icon_renders(app: QApplication) -> None:
    """The icon draws from its SVG."""
    assert not app_icon().isNull()


def test_record_and_stop_from_the_window(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    """A recording from the window: locked settings while it runs, a summary and files after it."""
    rig = window.rig
    assert pump(app, lambda: len(rig.cameras) == 2 and rig.ready)
    assert pump(app, window.record_bar.record_button.isEnabled)
    window.session_bar.wait_box.setChecked(False)
    assert window.start_recording(confirm=False)
    assert pump(app, lambda: all(s.stats and s.stats.markers_sent > 15 for s in rig.cameras.values()))
    assert not window.session_bar.session_edit.isEnabled(), "settings are locked while recording"
    rig.stop()
    assert pump(app, lambda: rig.phase is Phase.PREVIEW)
    assert pump(app, lambda: "every frame with its marker" in window.record_bar.phase_label.text())
    assert sorted(p.name for p in (tmp_path / "videos").iterdir()) == ["Video_0.avi", "Video_1.avi"]


def test_the_format_choice_sets_every_file_extension(app: QApplication, window: MainWindow) -> None:
    """Choosing a format changes every camera's file extension, and that of cameras yet to come."""
    assert pump(app, lambda: bool(window.rig.cameras))
    format_box = window.session_bar.format_box
    mp4 = format_box.findData(".mp4")
    format_box.setCurrentIndex(mp4)
    format_box.activated.emit(mp4)
    assert all(setup.templates.file.endswith(".mp4") for setup in window._setups)
    assert window._settings.templates.file == "Video_{i}.mp4", "new cameras start with it too"
    edit_file(cards(window)[0], "Face_{s}.mkv")
    assert format_box.currentIndex() == -1, "the cameras' formats differ"


def test_settings_are_remembered(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    """Closing the window saves the settings."""
    assert pump(app, lambda: bool(window.rig.cameras))
    window.session_bar.session_edit.setText("42")
    window.close()
    assert '"session": "42"' in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_the_camera_number_fills_in_the_names(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    """A camera's number shows in its names at once, and is saved."""
    assert pump(app, lambda: bool(window.rig.cameras))
    card = cards(window)[0]
    card.number_box.setValue(7)
    assert card.setup.number == 7
    assert "Video_7.avi" in card.names_label.text()
    window.close()
    assert '"number": 7' in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_a_cameras_names_can_become_the_ones_new_cameras_start_with(
    app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    """A card's button saves its templates for new cameras; each card shows whether it has them."""
    assert pump(app, lambda: len(window._cards) == 2)
    first, second = cards(window)
    assert not first.default_button.isEnabled(), "its names are those new cameras start with already"
    edit_file(first, "Face_{s}_{i}.mkv")
    assert first.default_button.isEnabled()

    first.default_button.click()
    assert window._settings.templates.file == "Face_{s}_{i}.mkv"
    assert not first.default_button.isEnabled()
    assert second.default_button.isEnabled(), "the other camera's names now differ from them"
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["templates"]["file"] == "Face_{s}_{i}.mkv"


def test_the_cards_scroll_only_as_far_as_they_reach(app: QApplication, window: MainWindow) -> None:
    """No scroll bar shows for room the cards do not take, whatever the window's size."""
    assert pump(app, lambda: len(window._cards) == 2)
    grid = window.card_grid
    for size in ((1240, 780), (1240, 600), (700, 700)):
        window.resize(*size)
        pump(app, lambda: False, timeout=0.3)  # let the layout settle
        reach = max(card.geometry().bottom() + 1 for card in cards(window))
        reach += grid.grid.contentsMargins().bottom()
        overflow = max(0, reach - grid.viewport().height())
        assert grid.verticalScrollBar().maximum() <= overflow, size


def test_status_line_describes_the_camera() -> None:
    """The status line tells the rate, the frames recorded, and a LabRecorder that is missing."""
    mode = CameraMode(640, 480, 30.0)
    setup = CameraSetup(CameraInfo("0", "Cam", (mode,), "synthetic", "Cam"), 0)
    state = CameraState(
        setup, process=cast(RecorderProcess, None), mode=mode
    )  # camera_status() never uses it
    recording = RecorderStats(
        fps=29.97,
        frame_bytes=1e5,
        seconds_since_frame=0.01,
        streaming=True,
        consumers=False,
        recording=True,
        markers_sent=42,
        backlog=0,
        error=None,
    )
    state.stats = recording
    status = camera_status(state, Phase.RECORDING, mode)
    assert "29.97 fps" in status.line
    assert "42 frames" in status.line
    assert "markers are not being recorded" in status.line
    assert status.overlay is None

    state.stats = dataclasses.replace(recording, seconds_since_frame=3.0, streaming=False, recording=False)
    assert camera_status(state, Phase.PREVIEW, mode).overlay == STALLED, "no frames for 3 s"
