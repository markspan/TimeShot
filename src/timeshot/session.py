"""A recording session: which cameras, their names and files, and the recording itself.

Independent of the GUI, so the command line and the tests drive the same logic:

* :class:`CameraSetup` holds one camera's settings. Its names are templates in which ``{i}``
  is the camera's number in the list and ``{s}`` the session, as in TimeShot 1.x.
* :func:`check` finds what would go wrong before recording starts.
* :class:`Session` runs a recorder process per camera through the phases preview, waiting
  for LabRecorder, recording and finishing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from timeshot.cameras.base import CameraInfo, CameraMode, preferred_mode
from timeshot.recording.protocol import (
    CloseStream,
    Ended,
    Failed,
    Opened,
    OpenStream,
    Recorded,
    RecorderConfig,
    Shutdown,
    StartRecording,
    Status,
)
from timeshot.recording.recorder import RecorderStats, RecordingResult
from timeshot.recording.worker import RecorderProcess
from timeshot.recording.writer import FILE_TYPES

log = logging.getLogger(__name__)

# TimeShot 1.x's defaults (its config.yaml).
DEFAULT_NAME = "Camera {i}"
DEFAULT_STREAM = "Stream_{i}"
DEFAULT_FILE = "Video_{i}.avi"

_UNSAFE_IN_FILE_NAMES = set('<>:"/\\|?*')


def expand(template: str, index: int, session: str) -> str:
    """Fills in a name template: ``{i}`` is the camera's number, ``{s}`` the session."""
    return template.replace("{i}", str(index)).replace("{s}", session).strip()


@dataclass
class CameraSetup:
    """One camera's settings. ``name``, ``stream`` and ``file`` are templates (see :func:`expand`)."""

    camera: CameraInfo
    index: int  # the camera's number in the list: {i}
    enabled: bool = True
    mode: CameraMode | None = None
    name: str = DEFAULT_NAME
    stream: str = DEFAULT_STREAM
    file: str = DEFAULT_FILE

    def __post_init__(self) -> None:
        if self.mode is None:
            self.mode = preferred_mode(self.camera.modes)

    def names(self, session: str) -> tuple[str, str, str]:
        """The camera, stream and file names for ``session``."""
        return tuple(expand(t, self.index, session) for t in (self.name, self.stream, self.file))  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class Problem:
    """Something that stops, or needs confirming before, a recording."""

    message: str
    camera: str | None = None  # the camera's key, if it concerns one camera
    confirm: bool = False  # True: recording may go ahead once confirmed (e.g. overwriting)


def check(setups: Iterable[CameraSetup], session: str, folder: Path) -> list[Problem]:
    """What is wrong with these settings: blank or clashing names, unusable file names, and
    files that would be overwritten (the last only needs confirming)."""
    active = [s for s in setups if s.enabled]
    if not active:
        return [Problem("Select at least one camera.")]
    problems: list[Problem] = []
    streams: dict[str, list[str]] = {}
    files: dict[str, list[str]] = {}
    for setup in active:
        key = setup.camera.key
        _name, stream, file = setup.names(session)
        problems += _setup_problems(setup, session, folder)
        if stream:
            streams.setdefault(stream, []).append(key)
        if file and _file_problem(file) is None:
            files.setdefault(file.lower(), []).append(key)
    for kind, names in (("stream name", streams), ("file name", files)):
        for value, keys in names.items():
            if len(keys) > 1:
                problems.extend(
                    Problem(f"Another camera has the same {kind} ({value}).", key) for key in keys
                )
    return problems


def _setup_problems(setup: CameraSetup, session: str, folder: Path) -> list[Problem]:
    """What is wrong with one camera's settings on its own."""
    key = setup.camera.key
    name, stream, file = setup.names(session)
    problems = []
    if setup.mode is None:
        problems.append(Problem("This camera offers no mode TimeShot can record.", key))
    if not name:
        problems.append(Problem("The camera name is empty.", key))
    if not stream:
        problems.append(Problem("The stream name is empty.", key))
    if (file_problem := _file_problem(file)) is not None:
        problems.append(Problem(file_problem, key))
    elif (folder / file).exists():
        problems.append(Problem(f"{file} exists and will be overwritten.", key, confirm=True))
    return problems


def _file_problem(file: str) -> str | None:
    """Why ``file`` cannot be used as a video file name, or None if it can."""
    if not file:
        return "The file name is empty."
    if set(file) & _UNSAFE_IN_FILE_NAMES:
        return f"The file name {file!r} contains a character not allowed in file names."
    if Path(file).suffix.lower() not in FILE_TYPES:
        *others, last = FILE_TYPES
        return f"The file name {file!r} must end in {', '.join(others)} or {last}."
    return None


class Phase(Enum):
    PREVIEW = "preview"  # cameras open, nothing recorded, no LSL streams
    WAITING = "waiting"  # LSL streams open, waiting for LabRecorder to connect
    RECORDING = "recording"
    FINISHING = "finishing"  # files being completed


@dataclass
class CameraState:
    """The live state of one camera's recorder process."""

    setup: CameraSetup
    process: RecorderProcess
    mode: CameraMode | None = None  # as opened
    stats: RecorderStats | None = None
    error: str | None = None
    result: RecordingResult | None = None
    ended: bool = False

    @property
    def ready(self) -> bool:
        return self.mode is not None and not self.ended


