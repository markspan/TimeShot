"""The top of the main window: the settings that apply to every camera.

The first row says where recordings go (session and folder) and finds cameras; the second
says how they are recorded (format and options).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from timeshot.gui.theme import SPACING
from timeshot.recording.writer import FILE_TYPES
from timeshot.settings import Settings

GROUP_SPACING = 16  # between the groups of a row


class SessionBar(QWidget):
    """Session, folder, file format and recording options, which apply to every camera.

    Signals:
        session_changed: The session was edited; carries the new session.
        changed: The folder or an option was changed.
        format_chosen: A file format was chosen; carries its extension, e.g. ".mp4".
        find_cameras: "Find cameras" was clicked.
    """

    session_changed = Signal(str)
    changed = Signal()
    format_chosen = Signal(str)
    find_cameras = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        """Builds the bar, showing the remembered settings.

        Args:
            settings: The remembered settings; the bar shows them, and does not change them.
            parent: The widget it belongs to.
        """
        super().__init__(parent)
        self.session_edit = QLineEdit(settings.session)
        self.session_edit.setMaximumWidth(140)
        self.session_edit.setToolTip("Fills in {s} in the names: a subject or session number")
        self.session_edit.textChanged.connect(self.session_changed)

        self.folder_edit = QLineEdit(settings.folder)
        self.folder_edit.setToolTip("Where the video files go")
        self.folder_edit.textChanged.connect(self.changed)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)

        self.find_button = QPushButton("Find cameras")
        self.find_button.setToolTip("Look for cameras again, e.g. after plugging one in")
        self.find_button.clicked.connect(self.find_cameras)

        self.format_box = QComboBox()
        for extension, file_type in FILE_TYPES.items():
            self.format_box.addItem(file_type.label, extension)
        self.format_box.setToolTip(
            "Camera JPEG: the camera's own frames, untouched: identical quality, little CPU, large files.\n"
            "H.264: files about ten times smaller, encoded by the graphics hardware if it can,\n"
            "else by the CPU. Sets the extension of every camera's file name."
        )
        self.format_box.activated.connect(self._format_activated)

        self.wait_box = QCheckBox("Wait for LabRecorder")
        self.wait_box.setChecked(settings.wait_for_labrecorder)
        self.wait_box.setToolTip(
            "Start recording only once LabRecorder records every camera's stream,\n"
            "so that no frame goes without its marker in the XDF file"
        )
        self.wait_box.toggled.connect(self.changed)

        self.numbers_box = QCheckBox("Burn frame numbers into the video")
        self.numbers_box.setChecked(settings.frame_numbers)
        self.numbers_box.setToolTip(
            "Draws each frame's number into its picture. The frames are then re-encoded,\n"
            "which takes much more CPU; without it, the camera's own JPEG frames are\n"
            "recorded untouched and the numbers are in the LSL markers only."
        )
        self.numbers_box.toggled.connect(self.changed)

        self._lay_out()

    # ── what it holds ────────────────────────────────────────────────────────

    @property
    def session(self) -> str:
        """The session, for ``{s}`` in the names."""
        return self.session_edit.text()

    @property
    def folder_text(self) -> str:
        """The folder for the video files, as typed (and remembered)."""
        return self.folder_edit.text()

    @property
    def folder(self) -> Path:
        """The folder for the video files: the current folder if none is given, ``~`` expanded."""
        return Path(self.folder_text.strip() or ".").expanduser()

    @property
    def wait_for_labrecorder(self) -> bool:
        """Whether recording waits until LabRecorder records every camera's stream."""
        return self.wait_box.isChecked()

    @property
    def frame_numbers(self) -> bool:
        """Whether to burn each frame's number into its picture."""
        return self.numbers_box.isChecked()

    # ── updates from the window ──────────────────────────────────────────────

    def show_format(self, extension: str | None) -> None:
        """Shows the format the cameras' file names have, without signalling a choice.

        Args:
            extension: Their common extension; None (no format shown) if they differ.
        """
        self.format_box.blockSignals(True)
        self.format_box.setCurrentIndex(self.format_box.findData(extension) if extension else -1)
        self.format_box.blockSignals(False)

    def set_locked(self, locked: bool) -> None:
        """Locks the settings, while LSL streams are open or a recording runs.

        Args:
            locked: Whether to lock them.
        """
        for widget in (
            self.session_edit,
            self.folder_edit,
            self.browse_button,
            self.format_box,
            self.wait_box,
            self.numbers_box,
        ):
            widget.setEnabled(not locked)

    def set_finding_enabled(self, enabled: bool) -> None:
        """Enables "Find cameras": between recordings, while no search runs.

        Args:
            enabled: Whether to enable it.
        """
        self.find_button.setEnabled(enabled)

    # ── internals ────────────────────────────────────────────────────────────

    def _lay_out(self) -> None:
        """Arranges the two rows: where recordings go, then how they are recorded."""
        where = QHBoxLayout()
        where.addWidget(QLabel("Session"))
        where.addWidget(self.session_edit)
        where.addSpacing(GROUP_SPACING)
        where.addWidget(QLabel("Folder"))
        where.addWidget(self.folder_edit, 1)
        where.addWidget(self.browse_button)
        where.addSpacing(GROUP_SPACING)
        where.addWidget(self.find_button)

        how = QHBoxLayout()
        how.addWidget(QLabel("Format"))
        how.addWidget(self.format_box)
        how.addSpacing(GROUP_SPACING)
        how.addWidget(self.wait_box)
        how.addSpacing(GROUP_SPACING)
        how.addWidget(self.numbers_box)
        how.addStretch(1)

        rows = QVBoxLayout(self)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(SPACING)  # the rows' own layouts take it on
        rows.addLayout(where)
        rows.addLayout(how)

    def _format_activated(self, index: int) -> None:
        """Signals the format chosen in the list.

        Args:
            index: The position of the chosen format in the list.
        """
        self.format_chosen.emit(self.format_box.itemData(index))

    def _browse(self) -> None:
        """Lets the user pick the folder for the video files in a dialog."""
        folder = QFileDialog.getExistingDirectory(self, "Folder for the video files", self.folder_text)
        if folder:
            self.folder_edit.setText(folder)
