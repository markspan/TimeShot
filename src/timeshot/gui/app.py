"""Starts TimeShot's desktop app."""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from timeshot import __version__
from timeshot.gui.main_window import MainWindow
from timeshot.gui.theme import app_icon, apply_theme


def settings_path() -> Path:
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    return Path(folder) / "settings.json"


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    app.setApplicationName("TimeShot")
    app.setOrganizationName("TimeShot")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    apply_theme(app)
    QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _scheme: apply_theme(app))
    window = MainWindow(settings_path())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
