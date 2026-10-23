"""The look of TimeShot's window: Fusion everywhere, light or dark as the system is, with the
blue of the TimeShot and MovieMerger icons as accent."""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ACCENT = "#4a7fc9"
RECORD = "#d64545"  # the record button and the recording indicator
WARNING = "#c8811a"
OK = "#3f9d5a"

_LIGHT = {
    QPalette.ColorRole.Window: "#f4f5f7",
    QPalette.ColorRole.WindowText: "#1d2330",
    QPalette.ColorRole.Base: "#ffffff",
    QPalette.ColorRole.AlternateBase: "#eef0f3",
    QPalette.ColorRole.Text: "#1d2330",
    QPalette.ColorRole.Button: "#e9ecf0",
    QPalette.ColorRole.ButtonText: "#1d2330",
    QPalette.ColorRole.ToolTipBase: "#ffffff",
    QPalette.ColorRole.ToolTipText: "#1d2330",
    QPalette.ColorRole.PlaceholderText: "#8a93a3",
    QPalette.ColorRole.Mid: "#c9ced6",
}
_DARK = {
    QPalette.ColorRole.Window: "#1e2128",
    QPalette.ColorRole.WindowText: "#e3e6eb",
    QPalette.ColorRole.Base: "#15171c",
    QPalette.ColorRole.AlternateBase: "#262a32",
    QPalette.ColorRole.Text: "#e3e6eb",
    QPalette.ColorRole.Button: "#2b3039",
    QPalette.ColorRole.ButtonText: "#e3e6eb",
    QPalette.ColorRole.ToolTipBase: "#2b3039",
    QPalette.ColorRole.ToolTipText: "#e3e6eb",
    QPalette.ColorRole.PlaceholderText: "#6f7785",
    QPalette.ColorRole.Mid: "#3a404b",
}


def is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply_theme(app: QApplication) -> None:
    """Applies the palette for the system's current light/dark setting (call again when it changes)."""
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (_DARK if is_dark() else _LIGHT).items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.Accent, QColor(ACCENT))
    disabled = QPalette.ColorGroup.Disabled
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(disabled, role, palette.color(QPalette.ColorRole.PlaceholderText))
    app.setPalette(palette)


def app_icon(sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)) -> QIcon:
    """The TimeShot icon, rendered from its SVG at the usual icon sizes."""
    icon = QIcon()
    try:
        svg = resources.files("timeshot").joinpath("resources/timeshot.svg").read_bytes()
    except (FileNotFoundError, OSError, NotADirectoryError):
        return icon  # a build without the resource still runs, just without an icon
    renderer = QSvgRenderer(QByteArray(svg))
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pixmap)
    return icon