@dataclass
class Session:
    """Runs the recorder processes of the enabled cameras."""

    backend: str
    phase: Phase = Phase.PREVIEW
    cameras: dict[str, CameraState] = field(default_factory=dict)
    wait_for_consumers: bool = True
    started_at: float | None = None  # time.monotonic() when recording started
    results: dict[str, RecordingResult] = field(default_factory=dict)  # of the last recording
    problems: dict[str, str] = field(default_factory=dict)  # each camera's error since start()
    # What start() was given, for when recording begins (possibly later, in poll()).
    _session: str = field(default="", init=False, repr=False)
    _folder: Path = field(default=Path(), init=False, repr=False)
    _frame_numbers: bool = field(default=False, init=False, repr=False)

    # ── preview ──────────────────────────────────────────────────────────────

    def preview(self, setups: Iterable[CameraSetup]) -> None:
        """Opens the enabled cameras (reopening those whose mode changed) and closes the rest."""
        if self.phase is not Phase.PREVIEW:
            raise RuntimeError("change cameras only between recordings")
        wanted = {s.camera.key: s for s in setups if s.enabled and s.mode is not None}
        for key in list(self.cameras):
            state = self.cameras[key]
            # Compared with the mode the process was started in: the GUI edits setups in place.
            if key not in wanted or wanted[key].mode != state.process.config.mode or state.ended:
                state.process.shutdown()
                del self.cameras[key]
        for key, setup in wanted.items():
            if key in self.cameras:
                self.cameras[key].setup = setup  # names may have changed; the camera stays open
            else:
                assert setup.mode is not None
                config = RecorderConfig(self.backend, setup.camera.id, setup.camera.name, setup.mode)
                self.cameras[key] = CameraState(setup, RecorderProcess(config))

    # ── recording ────────────────────────────────────────────────────────────

    def start(
        self, session: str, folder: Path, *, wait_for_consumers: bool = True, frame_numbers: bool = False
    ) -> None:
        """Opens every camera's LSL stream; recording starts once LabRecorder is connected to all
        of them (or at once, without ``wait_for_consumers``)."""
        if self.phase is not Phase.PREVIEW:
            raise RuntimeError("already recording")
        not_ready = [s.setup.camera.name for s in self.cameras.values() if not s.ready]
        if not self.cameras or not_ready:
            raise RuntimeError(f"cameras not open: {', '.join(not_ready) or 'none selected'}")
        folder.mkdir(parents=True, exist_ok=True)
        self._session, self._folder, self._frame_numbers = session, folder, frame_numbers
        self.wait_for_consumers = wait_for_consumers
        self.results = {}
        self.problems = {}
        for state in self.cameras.values():
            state.result = None
            _name, stream, file = state.setup.names(session)
            state.process.send(OpenStream(stream, file))
        self.phase = Phase.WAITING
        if not wait_for_consumers:
            self._record()

    def cancel(self) -> None:
        """Stops waiting for LabRecorder and closes the streams."""
        if self.phase is Phase.WAITING:
            for state in self.cameras.values():
                state.process.send(CloseStream())
            self.phase = Phase.PREVIEW

    def stop(self) -> None:
        """Stops recording; the files are complete when the phase returns to PREVIEW."""
        if self.phase is Phase.WAITING:
            self.cancel()
        elif self.phase is Phase.RECORDING:
            for state in self.cameras.values():
                state.process.send(CloseStream())
            self.phase = Phase.FINISHING

    def _record(self) -> None:
        for state in self.cameras.values():
            _name, _stream, file = state.setup.names(self._session)
            state.process.send(StartRecording(str(self._folder / file), self._frame_numbers))
        self.started_at = time.monotonic()
        self.phase = Phase.RECORDING

    # ── progress ─────────────────────────────────────────────────────────────

    def poll(self) -> None:
        """Takes in the recorder processes' reports and moves the session on: starts recording
        once LabRecorder is connected, and returns to preview once every file is complete."""
        for key, state in self.cameras.items():
            for report in state.process.reports():
                match report:
                    case Opened(mode):
                        state.mode = mode
                    case Status(stats):
                        state.stats = stats
                    case Recorded(result):
                        state.result = result
                        self.results[key] = result
                    case Failed(message, fatal):
                        state.error = message
                        state.ended = state.ended or fatal
                        self.problems[key] = message
                    case Ended():
                        state.ended = True
        if self.phase is Phase.WAITING and all(
            s.stats is not None and s.stats.consumers for s in self.cameras.values()
        ):
            self._record()
        elif self.phase is Phase.FINISHING and all(
            s.result is not None or s.ended for s in self.cameras.values()
        ):
            self.phase = Phase.PREVIEW
            self.started_at = None

    @property
    def elapsed(self) -> float:
        """Seconds since recording started (0 when not recording)."""
        return time.monotonic() - self.started_at if self.started_at is not None else 0.0

    def shutdown(self) -> None:
        """Ends every recorder process, completing any file being recorded."""
        for state in self.cameras.values():  # all at once: each completes its own file
            state.process.send(Shutdown())
        for key, state in self.cameras.items():
            for report in state.process.shutdown():
                if isinstance(report, Recorded):
                    self.results[key] = report.result
                elif isinstance(report, Failed):
                    self.problems[key] = report.message
        self.cameras.clear()
        self.phase = Phase.PREVIEW
