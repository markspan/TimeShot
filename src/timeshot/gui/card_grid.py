"""The middle of the main window: the camera cards, in as many columns as fit."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QSizePolicy, QWidget

from timeshot.gui.camera_card import CameraCard

CARD_WIDTH = 440  # the grid gets as many columns of about this width as fit
SPACING = 14  # between the cards


class CardGrid(QScrollArea):
    """The camera cards in a scrolling grid, or a message while there are none."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Builds the grid, empty, showing no message yet.

        Args:
            parent: The widget it belongs to.
        """
        super().__init__(parent)
        self.grid = QGridLayout()
        self.grid.setSpacing(SPACING)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.message_label = QLabel()
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.grid.addWidget(self.message_label, 0, 0)
        self._cards: list[CameraCard] = []

        host = QWidget()
        host.setLayout(self.grid)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidget(host)

    def show_message(self, text: str) -> None:
        """Shows a message in place of the cards, which it removes.

        Args:
            text: The message, e.g. "Looking for cameras…".
        """
        for card in self._cards:
            card.deleteLater()
        self._cards = []
        self.message_label.setText(text)
        self.message_label.show()
        self.grid.addWidget(self.message_label, 0, 0)

    def set_cards(self, cards: Iterable[CameraCard]) -> None:
        """Shows cards in place of the message.

        Args:
            cards: The cards, in the order to show them in: row by row.
        """
        self.message_label.hide()
        self._cards = list(cards)
        self._lay_out()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Arranges the cards again for the new width."""
        super().resizeEvent(event)
        self._lay_out()

    def _lay_out(self) -> None:
        """Puts the cards in as many equally wide columns as fit."""
        if not self._cards:
            return
        columns = max(1, self.viewport().width() // CARD_WIDTH)
        for position, card in enumerate(self._cards):
            self.grid.addWidget(card, *divmod(position, columns))  # adding a card again moves it
        for column in range(self.grid.columnCount()):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
