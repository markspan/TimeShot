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

APP_NAME = "TimeShot"


def settings_path() -> Path:
    r"""Where the settings file lives: the user's configuration folder for TimeShot.

    On Windows that is ``%LOCALAPPDATA%\TimeShot\TimeShot\settings.json``. Call it once the
    application's name is set (see :func:`main`), which the folder is named after.
    """
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    return Path(folder) / "settings.json"


def main() -> int:
    """Runs the desktop app until its window closes.

    Returns:
        The exit code: 0 if all went well.
    """
    multiprocessing.freeze_support()
    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    apply_theme(app)
    QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _scheme: apply_theme(app))
    window = MainWindow(settings_path())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
