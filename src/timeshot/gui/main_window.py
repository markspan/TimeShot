"""TimeShot's main window: session settings, a card per camera, and the record button."""

from __future__ import annotations

import concurrent.futures
import html
import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from timeshot import __version__
from timeshot.cameras import CameraError, CameraInfo, default_backend_name, list_cameras
from timeshot.gui.camera_card import CameraCard
from timeshot.gui.theme import ACCENT, RECORD, WARNING, app_icon
from timeshot.session import CameraSetup, Phase, Problem, Session, check
from timeshot.settings import Settings

log = logging.getLogger(__name__)

# How often the window takes in the recorders' reports and previews (previews come at 20 fps).
REFRESH_MS = 50
CARD_WIDTH = 440  # the grid gets as many columns of about this width as fit

_RECORD_STYLE = (
    f"QPushButton {{ background: {RECORD}; color: white; font-weight: bold; border: none;"
    " border-radius: 8px; } QPushButton:disabled { background: palette(mid); }"
)
_PLAIN_STYLE = (
    "QPushButton { background: palette(button); border: 1px solid palette(mid); border-radius: 8px; }"
    f" QPushButton:hover {{ border-color: {ACCENT}; }}"
)


class MainWindow(QMainWindow):
    def __init__(self, settings_path: Path) -> None:
        super().__init__()
        self.setWindowTitle("TimeShot")
        self.setWindowIcon(app_icon())
        self.resize(1180, 800)
        self._settings_path = settings_path
        self._settings = Settings.load(settings_path)
        if not self._settings.folder:
            videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
            self._settings.folder = str(Path(videos or Path.home()) / "TimeShot")
        self._session = Session(default_backend_name())
        self._setups: list[CameraSetup] = []
        self._cards: dict[str, CameraCard] = {}
        self._problems: list[Problem] = []
        self._scan: concurrent.futures.Future[list[CameraInfo]] | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="camera scan"
        )

        self.setCentralWidget(self._build())
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self.rescan()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        layout.addLayout(self._build_top_bar())
        layout.addWidget(self._build_card_area(), 1)
        layout.addLayout(self._build_bottom_bar())
        self.statusBar().showMessage(f"TimeShot {__version__} · cameras via {default_backend_name()}")
        return root

    def _build_top_bar(self) -> QHBoxLayout:
        """Session, folder and recording options, which apply to every camera."""
        self.session_edit = QLineEdit(self._settings.session)
        self.session_edit.setMaximumWidth(140)
        self.session_edit.setToolTip("Fills in {s} in the names: a subject or session number")
        self.session_edit.textChanged.connect(self._session_changed)
        self.folder_edit = QLineEdit(self._settings.folder)
        self.folder_edit.setToolTip("Where the video files go")
        self.folder_edit.textChanged.connect(self._settings_changed)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.wait_box = QCheckBox("Wait for LabRecorder")
        self.wait_box.setChecked(self._settings.wait_for_labrecorder)
        self.wait_box.setToolTip(
            "Start recording only once LabRecorder records every camera's stream,\n"
            "so that no frame goes without its marker in the XDF file"
        )
        self.wait_box.toggled.connect(self._settings_changed)
        self.numbers_box = QCheckBox("Burn frame numbers into the video")
        self.numbers_box.setChecked(self._settings.frame_numbers)
        self.numbers_box.setToolTip(
            "Draws each frame's number into its picture. The frames are then re-encoded,\n"
            "which takes much more CPU; without it, the camera's own JPEG frames are\n"
            "recorded untouched and the numbers are in the LSL markers only."
        )
        self.numbers_box.toggled.connect(self._settings_changed)
        self.rescan_button = QPushButton("Find cameras")
        self.rescan_button.setToolTip("Look for cameras again, e.g. after plugging one in")
        self.rescan_button.clicked.connect(self.rescan)

        top = QHBoxLayout()
        top.addWidget(QLabel("Session"))
        top.addWidget(self.session_edit)
        top.addSpacing(16)
        top.addWidget(QLabel("Folder"))
        top.addWidget(self.folder_edit, 1)
        top.addWidget(browse)
        top.addSpacing(16)
        top.addWidget(self.wait_box)
        top.addWidget(self.numbers_box)
        top.addSpacing(16)
        top.addWidget(self.rescan_button)
        return top

    def _build_card_area(self) -> QScrollArea:
        """A scrolling grid for the camera cards, showing a message until cameras are found."""
        self.grid = QGridLayout()
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        grid_host = QWidget()
        grid_host.setLayout(self.grid)
        self.placeholder = QLabel("Looking for cameras…")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.grid.addWidget(self.placeholder, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setWidget(grid_host)
        return self._scroll

    def _build_bottom_bar(self) -> QHBoxLayout:
        """The record button, what the session is doing, and the free disk space."""
        self.record_button = QPushButton("Record")
        self.record_button.setMinimumSize(170, 44)
        self.record_button.clicked.connect(self._record_clicked)
        self.phase_label = QLabel()
        self.phase_label.setTextFormat(Qt.TextFormat.RichText)
        self.phase_label.setWordWrap(True)
        self.disk_label = QLabel()
        self.disk_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom = QHBoxLayout()
        bottom.addWidget(self.record_button)
        bottom.addSpacing(12)
        bottom.addWidget(self.phase_label, 1)
        bottom.addWidget(self.disk_label)
        return bottom

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._lay_out_cards()

    def _lay_out_cards(self) -> None:
        if not self._cards:
            return
        columns = max(1, self._scroll.viewport().width() // CARD_WIDTH)
        for position, card in enumerate(self._cards.values()):
            self.grid.addWidget(card, position // columns, position % columns)  # re-adding moves it
        for column in range(self.grid.columnCount()):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)

    # ── cameras ──────────────────────────────────────────────────────────────

    def rescan(self) -> None:
        """Looks for cameras in the background (closing the open ones first: an open camera
        may not report its modes)."""
        if self._session.phase is not Phase.PREVIEW or self._scan is not None:
            return
        self._remember()
        self._session.shutdown()
        for card in self._cards.values():
            card.deleteLater()
        self._cards.clear()
        self._setups = []
        self.placeholder.setText("Looking for cameras…")
        self.placeholder.show()
        self.grid.addWidget(self.placeholder, 0, 0)
        self.rescan_button.setEnabled(False)
        self._scan = self._executor.submit(list_cameras)

    def _scan_finished(self, scan: concurrent.futures.Future[list[CameraInfo]]) -> None:
        self._scan = None
        self.rescan_button.setEnabled(True)
        try:
            cameras = scan.result()
        except CameraError as error:
            self.placeholder.setText(f"Could not look for cameras: {error}")
            return
        if not cameras:
            self.placeholder.setText("No cameras found. Plug one in and click “Find cameras”.")
            return
        self.placeholder.hide()
        self._setups = self._settings.setups(cameras)
        for setup in self._setups:
            card = CameraCard(setup, self.session_edit.text())
            card.changed.connect(self._settings_changed)
            card.reopen.connect(self._open_cameras)
            self._cards[setup.camera.key] = card
        self._lay_out_cards()
        self._open_cameras()
        self._settings_changed()

    def _open_cameras(self) -> None:
        if self._session.phase is Phase.PREVIEW:
            self._session.preview(self._setups)

    # ── settings ─────────────────────────────────────────────────────────────

    def _session_changed(self, text: str) -> None:
        for card in self._cards.values():
            card.set_session(text)
        self._settings_changed()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder for the video files", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _settings_changed(self) -> None:
        problems = check(self._setups, self.session_edit.text(), self._folder())
        for key, card in self._cards.items():
            card.set_problems([p for p in problems if p.camera == key])
        self._problems = problems

    def _folder(self) -> Path:
        return Path(self.folder_edit.text().strip() or ".").expanduser()

    def _remember(self) -> None:
        settings = self._settings
        settings.session = self.session_edit.text()
        settings.folder = self.folder_edit.text()
        settings.wait_for_labrecorder = self.wait_box.isChecked()
        settings.frame_numbers = self.numbers_box.isChecked()
        settings.remember(self._setups)
        try:
            settings.save(self._settings_path)
        except OSError as error:
            log.warning("Could not save the settings: %s", error)

    # ── recording ────────────────────────────────────────────────────────────

    def _record_clicked(self) -> None:
        phase = self._session.phase
        if phase is Phase.PREVIEW:
            self.start_recording()
        elif phase is Phase.WAITING:
            self._session.cancel()
        elif phase is Phase.RECORDING:
            answer = QMessageBox.question(
                self,
                "Stop recording",
                "Stop the recording?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._session.stop()

    def start_recording(self, *, confirm: bool = True) -> bool:
        """Starts a recording with the current settings; asks before overwriting files."""
        self._settings_changed()
        blocking = [p for p in self._problems if not p.confirm]
        if blocking:
            QMessageBox.warning(self, "Cannot record", "\n".join(p.message for p in blocking))
            return False
        overwrite = [p.message for p in self._problems if p.confirm]
        if overwrite and confirm:
            answer = QMessageBox.question(
                self,
                "Overwrite recordings?",
                "\n".join(overwrite) + "\n\nOverwrite them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._remember()
        try:
            self._session.start(
                self.session_edit.text(),
                self._folder(),
                wait_for_consumers=self.wait_box.isChecked(),
                frame_numbers=self.numbers_box.isChecked(),
            )
        except (RuntimeError, OSError) as error:
            QMessageBox.warning(self, "Cannot record", str(error))
            return False
        return True

    @property
    def session(self) -> Session:
        return self._session

    # ── periodic refresh ─────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self._scan is not None and self._scan.done():
            self._scan_finished(self._scan)
        session = self._session
        session.poll()
        phase = session.phase
        for key, card in self._cards.items():
            state = session.cameras.get(key)
            card.set_locked(phase is not Phase.PREVIEW)
            card.show_state(state, phase)
            if state is not None and (image := state.process.preview.read()) is not None:
                card.show_preview(image)
        self.rescan_button.setEnabled(phase is Phase.PREVIEW and self._scan is None)
        for widget in (self.session_edit, self.folder_edit, self.wait_box, self.numbers_box):
            widget.setEnabled(phase is Phase.PREVIEW)
        self._show_phase(phase)
        self._show_disk()

    def _show_phase(self, phase: Phase) -> None:
        session = self._session
        button = self.record_button
        if phase is Phase.PREVIEW:
            ready = bool(session.cameras) and all(s.ready for s in session.cameras.values())
            button.setText("●  Record")
            button.setEnabled(ready and not any(not p.confirm for p in self._problems))
            button.setStyleSheet(_RECORD_STYLE)
            self.phase_label.setText(self._summary())
        elif phase is Phase.WAITING:
            connected = sum(bool(s.stats and s.stats.consumers) for s in session.cameras.values())
            streams = ", ".join(
                html.escape(s.setup.names(self.session_edit.text())[1]) for s in session.cameras.values()
            )
            button.setText("Cancel")
            button.setEnabled(True)
            button.setStyleSheet(_PLAIN_STYLE)
            self.phase_label.setText(
                f"<span style='color:{WARNING}'><b>Waiting for LabRecorder</b></span>: "
                f"record {streams} in LabRecorder ({connected} of {len(session.cameras)} connected)."
            )
        elif phase is Phase.RECORDING:
            seconds = int(session.elapsed)
            button.setText("■  Stop")
            button.setEnabled(True)
            button.setStyleSheet(_PLAIN_STYLE + " QPushButton { font-weight: bold; }")
            self.phase_label.setText(
                f"<span style='color:{RECORD}'><b>● Recording</b></span> "
                f"{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}"
            )
        else:
            button.setText("Finishing…")
            button.setEnabled(False)
            button.setStyleSheet(_PLAIN_STYLE)
            self.phase_label.setText("Completing the video files…")

    def _summary(self) -> str:
        results = self._session.results
        if not results:
            return ""
        frames = sum(r.file.frames_written for r in results.values())
        bad = [key for key, r in results.items() if not r.ok]
        if bad:
            cameras = html.escape(", ".join(bad))
            return f"<span style='color:{RECORD}'>Last recording: problems with {cameras}.</span>"
        return f"Last recording: {len(results)} file(s), {frames:,} frames, every frame with its marker."

    def _show_disk(self) -> None:
        folder = self._folder()
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            self.disk_label.setText("")
            return
        rate = sum(s.stats.frame_bytes * s.stats.fps for s in self._session.cameras.values() if s.stats)
        text = f"{free / 1e9:,.0f} GB free"
        if rate > 0:
            hours = free / rate / 3600
            text += f" (about {hours:,.1f} h)" if hours < 100 else ""
        self.disk_label.setText(text)

    # ── closing ──────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._session.phase in (Phase.RECORDING, Phase.WAITING):
            answer = QMessageBox.question(
                self,
                "Quit TimeShot",
                "A recording is running. Stop it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._timer.stop()
        self._remember()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._session.shutdown()  # completes any file being recorded
        finally:
            QApplication.restoreOverrideCursor()
        self._executor.shutdown(wait=False, cancel_futures=True)
        event.accept()
