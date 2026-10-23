"""One camera's card in the main window: live preview, mode, names and status."""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from timeshot.cameras.base import CameraMode
from timeshot.gui.theme import OK, RECORD, WARNING
from timeshot.recording.recorder import RecorderStats
from timeshot.recording.writer import FILE_TYPES
from timeshot.session import CameraSetup, CameraState, Phase, Problem

# A camera that delivered nothing for this long is reported as stalled.
STALL_SECONDS = 1.5


class PreviewView(QWidget):
    """A camera's preview image, letterboxed to keep its aspect ratio, or a message instead."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._message: str | None = "Starting camera…"
        self._recording = False
        self.setMinimumSize(240, 135)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:
        return QSize(400, 225)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return width * 9 // 16

    def set_image(self, rgb: npt.NDArray[np.uint8]) -> None:
        height, width = rgb.shape[:2]
        self._image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()
        self._message = None
        self.update()

    def set_message(self, message: str | None) -> None:
        """Shows ``message`` over the picture (None: the picture only); clears a stale picture."""
        if message != self._message:
            self._message = message
            if message:
                self._image = None
            self.update()

    def set_recording(self, recording: bool) -> None:
        if recording != self._recording:
            self._recording = recording
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#0d0f13"))
        if self._image is not None:
            size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x, y = (self.width() - size.width()) / 2, (self.height() - size.height()) / 2
            painter.drawImage(QRectF(x, y, size.width(), size.height()), self._image)
        if self._message:
            painter.setPen(QColor("#aab2bf"))
            painter.drawText(
                self.rect().adjusted(12, 12, -12, -12),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message,
            )
        if self._recording:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(RECORD))
            painter.drawEllipse(QRectF(10, 10, 12, 12))
            font = QFont(painter.font())
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(28, 6, 60, 20), Qt.AlignmentFlag.AlignVCenter, "REC")
        painter.end()


class ModePicker(QWidget):
    """Format, size and frame rate of a camera, each list narrowed to what the choices before it allow."""

    changed = Signal()

    def __init__(
        self, modes: tuple[CameraMode, ...], current: CameraMode | None, parent: QWidget | None = None
    ) -> None:
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
        formats = sorted({m.format_label for m in modes}, key=lambda f: (f != "JPEG", f))
        self._fill(self._format, [(f, f) for f in formats], current.format_label if current else None)
        self._format.currentIndexChanged.connect(self._format_changed)
        self._size.currentIndexChanged.connect(self._size_changed)
        self._fps.currentIndexChanged.connect(self.changed)
        self._refill_sizes(current)

    def mode(self) -> CameraMode | None:
        fmt, size, fps = self._format.currentData(), self._size.currentData(), self._fps.currentData()
        return next(
            (m for m in self._modes if (m.format_label, m.size_label, m.fps) == (fmt, size, fps)), None
        )

    def _format_changed(self) -> None:
        self._refill_sizes(self.mode())
        self.changed.emit()

    def _size_changed(self) -> None:
        self._refill_rates(self.mode())
        self.changed.emit()

    def _refill_sizes(self, keep: CameraMode | None) -> None:
        modes = [m for m in self._modes if m.format_label == self._format.currentData()]
        sizes = sorted({(m.width * m.height, m.size_label) for m in modes}, reverse=True)
        self._fill(self._size, [(label, label) for _area, label in sizes], keep.size_label if keep else None)
        self._refill_rates(keep)

    def _refill_rates(self, keep: CameraMode | None) -> None:
        rates = sorted(
            {
                m.fps
                for m in self._modes
                if (m.format_label, m.size_label) == (self._format.currentData(), self._size.currentData())
            },
            reverse=True,
        )
        self._fill(self._fps, [(f"{r:g} fps", r) for r in rates], keep.fps if keep else None)

    @staticmethod
    def _fill(combo: QComboBox, items: list[tuple[str, object]], keep: object) -> None:
        combo.blockSignals(True)
        combo.clear()
        for text, data in items:
            combo.addItem(text, data)
        index = combo.findData(keep)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)


class CameraCard(QFrame):
    """A camera's settings and live state."""

    changed = Signal()  # settings edited
    reopen = Signal()  # switched on or off, or another mode chosen: the camera must be (re)opened

    def __init__(self, setup: CameraSetup, session: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setup = setup
        self._session = session
        self.setObjectName("card")
        self.setStyleSheet(
            "QFrame#card { border: 1px solid palette(mid); border-radius: 10px; background: palette(base); }"
        )

        self.enabled = QCheckBox(setup.camera.key)
        font = self.enabled.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.1)
        self.enabled.setFont(font)
        self.enabled.setChecked(setup.enabled)
        self.enabled.setToolTip("Record this camera")
        self.enabled.toggled.connect(self._enabled_changed)

        self.preview = PreviewView()
        self.modes = ModePicker(setup.camera.modes, setup.mode)
        self.modes.changed.connect(self._mode_changed)

        self.name = self._field(setup.name, "Camera name, stored in the LSL stream (camera_name)")
        self.stream = self._field(
            setup.stream, "Name of the camera's LSL marker stream, as LabRecorder lists it"
        )
        self.file = self._field(
            setup.file, "Video file name, in the output folder: .avi or .mkv for camera JPEG, .mp4 for H.264"
        )
        self.result = QLabel()
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.setWordWrap(True)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setWordWrap(True)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet(f"color: {WARNING};")
        self.problems.hide()

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Mode", self.modes)
        form.addRow("Name", self.name)
        form.addRow("Stream", self.stream)
        form.addRow("File", self.file)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self.enabled)
        layout.addWidget(self.preview)
        layout.addLayout(form)
        layout.addWidget(self.result)
        layout.addWidget(self.status)
        layout.addWidget(self.problems)
        self._show_names()
        self._set_inputs_enabled(True)

    # ── updates from the window ──────────────────────────────────────────────

    def set_session(self, session: str) -> None:
        self._session = session
        self._show_names()

    def set_locked(self, locked: bool) -> None:
        """Locks the settings while streams are open or a recording runs."""
        self.enabled.setEnabled(not locked)
        self._set_inputs_enabled(not locked)

    def set_file_extension(self, extension: str) -> None:
        """Gives the file name template ``extension``, in place of a video extension it has."""
        template = self.setup.file
        suffix = Path(template).suffix
        stem = template[: -len(suffix)] if suffix.lower() in FILE_TYPES else template
        self.file.setText(stem + extension)
        self._names_edited()

    def set_problems(self, problems: list[Problem]) -> None:
        self.problems.setText("\n".join(p.message for p in problems))
        self.problems.setVisible(bool(problems))

    def show_preview(self, rgb: npt.NDArray[np.uint8]) -> None:
        self.preview.set_image(rgb)

    def show_state(self, state: CameraState | None, phase: Phase) -> None:
        if not self.setup.enabled:
            self.preview.set_message("Off")
            self.status.setText("")
            self.preview.set_recording(False)
            return
        text, message = describe(state, phase, self.setup.mode)
        self.status.setText(text)
        self.preview.set_message(message)
        self.preview.set_recording(bool(state and state.stats and state.stats.recording))

    # ── internals ────────────────────────────────────────────────────────────

    def _field(self, text: str, tooltip: str) -> QLineEdit:
        edit = QLineEdit(text)
        edit.setToolTip(f"{tooltip}.\n{{i}} is the camera's number, {{s}} the session.")
        edit.textEdited.connect(self._names_edited)
        return edit

    def _names_edited(self) -> None:
        self.setup.name, self.setup.stream, self.setup.file = (
            self.name.text(),
            self.stream.text(),
            self.file.text(),
        )
        self._show_names()
        self.changed.emit()

    def _enabled_changed(self, enabled: bool) -> None:
        self.setup.enabled = enabled
        self._set_inputs_enabled(True)
        self.changed.emit()
        self.reopen.emit()

    def _mode_changed(self) -> None:
        mode = self.modes.mode()
        if mode is not None and mode != self.setup.mode:
            self.setup.mode = mode
            self.changed.emit()
            self.reopen.emit()

    def _set_inputs_enabled(self, editable: bool) -> None:
        active = editable and self.setup.enabled
        for widget in (self.modes, self.name, self.stream, self.file):
            widget.setEnabled(active)

    def _show_names(self) -> None:
        name, stream, file = self.setup.names(self._session)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        self.result.setText(
            f"<span style='color:{muted}'>→ <b>{html.escape(file) or '…'}</b>, stream "
            f"<b>{html.escape(stream) or '…'}</b>, camera “{html.escape(name)}”</span>"
        )


