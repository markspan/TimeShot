"""The bottom of the main window: the record button, what the rig is doing, and the free disk space."""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from timeshot.gui.theme import PLAIN_BUTTON_STYLE, RECORD, RECORD_BUTTON_STYLE, SPACING, WARNING, colored
from timeshot.recording.recorder import RecordingResult
from timeshot.rig import CameraRig, Phase

# Recording time left is shown only below this many hours; above, the disk is plenty.
MAX_HOURS_SHOWN = 100


class RecordBar(QWidget):
    """The record button, a line on what the rig is doing, and the free disk space.

    The button records, cancels the waiting for LabRecorder, or stops the recording, as the
    phase asks; the window decides what a click does.

    Signals:
        record_clicked: The button was clicked.
    """

    record_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Builds the bar.

        Args:
            parent: The widget it belongs to.
        """
        super().__init__(parent)
        self.record_button = QPushButton("Record")
        self.record_button.setMinimumSize(170, 44)
        self.record_button.clicked.connect(self.record_clicked)
        self.phase_label = QLabel()
        self.phase_label.setTextFormat(Qt.TextFormat.RichText)
        self.phase_label.setWordWrap(True)
        self.disk_label = QLabel()
        self.disk_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        layout.addWidget(self.record_button)
        layout.addSpacing(SPACING)
        layout.addWidget(self.phase_label, 1)
        layout.addWidget(self.disk_label)

    def show_phase(self, rig: CameraRig, session: str, *, can_record: bool) -> None:
        """Shows what the rig is doing, and what the button does now.

        Args:
            rig: The rig.
            session: The session, for the stream names LabRecorder should record.
            can_record: Whether a recording can start (every camera open, no problems).
        """
        match rig.phase:
            case Phase.PREVIEW:
                self._show_button("●  Record", RECORD_BUTTON_STYLE, enabled=can_record)
                self.phase_label.setText(_last_recording(rig.results))
            case Phase.WAITING:
                connected = sum(state.connected for state in rig.cameras.values())
                streams = ", ".join(html.escape(s.setup.names(session).stream) for s in rig.cameras.values())
                self._show_button("Cancel", PLAIN_BUTTON_STYLE)
                self.phase_label.setText(
                    f"{colored('Waiting for LabRecorder', WARNING, bold=True)}: "
                    f"record {streams} in LabRecorder ({connected} of {len(rig.cameras)} connected)."
                )
            case Phase.RECORDING:
                self._show_button("■  Stop", PLAIN_BUTTON_STYLE + " QPushButton { font-weight: bold; }")
                self.phase_label.setText(
                    f"{colored('● Recording', RECORD, bold=True)} {_duration(rig.elapsed)}"
                )
            case Phase.FINISHING:
                self._show_button("Finishing…", PLAIN_BUTTON_STYLE, enabled=False)
                self.phase_label.setText("Completing the video files…")

    def show_disk(self, folder: Path, bytes_per_second: float) -> None:
        """Shows the free space on the disk the video files go to.

        Args:
            folder: The folder for the video files; it need not exist yet.
            bytes_per_second: How fast the cameras fill the disk while recording; 0 if unknown.
        """
        while (
            not folder.exists() and folder != folder.parent
        ):  # the disk is that of its nearest existing parent
            folder = folder.parent
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            self.disk_label.setText("")
            return
        text = f"{free / 1e9:,.0f} GB free"
        if bytes_per_second > 0 and (hours := free / bytes_per_second / 3600) < MAX_HOURS_SHOWN:
            text += f" (about {hours:,.1f} h)"
        self.disk_label.setText(text)

    def _show_button(self, text: str, style: str, *, enabled: bool = True) -> None:
        """Sets the record button's text, look and whether it can be clicked.

        Args:
            text: The text.
            style: Its style sheet (see :mod:`timeshot.gui.theme`).
            enabled: Whether it can be clicked.
        """
        self.record_button.setText(text)
        self.record_button.setEnabled(enabled)
        self.record_button.setStyleSheet(style)


def _last_recording(results: dict[str, RecordingResult]) -> str:
    """A line on how the last recording went.

    Args:
        results: Each camera's result, by camera key.

    Returns:
        Rich text: the files and frames recorded, or the cameras that had problems; empty
        before the first recording.
    """
    if not results:
        return ""
    failed = [key for key, result in results.items() if not result.ok]
    if failed:
        return colored(f"Last recording: problems with {', '.join(failed)}.", RECORD)
    frames = sum(result.file.frames_written for result in results.values())
    return f"Last recording: {len(results)} file(s), {frames:,} frames, every frame with its marker."


def _duration(seconds: float) -> str:
    """A duration as a clock shows it: "1:02:03"."""
    whole = int(seconds)
    return f"{whole // 3600}:{whole // 60 % 60:02}:{whole % 60:02}"
