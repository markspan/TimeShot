"""TimeShot's main window: the settings for every camera, a card per camera, and the record button.

The window ties its parts together: the :class:`~timeshot.gui.session_bar.SessionBar` at the
top, the :class:`~timeshot.gui.card_grid.CardGrid` of :class:`~timeshot.gui.camera_card.CameraCard`
in the middle and the :class:`~timeshot.gui.record_bar.RecordBar` at the bottom. It finds the
cameras, keeps the settings checked and remembered, and drives the
:class:`~timeshot.rig.CameraRig`, whose state a timer shows several times per second.
"""

from __future__ import annotations

import concurrent.futures
import functools
import logging
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QVBoxLayout, QWidget

from timeshot import __version__
from timeshot.cameras import CameraError, CameraInfo, default_backend_name, list_cameras
from timeshot.gui.camera_card import CameraCard
from timeshot.gui.card_grid import CardGrid
from timeshot.gui.record_bar import RecordBar
from timeshot.gui.session_bar import SessionBar
from timeshot.gui.theme import SPACING, app_icon
from timeshot.rig import CameraRig, Phase
from timeshot.settings import Settings
from timeshot.setup import CameraSetup, Problem, check

log = logging.getLogger(__name__)

# How often the window takes in the recorders' reports and previews (previews come at 20 fps).
REFRESH_MS = 50


