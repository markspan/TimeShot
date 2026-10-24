"""The camera rig: a recorder process per enabled camera, moved through a recording together.

:class:`CameraRig` takes the cameras through four phases (see :class:`Phase`): previewing,
waiting for LabRecorder, recording, and completing the files. The GUI and the command line
drive it alike, calling :meth:`CameraRig.poll` regularly to take in what the recorder
processes report.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from timeshot.cameras.base import CameraMode
from timeshot.recording.protocol import (
    CloseStream,
    Ended,
    Failed,
    Opened,
    OpenStream,
    Recorded,
    RecorderConfig,
    Report,
    StartRecording,
    Status,
)
from timeshot.recording.recorder import RecorderStats, RecordingResult
from timeshot.recording.worker import RecorderProcess
from timeshot.setup import CameraSetup


class Phase(Enum):
    """What the rig is doing."""

    PREVIEW = "preview"  # cameras open and previewed; nothing recorded, no LSL streams
    WAITING = "waiting"  # LSL streams open, waiting for LabRecorder to connect to them all
    RECORDING = "recording"  # every frame gets a marker and goes into its file
    FINISHING = "finishing"  # the recorder processes complete their files


@dataclass
class CameraState:
    """The live state of one camera's recorder process, as its reports tell it.

    Attributes:
        setup: The camera's settings.
        process: Its recorder process.
        mode: The mode the camera delivers in, once it is open.
        stats: Its newest status.
        error: What went wrong last, if anything did.
        result: How its last recording went, once its file is complete.
        ended: Whether its recorder process has ended (the camera is then unavailable).
    """

    setup: CameraSetup
    process: RecorderProcess
    mode: CameraMode | None = None
    stats: RecorderStats | None = None
    error: str | None = None
    result: RecordingResult | None = None
    ended: bool = False

    @property
    def ready(self) -> bool:
        """Whether the camera is open and its recorder process running."""
        return self.mode is not None and not self.ended

    @property
    def connected(self) -> bool:
        """Whether LabRecorder is connected to the camera's LSL stream."""
        return self.stats is not None and self.stats.consumers

    def take_in(self, report: Report) -> None:
        """Updates the state with a report of the recorder process.

        Args:
            report: The report.
        """
        match report:
            case Opened(mode):
                self.mode = mode
            case Status(stats):
                self.stats = stats
            case Recorded(result):
                self.result = result
            case Failed(message, fatal):
                self.error = message
                self.ended = self.ended or fatal
            case Ended():
                self.ended = True


@dataclass(frozen=True, slots=True)
class _Plan:
    """What :meth:`CameraRig.start` was asked for, kept for when the recording begins."""

    session: str
    folder: Path
    frame_numbers: bool