def _colored(color: str, text: str) -> str:
    return f"<span style='color:{color}'>{html.escape(text)}</span>"


def describe(state: CameraState | None, phase: Phase, requested: CameraMode | None) -> tuple[str, str | None]:
    """The status line of a camera (rich text) and the message to show over its preview, if any."""
    if state is None:
        return "", "Starting camera…"
    if state.ended or (state.error and state.stats is None):
        return _colored(RECORD, state.error or "The camera stopped."), "Camera unavailable"
    if state.mode is None or state.stats is None:
        return "Opening camera…", "Starting camera…"
    stats = state.stats
    parts = [f"{stats.fps:.2f} fps"]
    if requested is not None and state.mode != requested:
        parts.append(html.escape(f"camera gave {state.mode}"))
    parts += _link_status(stats)
    parts += _recording_status(state, phase)
    if stats.error:
        parts.append(_colored(RECORD, stats.error))
    message = None
    if stats.seconds_since_frame > STALL_SECONDS:
        parts.append(_colored(RECORD, f"no frames for {stats.seconds_since_frame:.0f} s"))
        message = "The camera stopped delivering frames"
    return " · ".join(parts), message


def _link_status(stats: RecorderStats) -> list[str]:
    """Whether LabRecorder receives the camera's markers."""
    if stats.streaming and stats.consumers:
        return [_colored(OK, "LabRecorder connected")]
    if stats.recording:
        return [_colored(RECORD, "no LabRecorder connected: markers are not being recorded")]
    if stats.streaming:
        return [_colored(WARNING, "waiting for LabRecorder")]
    return []


def _recording_status(state: CameraState, phase: Phase) -> list[str]:
    """Progress of the running recording, or how the last one ended."""
    stats, result = state.stats, state.result
    if stats is not None and stats.recording:
        parts = [_colored(RECORD, f"● {stats.markers_sent:,} frames"), html.escape(stats.writing)]
        if stats.backlog > stats.fps / 2:
            parts.append(_colored(WARNING, f"{stats.backlog} frames waiting for the disk"))
        return parts
    if phase is Phase.PREVIEW and result is not None:
        if result.ok:
            return [_colored(OK, f"recorded {result.file.frames_written:,} frames")]
        return [_colored(RECORD, result.file.error or "frames and markers differ")]
    return []