class MainWindow(QMainWindow):
    """TimeShot's main window.

    Attributes:
        rig: The recorder processes of the enabled cameras.
        session_bar: The settings for every camera, at the top.
        card_grid: The camera cards, in the middle.
        record_bar: The record button and the rig's state, at the bottom.
    """

    def __init__(self, settings_path: Path) -> None:
        """Builds the window, and starts looking for cameras.

        Args:
            settings_path: The settings file, read now and written as settings change.
        """
        super().__init__()
        self.setWindowTitle("TimeShot")
        self.setWindowIcon(app_icon())
        self.resize(1180, 800)
        self._settings_path = settings_path
        self._settings = Settings.load(settings_path)
        if not self._settings.folder:
            self._settings.folder = str(_suggested_folder())

        self.rig = CameraRig()
        self._setups: list[CameraSetup] = []
        self._cards: dict[str, CameraCard] = {}  # by camera key
        self._problems: list[Problem] = []
        # Looking for cameras takes a few seconds, so it runs in the background.
        self._scanner = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera scan")
        self._scan: concurrent.futures.Future[list[CameraInfo]] | None = None

        self.session_bar = SessionBar(self._settings)
        self.card_grid = CardGrid()
        self.record_bar = RecordBar()
        self.setCentralWidget(self._central_widget())
        self.statusBar().showMessage(
            f"TimeShot {__version__} · cameras via {default_backend_name()} · settings in {settings_path}"
        )
        self.session_bar.session_changed.connect(self._session_changed)
        self.session_bar.changed.connect(self._settings_changed)
        self.session_bar.format_chosen.connect(self._format_chosen)
        self.session_bar.find_cameras.connect(self.find_cameras)
        self.record_bar.record_clicked.connect(self._record_clicked)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self.find_cameras()

    def _central_widget(self) -> QWidget:
        """The session bar, the card grid (taking the room left) and the record bar, stacked."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(SPACING)
        layout.addWidget(self.session_bar)
        layout.addWidget(self.card_grid, 1)
        layout.addWidget(self.record_bar)
        return root

    # ── cameras ──────────────────────────────────────────────────────────────

    def find_cameras(self) -> None:
        """Looks for cameras in the background; :meth:`_refresh` picks up what it finds.

        The open cameras are closed first: an open camera may not report its modes. Does
        nothing while a recording runs or a search is under way.
        """
        if self.rig.phase is not Phase.PREVIEW or self._scan is not None:
            return
        self._remember()
        self.rig.shutdown()
        self._cards.clear()
        self._setups = []
        self.card_grid.show_message("Looking for cameras…")
        self.session_bar.set_finding_enabled(False)
        self._scan = self._scanner.submit(list_cameras)

    def _cameras_found(self, scan: concurrent.futures.Future[list[CameraInfo]]) -> None:
        """Shows a card for each camera found, with its remembered settings, and opens the cameras.

        Args:
            scan: The finished search.
        """
        self._scan = None
        self.session_bar.set_finding_enabled(True)
        try:
            cameras = scan.result()
        except CameraError as error:
            self.card_grid.show_message(f"Could not look for cameras: {error}")
            return
        if not cameras:
            self.card_grid.show_message("No cameras found. Plug one in and click “Find cameras”.")
            return
        self._setups = self._settings.setups(cameras)
        self._cards = {setup.camera.key: self._card(setup) for setup in self._setups}
        self.card_grid.set_cards(self._cards.values())
        self._open_cameras()
        self._settings_changed()

    def _card(self, setup: CameraSetup) -> CameraCard:
        """A camera's card, connected to the window.

        Args:
            setup: The camera's settings, which the card edits.

        Returns:
            The card.
        """
        card = CameraCard(setup, self.session_bar.session)
        card.changed.connect(self._settings_changed)
        card.reopen.connect(self._open_cameras)
        card.make_default.connect(functools.partial(self._make_default, card))
        card.set_default_templates(self._settings.templates)
        return card

    def _open_cameras(self) -> None:
        """Opens the enabled cameras in their chosen modes, and closes the others (between recordings)."""
        if self.rig.phase is Phase.PREVIEW:
            self.rig.preview(self._setups)

    # ── settings ─────────────────────────────────────────────────────────────

    def _session_changed(self, session: str) -> None:
        """Shows every camera's names for the new session, and checks them.

        Args:
            session: The new session.
        """
        for card in self._cards.values():
            card.set_session(session)
        self._settings_changed()

    def _format_chosen(self, extension: str) -> None:
        """Gives every camera's file name, and that of cameras yet to come, the chosen format.

        Args:
            extension: The format's extension, e.g. ".mp4".
        """
        for card in self._cards.values():
            card.set_file_extension(extension)
        self._settings.templates = self._settings.templates.with_extension(extension)
        self._show_default_templates()
        self._settings_changed()

    def _make_default(self, card: CameraCard) -> None:
        """Makes a camera's name templates the ones new cameras start with, and saves them.

        Args:
            card: The camera's card.
        """
        self._settings.templates = card.setup.templates
        self._show_default_templates()
        self._remember()

    def _show_default_templates(self) -> None:
        """Tells every card the templates new cameras start with."""
        for card in self._cards.values():
            card.set_default_templates(self._settings.templates)

    def _settings_changed(self) -> None:
        """Checks the settings after an edit: shows each card its problems, and the common format."""
        self._problems = check(self._setups, self.session_bar.session, self.session_bar.folder)
        for key, card in self._cards.items():
            card.set_problems([problem for problem in self._problems if problem.camera == key])
        extensions = {Path(setup.templates.file).suffix.lower() for setup in self._setups if setup.enabled}
        self.session_bar.show_format(extensions.pop() if len(extensions) == 1 else None)

    def _remember(self) -> None:
        """Saves the current settings to the settings file (a failure is only logged)."""
        settings = self._settings
        settings.session = self.session_bar.session
        settings.folder = self.session_bar.folder_text
        settings.wait_for_labrecorder = self.session_bar.wait_for_labrecorder
        settings.frame_numbers = self.session_bar.frame_numbers
        settings.remember(self._setups)
        try:
            settings.save(self._settings_path)
        except OSError as error:
            log.warning("Could not save the settings: %s", error)

    # ── recording ────────────────────────────────────────────────────────────

    def _record_clicked(self) -> None:
        """Records, cancels the waiting for LabRecorder, or stops the recording (once confirmed)."""
        match self.rig.phase:
            case Phase.PREVIEW:
                self.start_recording()
            case Phase.WAITING:
                self.rig.cancel()
            case Phase.RECORDING:
                if self._ask("Stop recording", "Stop the recording?"):
                    self.rig.stop()

    def start_recording(self, *, confirm: bool = True) -> bool:
        """Starts a recording with the current settings.

        Args:
            confirm: Whether to ask before overwriting existing files.

        Returns:
            Whether the recording started (it may wait for LabRecorder first). If not, the
            user has been told why, or declined to overwrite files.
        """
        self._settings_changed()
        blocking = [problem.message for problem in self._problems if not problem.confirm]
        if blocking:
            QMessageBox.warning(self, "Cannot record", "\n".join(blocking))
            return False
        overwritten = [problem.message for problem in self._problems if problem.confirm]
        if (
            overwritten
            and confirm
            and not self._ask(
                "Overwrite recordings?", "\n".join(overwritten) + "\n\nOverwrite them?", default_no=True
            )
        ):
            return False
        self._remember()
        try:
            self.rig.start(
                self.session_bar.session,
                self.session_bar.folder,
                wait_for_consumers=self.session_bar.wait_for_labrecorder,
                frame_numbers=self.session_bar.frame_numbers,
            )
        except (RuntimeError, OSError) as error:
            QMessageBox.warning(self, "Cannot record", str(error))
            return False
        return True

    def _ask(self, title: str, question: str, *, default_no: bool = False) -> bool:
        """Asks a yes-or-no question in a dialog.

        Args:
            title: The dialog's title.
            question: The question.
            default_no: Whether "No" is the default answer (for a question whose "Yes" loses something).

        Returns:
            Whether the answer is yes.
        """
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        default = QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.NoButton
        return QMessageBox.question(self, title, question, buttons, default) == QMessageBox.StandardButton.Yes

    # ── refresh ──────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Brings the window up to date, every :data:`REFRESH_MS`.

        Picks up the cameras a search found, takes in the recorder processes' reports, and
        shows each camera's state and preview, what the rig is doing and the free disk space.
        """
        if self._scan is not None and self._scan.done():
            self._cameras_found(self._scan)
        self.rig.poll()
        phase = self.rig.phase
        locked = phase is not Phase.PREVIEW
        for key, card in self._cards.items():
            state = self.rig.cameras.get(key)
            card.set_locked(locked)
            card.show_state(state, phase)
            if state is not None and (image := state.process.preview.read()) is not None:
                card.show_preview(image)
        self.session_bar.set_locked(locked)
        self.session_bar.set_finding_enabled(not locked and self._scan is None)
        can_record = self.rig.ready and all(problem.confirm for problem in self._problems)
        self.record_bar.show_phase(self.rig, self.session_bar.session, can_record=can_record)
        self.record_bar.show_disk(self.session_bar.folder, self._bytes_per_second())

    def _bytes_per_second(self) -> float:
        """How fast the open cameras would fill the disk, from their frame sizes and rates."""
        return sum(
            state.stats.frame_bytes * state.stats.fps for state in self.rig.cameras.values() if state.stats
        )

    # ── closing ──────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closes the window once a running recording may stop: completes its files, saves the settings.

        Args:
            event: The close request; ignored if the user keeps the recording running.
        """
        if self.rig.phase in (Phase.RECORDING, Phase.WAITING) and not self._ask(
            "Quit TimeShot", "A recording is running. Stop it and quit?"
        ):
            event.ignore()
            return
        self._timer.stop()
        self._remember()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.rig.shutdown()  # completes any file being recorded
        finally:
            QApplication.restoreOverrideCursor()
        self._scanner.shutdown(wait=False, cancel_futures=True)
        event.accept()


def _suggested_folder() -> Path:
    """The folder suggested for the video files: "TimeShot" in the user's video folder."""
    videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
    return Path(videos or Path.home()) / "TimeShot"
