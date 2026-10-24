"""The desktop app, driven offscreen with synthetic cameras."""

from __future__ import annotations

import shutil
import subprocess
import sys
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
    card = next(iter(window._cards.values()))
    card.file.setText("Face_{s}.mkv")
    card.file.textEdited.emit(card.file.text())
    assert window.format_box.currentIndex() == -1, "the cameras' formats differ"


def test_settings_are_remembered(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    assert pump(app, lambda: bool(window.session.cameras))
    window.session_edit.setText("42")
    window.close()
    assert '"session": "42"' in (tmp_path / "settings.json").read_text(encoding="utf-8")


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' DLL search")
def test_qt_loads_with_a_foreign_icuuc_dll_on_the_dll_search_path(tmp_path: Path) -> None:
    """As under Anaconda, whose icuuc.dll lacks the plain-named functions Qt needs from Windows'
    ICU: plain PySide6 fails to load, the app (which loads Windows' ICU first) does not."""
    shutil.copy(Path(sys.base_prefix) / "python3.dll", tmp_path / "icuuc.dll")  # any other DLL

    def import_with_foreign_icu(module: str) -> subprocess.CompletedProcess[str]:
        code = f"import os; os.add_dll_directory({str(tmp_path)!r}); import {module}"
        return subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
        )

    plain = import_with_foreign_icu("PySide6.QtCore")
    assert plain.returncode != 0, "the problem, without TimeShot"
    assert "QtCore" in plain.stderr
    app = import_with_foreign_icu("timeshot.gui.app")
    assert app.returncode == 0, app.stderr