class CameraRig:
    """Runs a recorder process per enabled camera, and moves them through a recording together.

    Attributes:
        phase: What the rig is doing.
        cameras: The state of each camera's recorder process, by camera key.
        started_at: When the recording started (``time.monotonic()``); None when not recording.
        results: How each camera's last recording went, by camera key.
        errors: What went wrong with each camera since the last :meth:`start`, by camera key.
    """

    def __init__(self) -> None:
        """Starts without cameras, in the preview phase; :meth:`preview` opens cameras."""
        self.phase = Phase.PREVIEW
        self.cameras: dict[str, CameraState] = {}
        self.started_at: float | None = None
        self.results: dict[str, RecordingResult] = {}
        self.errors: dict[str, str] = {}
        self._plan: _Plan | None = None

    @property
    def ready(self) -> bool:
        """Whether there are cameras, and every one is open: a recording can start."""
        return bool(self.cameras) and all(state.ready for state in self.cameras.values())

    @property
    def elapsed(self) -> float:
        """Seconds since the recording started; 0 when not recording."""
        return time.monotonic() - self.started_at if self.started_at is not None else 0.0

    # ── preview ──────────────────────────────────────────────────────────────

    def preview(self, setups: Iterable[CameraSetup]) -> None:
        """Opens the enabled cameras and closes the others.

        A camera that is open already stays open, unless its mode changed or its recorder
        process ended: it is then opened again.

        Args:
            setups: Every camera's settings.

        Raises:
            RuntimeError: A recording runs (cameras change only between recordings).
        """
        if self.phase is not Phase.PREVIEW:
            raise RuntimeError("We would kindly ask you to change cameras only between recordings.")
        wanted = {setup.camera.key: setup for setup in setups if setup.enabled and setup.mode is not None}
        for key, state in list(self.cameras.items()):
            # Compared with the mode the process was started in: the GUI edits setups in place.
            if key not in wanted or wanted[key].mode != state.process.config.mode or state.ended:
                state.process.shutdown()
                del self.cameras[key]
        for key, setup in wanted.items():
            if key in self.cameras:
                self.cameras[key].setup = setup  # names may have changed; the camera stays open
            else:
                assert setup.mode is not None  # wanted holds cameras with a mode only
                self.cameras[key] = CameraState(
                    setup, RecorderProcess(RecorderConfig(setup.camera, setup.mode))
                )

    # ── recording ────────────────────────────────────────────────────────────

    def start(
        self, session: str, folder: Path, *, wait_for_consumers: bool = True, frame_numbers: bool = False
    ) -> None:
        """Opens every camera's LSL stream; the recording starts once LabRecorder records them all.

        Args:
            session: The session, for ``{s}`` in the names.
            folder: The folder for the video files; created if it does not exist.
            wait_for_consumers: Whether to wait for LabRecorder to connect to every stream
                (see :meth:`poll`); without, the recording starts at once.
            frame_numbers: Whether to burn each frame's number into its picture.

        Raises:
            RuntimeError: A recording runs already, or not every camera is open.
            OSError: The folder could not be created.
        """
        if self.phase is not Phase.PREVIEW:
            raise RuntimeError("We are sorry, but a recording is already running.")
        if not self.cameras:
            raise RuntimeError(
                "We are sorry, but no camera is selected. Would you be so kind as to select one?"
            )
        if not self.ready:
            not_ready = [state.setup.camera.name for state in self.cameras.values() if not state.ready]
            raise RuntimeError(
                f"We are terribly sorry, but these cameras are not open yet: {', '.join(not_ready)}."
            )
        folder.mkdir(parents=True, exist_ok=True)
        self._plan = _Plan(session, folder, frame_numbers)
        self.results = {}
        self.errors = {}
        for state in self.cameras.values():
            state.result = None
            names = state.setup.names(session)
            state.process.send(OpenStream(names.stream, names.file))
        self.phase = Phase.WAITING
        if not wait_for_consumers:
            self._record()

    def cancel(self) -> None:
        """Stops waiting for LabRecorder, and closes the LSL streams."""
        if self.phase is Phase.WAITING:
            self._close_streams()
            self.phase = Phase.PREVIEW

    def stop(self) -> None:
        """Stops the recording (or the waiting for LabRecorder).

        The files are complete once :meth:`poll` has brought the phase back to PREVIEW.
        """
        if self.phase is Phase.WAITING:
            self.cancel()
        elif self.phase is Phase.RECORDING:
            self._close_streams()
            self.phase = Phase.FINISHING

    def _record(self) -> None:
        """Starts recording: from their next frame on, the cameras send markers and write files."""
        assert self._plan is not None  # set by start(), which comes first
        for state in self.cameras.values():
            path = self._plan.folder / state.setup.names(self._plan.session).file
            state.process.send(StartRecording(str(path), self._plan.frame_numbers))
        self.started_at = time.monotonic()
        self.phase = Phase.RECORDING

    def _close_streams(self) -> None:
        """Tells every recorder process to stop recording and withdraw its LSL stream."""
        for state in self.cameras.values():
            state.process.send(CloseStream())

    # ── progress ─────────────────────────────────────────────────────────────

    def poll(self) -> None:
        """Takes in the recorder processes' reports, and moves the rig on accordingly.

        Waiting for LabRecorder, the recording starts once it is connected to every stream;
        finishing, the rig returns to preview once every file is complete (or its recorder
        process has ended).
        """
        for key, state in self.cameras.items():
            self._take_in(key, state, state.process.reports())
        cameras = self.cameras.values()
        if self.phase is Phase.WAITING and all(state.connected for state in cameras):
            self._record()
        elif self.phase is Phase.FINISHING and all(
            state.result is not None or state.ended for state in cameras
        ):
            self.phase = Phase.PREVIEW
            self.started_at = None

    def shutdown(self) -> None:
        """Ends every recorder process, completing the files of a recording that runs."""
        for state in self.cameras.values():  # all at once: each completes its own file meanwhile
            state.process.request_shutdown()
        for key, state in self.cameras.items():
            self._take_in(key, state, state.process.shutdown())
        self.cameras.clear()
        self.phase = Phase.PREVIEW
        self.started_at = None

    def _take_in(self, key: str, state: CameraState, reports: Iterable[Report]) -> None:
        """Takes a camera's reports into its state, and into :attr:`results` and :attr:`errors`.

        Args:
            key: The camera's key.
            state: The camera's state.
            reports: Its recorder process's reports, oldest first.
        """
        for report in reports:
            state.take_in(report)
            if isinstance(report, Recorded):
                self.results[key] = report.result
            elif isinstance(report, Failed):
                self.errors[key] = report.message
