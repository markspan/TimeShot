"""A camera's live preview on its card: the newest image, or a message instead."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from timeshot.gui.theme import RECORD

BACKGROUND = "#0d0f13"  # dark in light and dark mode alike: a picture's surroundings
MESSAGE_COLOR = "#aab2bf"


class PreviewView(QWidget):
    """A camera's preview image, letterboxed to keep its aspect ratio, or a message instead.

    While the camera records, a red "REC" mark shows in the top-left corner.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Starts with the message that the camera is starting.

        Args:
            parent: The widget it belongs to.
        """
        super().__init__(parent)
        self._image: QImage | None = None
        self._message: str | None = "Starting camera…"
        self._recording = False
        self.setMinimumSize(240, 135)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:
        """A 16:9 size that suits a card.

        Fixed, not following the width: the cards sit at the top of their grid, which gives
        them their size hint, so a preview would never get a taller height, while the scroll
        area would count it and show a scroll bar for nothing.
        """
        return QSize(400, 225)

    def set_image(self, rgb: npt.NDArray[np.uint8]) -> None:
        """Shows a new image, in place of the previous image or a message.

        Args:
            rgb: The image, as an RGB array.
        """
        height, width = rgb.shape[:2]
        # Copied: the QImage would otherwise point into the array, which the caller may reuse.
        self._image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()
        self._message = None
        self.update()

    def set_message(self, message: str | None) -> None:
        """Shows a message in place of the image.

        Args:
            message: The message, e.g. that the camera is unavailable; it clears the image, which would
                be stale. None shows the image only.
        """
        if message != self._message:
            self._message = message
            if message:
                self._image = None
            self.update()

    def set_recording(self, recording: bool) -> None:
        """Shows or hides the "REC" mark.

        Args:
            recording: Whether the camera records.
        """
        if recording != self._recording:
            self._recording = recording
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Draws the image, letterboxed, then the message and the "REC" mark over it."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(BACKGROUND))
        if self._image is not None:
            size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x, y = (self.width() - size.width()) / 2, (self.height() - size.height()) / 2
            painter.drawImage(QRectF(x, y, size.width(), size.height()), self._image)
        if self._message:
            painter.setPen(QColor(MESSAGE_COLOR))
            painter.drawText(
                self.rect().adjusted(12, 12, -12, -12),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message,
            )
        if self._recording:
            self._paint_recording_mark(painter)
        painter.end()

    @staticmethod
    def _paint_recording_mark(painter: QPainter) -> None:
        """Draws a red dot and "REC" in the top-left corner.

        Args:
            painter: The painter, painting this view.
        """
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(RECORD))
        painter.drawEllipse(QRectF(10, 10, 12, 12))
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(28, 6, 60, 20), Qt.AlignmentFlag.AlignVCenter, "REC")
