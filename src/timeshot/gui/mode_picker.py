"""Choosing a camera's mode: its frame format, size and frame rate, in three lists."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from timeshot.cameras.base import CameraMode


class ModePicker(QWidget):
    """A camera's frame format, size and frame rate, each list narrowed to what the choices before it allow.

    Signals:
        changed: Another format, size or frame rate was chosen.
    """

    changed = Signal()

    def __init__(
        self, modes: tuple[CameraMode, ...], current: CameraMode | None, parent: QWidget | None = None
    ) -> None:
        """Fills the lists with the camera's modes.

        Args:
            modes: The modes the camera offers.
            current: The mode to show chosen; None for the first of each list.
            parent: The widget it belongs to.
        """
        super().__init__(parent)
        self._modes = modes
        self._format, self._size, self._fps = QComboBox(), QComboBox(), QComboBox()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for combo, stretch in ((self._format, 3), (self._size, 3), (self._fps, 2)):
            layout.addWidget(combo, stretch)
        self._format.setToolTip(
            "JPEG frames are recorded exactly as the camera delivers them.\n"
            "Raw frames are encoded as JPEG first, which takes more CPU."
        )

        formats = sorted({mode.format_label for mode in modes}, key=lambda label: (label != "JPEG", label))
        self._fill(
            self._format, ((label, label) for label in formats), current.format_label if current else None
        )
        self._refill_sizes(current)
        self._format.currentIndexChanged.connect(self._format_changed)
        self._size.currentIndexChanged.connect(self._size_changed)
        self._fps.currentIndexChanged.connect(self.changed)

    def mode(self) -> CameraMode | None:
        """The chosen mode; None only if the camera offers none."""
        chosen = (self._format.currentData(), self._size.currentData(), self._fps.currentData())
        return next(
            (mode for mode in self._modes if (mode.format_label, mode.size_label, mode.fps) == chosen), None
        )

    def _format_changed(self) -> None:
        """Offers the sizes of the newly chosen format, keeping the size if it has it."""
        self._refill_sizes(self.mode())
        self.changed.emit()

    def _size_changed(self) -> None:
        """Offers the frame rates of the newly chosen size, keeping the rate if it has it."""
        self._refill_rates(self.mode())
        self.changed.emit()

    def _refill_sizes(self, keep: CameraMode | None) -> None:
        """Lists the chosen format's sizes, largest first, then their frame rates.

        Args:
            keep: The mode whose size (and frame rate) to keep chosen, if offered.
        """
        sizes = sorted(
            {
                (m.width * m.height, m.size_label)
                for m in self._modes
                if m.format_label == self._format.currentData()
            },
            reverse=True,
        )
        self._fill(self._size, ((label, label) for _area, label in sizes), keep.size_label if keep else None)
        self._refill_rates(keep)

    def _refill_rates(self, keep: CameraMode | None) -> None:
        """Lists the chosen format and size's frame rates, fastest first.

        Args:
            keep: The mode whose frame rate to keep chosen, if offered.
        """
        chosen = (self._format.currentData(), self._size.currentData())
        rates = sorted({m.fps for m in self._modes if (m.format_label, m.size_label) == chosen}, reverse=True)
        self._fill(self._fps, ((f"{rate:g} fps", rate) for rate in rates), keep.fps if keep else None)

    @staticmethod
    def _fill(combo: QComboBox, items: Iterable[tuple[str, object]], keep: object) -> None:
        """Replaces a list's items, without signalling a change.

        Args:
            combo: The list.
            items: (text, data) of each item.
            keep: The data of the item to choose; the first item is chosen if none has it.
        """
        combo.blockSignals(True)
        combo.clear()
        for text, data in items:
            combo.addItem(text, data)
        index = combo.findData(keep)
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)
