"""One camera's card in the main window: its settings, live preview and status."""

from __future__ import annotations

import html

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from timeshot.gui.camera_status import camera_status
from timeshot.gui.mode_picker import ModePicker
from timeshot.gui.preview_view import PreviewView
from timeshot.gui.theme import CARD_STYLE, WARNING
from timeshot.rig import CameraState, Phase
from timeshot.setup import CameraSetup, NameTemplates, Problem

MAX_NUMBER = 999  # the highest camera number the number box offers
MAKE_DEFAULT = "Use these names for new cameras"
IS_DEFAULT = "✓ New cameras start with these names"


class CameraCard(QFrame):
    """A camera's card: whether to record it, its preview, number, mode, names and status.

    The card edits the camera's :class:`~timeshot.setup.CameraSetup` in place, and signals
    what changed.

    Signals:
        changed: A setting was edited.
        reopen: The camera was switched on or off, or another mode chosen: the camera must be
            opened again (or closed).
        make_default: "Use these names for new cameras" was clicked.
    """

    changed = Signal()
    reopen = Signal()
    make_default = Signal()

    def __init__(self, setup: CameraSetup, session: str, parent: QWidget | None = None) -> None:
        """Builds the card.

        Args:
            setup: The camera's settings, which the card edits in place.
            session: The session, to show the names filled in.
            parent: The widget it belongs to.
        """
        super().__init__(parent)
        self.setup = setup
        self._session = session
        self._defaults: NameTemplates | None = None  # the templates new cameras start with
        self._editable = True  # the camera is on and its settings may be edited
        self.setObjectName("card")
        self.setStyleSheet(CARD_STYLE)

        self.enabled_box = self._enabled_box()
        self.preview = PreviewView()
        self.mode_picker = ModePicker(setup.camera.modes, setup.mode)
        self.mode_picker.changed.connect(self._mode_changed)
        self.number_box = self._number_box()
        templates = setup.templates
        self.name_field = self._template_field(
            templates.name, "Camera name, stored in the LSL stream (camera_name)"
        )
        self.stream_field = self._template_field(
            templates.stream, "Name of the camera's LSL marker stream, as LabRecorder lists it"
        )
        self.file_field = self._template_field(
            templates.file,
            "Video file name, in the output folder: .avi or .mkv for camera JPEG, .mp4 for H.264",
        )
        self.default_button = self._default_button()
        self.names_label = self._rich_label()  # the names, filled in
        self.status_label = self._rich_label()
        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        self.problems_label.setStyleSheet(f"color: {WARNING};")
        self.problems_label.hide()

        self._lay_out()
        self._show_names()
        self._set_inputs_enabled(True)

    # ── updates from the window ──────────────────────────────────────────────

    def set_session(self, session: str) -> None:
        """Shows the names filled in for another session.

        Args:
            session: The session.
        """
        self._session = session
        self._show_names()

    def set_locked(self, locked: bool) -> None:
        """Locks the settings, while LSL streams are open or a recording runs.

        Args:
            locked: Whether to lock them.
        """
        self.enabled_box.setEnabled(not locked)
        self._set_inputs_enabled(not locked)

    def set_file_extension(self, extension: str) -> None:
        """Gives the file name template another video extension, as if typed.

        Args:
            extension: The extension, e.g. ".mp4".
        """
        self.file_field.setText(self.setup.templates.with_extension(extension).file)
        self._names_edited()

    def set_default_templates(self, templates: NameTemplates) -> None:
        """Tells the card the templates new cameras start with, to show whether its own are those.

        Args:
            templates: The templates.
        """
        self._defaults = templates
        self._show_default_state()

    def set_problems(self, problems: list[Problem]) -> None:
        """Shows what is wrong with the camera's settings.

        Args:
            problems: The problems that concern this camera; none hides the warning.
        """
        self.problems_label.setText("\n".join(problem.message for problem in problems))
        self.problems_label.setVisible(bool(problems))

    def show_preview(self, rgb: npt.NDArray[np.uint8]) -> None:
        """Shows a new preview image.

        Args:
            rgb: The image, as an RGB array.
        """
        self.preview.set_image(rgb)

    def show_state(self, state: CameraState | None, phase: Phase) -> None:
        """Shows the camera's live state: its status line, the preview's message and "REC" mark.

        Args:
            state: The state of the camera's recorder process; None if it has none.
            phase: What the rig is doing.
        """
        if not self.setup.enabled:
            self.status_label.setText("")
            self.preview.set_message("Off")
            self.preview.set_recording(False)
            return
        status = camera_status(state, phase, self.setup.mode)
        self.status_label.setText(status.line)
        self.preview.set_message(status.overlay)
        self.preview.set_recording(bool(state and state.stats and state.stats.recording))

    # ── building ─────────────────────────────────────────────────────────────

    def _enabled_box(self) -> QCheckBox:
        """The check box that switches the camera on or off, titled with its name."""
        box = QCheckBox(self.setup.camera.key)
        font = box.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.1)
        box.setFont(font)
        box.setChecked(self.setup.enabled)
        box.setToolTip("Record this camera")
        box.toggled.connect(self._enabled_changed)
        return box

    def _number_box(self) -> QSpinBox:
        """The box for the camera's number, a few digits wide."""
        box = QSpinBox()
        box.setRange(0, MAX_NUMBER)
        box.setValue(self.setup.number)
        box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        box.setToolTip("The camera's number: {i} in its names below. Kept for this camera.")
        box.valueChanged.connect(self._number_changed)
        return box

    def _template_field(self, template: str, tooltip: str) -> QLineEdit:
        """A field for one of the camera's name templates.

        Args:
            template: The template.
            tooltip: What the name is for; how to fill in ``{i}`` and ``{s}`` is added.

        Returns:
            The field.
        """
        field = QLineEdit(template)
        field.setToolTip(f"{tooltip}.\n{{i}} is the camera's number, {{s}} the session.")
        field.textEdited.connect(self._names_edited)
        return field

    def _default_button(self) -> QPushButton:
        """The button that makes the card's name templates the ones new cameras start with."""
        button = QPushButton(MAKE_DEFAULT)
        button.setFlat(True)
        button.setToolTip(
            "Cameras seen for the first time start with this camera's name, stream and file templates"
        )
        button.clicked.connect(self.make_default)
        return button

    @staticmethod
    def _rich_label() -> QLabel:
        """An empty label for rich text, wrapping its lines."""
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        return label

    def _lay_out(self) -> None:
        """Arranges the card: title, preview, the settings form, then the texts below it."""
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Number", self.number_box)
        form.addRow("Mode", self.mode_picker)
        form.addRow("Name", self.name_field)
        form.addRow("Stream", self.stream_field)
        form.addRow("File", self.file_field)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self.enabled_box)
        layout.addWidget(self.preview)
        layout.addLayout(form)
        layout.addWidget(self.default_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.names_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.problems_label)

    # ── edits ────────────────────────────────────────────────────────────────

    def _names_edited(self) -> None:
        """Takes the edited name templates into the camera's settings."""
        self.setup.templates = NameTemplates(
            self.name_field.text(), self.stream_field.text(), self.file_field.text()
        )
        self._show_names()
        self._show_default_state()
        self.changed.emit()

    def _number_changed(self, number: int) -> None:
        """Takes the edited number into the camera's settings.

        Args:
            number: The new number.
        """
        self.setup.number = number
        self._show_names()
        self.changed.emit()

    def _enabled_changed(self, enabled: bool) -> None:
        """Switches the camera on or off.

        Args:
            enabled: Whether to record it.
        """
        self.setup.enabled = enabled
        self._set_inputs_enabled(True)
        self.changed.emit()
        self.reopen.emit()

    def _mode_changed(self) -> None:
        """Takes a newly chosen mode into the camera's settings; the camera must reopen in it."""
        mode = self.mode_picker.mode()
        if mode is not None and mode != self.setup.mode:
            self.setup.mode = mode
            self.changed.emit()
            self.reopen.emit()

    # ── showing ──────────────────────────────────────────────────────────────

    def _set_inputs_enabled(self, editable: bool) -> None:
        """Enables the settings of a camera that is on, while they may be edited.

        Args:
            editable: Whether the settings may be edited now.
        """
        self._editable = editable and self.setup.enabled
        for widget in (
            self.number_box,
            self.mode_picker,
            self.name_field,
            self.stream_field,
            self.file_field,
        ):
            widget.setEnabled(self._editable)
        self._show_default_state()

    def _show_names(self) -> None:
        """Shows the names the templates give in this session, muted, below the form."""
        names = self.setup.names(self._session)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        self.names_label.setText(
            f"<span style='color:{muted}'>→ <b>{html.escape(names.file) or '…'}</b>, stream "
            f"<b>{html.escape(names.stream) or '…'}</b>, camera “{html.escape(names.name)}”</span>"
        )

    def _show_default_state(self) -> None:
        """Shows whether the card's templates are those new cameras start with."""
        is_default = self.setup.templates == self._defaults
        self.default_button.setEnabled(self._editable and not is_default)
        self.default_button.setText(IS_DEFAULT if is_default else MAKE_DEFAULT)
