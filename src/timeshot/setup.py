"""What to record of each camera, and what is wrong with it before recording starts.

Independent of how recordings run, so that the GUI, the command line and the settings file
share it:

* :class:`NameTemplates` holds a camera's name, LSL stream name and video file name as
  templates, in which ``{i}`` is the camera's number and ``{s}`` the session, as in TimeShot 1.x.
* :class:`CameraSetup` holds one camera's settings: whether to record it, its number, its mode
  and its name templates.
* :func:`check` finds what would go wrong with a recording before it starts.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from timeshot.cameras.base import CameraInfo, CameraMode, preferred_mode
from timeshot.recording.writer import FILE_TYPES

# Characters that Windows, macOS or Linux do not allow in a file name (or that would make it a path).
_UNSAFE_IN_FILE_NAMES = frozenset('<>:"/\\|?*')


def expand(template: str, number: int, session: str) -> str:
    """Fills in a name template.

    Args:
        template: The template, e.g. "Video_{i}_s{s}.avi".
        number: The camera's number, for ``{i}``.
        session: The session, for ``{s}``.

    Returns:
        The name, without surrounding spaces, e.g. "Video_2_s07.avi".
    """
    return template.replace("{i}", str(number)).replace("{s}", session).strip()


def with_extension(template: str, extension: str) -> str:
    """A file name template with another video file extension.

    Args:
        template: The template, e.g. "Video_{i}.avi".
        extension: The new extension, e.g. ".mp4".

    Returns:
        The template with ``extension`` in place of the video extension it has, or added to it
        if it has none (another extension, as in "lab.v2", is kept): "Video_{i}.mp4".
    """
    suffix = Path(template).suffix
    stem = template[: -len(suffix)] if suffix.lower() in FILE_TYPES else template
    return stem + extension


class Names(NamedTuple):
    """A camera's names for one session: its :class:`NameTemplates`, filled in."""

    name: str  # the camera's name, stored in its LSL stream's description
    stream: str  # its LSL marker stream's name
    file: str  # its video file's name


@dataclass(frozen=True, slots=True)
class NameTemplates:
    """A camera's names, as templates in which ``{i}`` is its number and ``{s}`` the session.

    The defaults are TimeShot 1.x's.

    Attributes:
        name: The camera's name, stored in its LSL stream's description.
        stream: The name of its LSL marker stream, as LabRecorder lists it.
        file: The name of its video file; the extension chooses the format.
    """

    name: str = "Camera {i}"
    stream: str = "Stream_{i}"
    file: str = "Video_{i}.avi"

    def expand(self, number: int, session: str) -> Names:
        """The names, filled in for one camera and session (see :func:`expand`).

        Args:
            number: The camera's number, for ``{i}``.
            session: The session, for ``{s}``.

        Returns:
            The camera, stream and file names.
        """
        return Names(*(expand(template, number, session) for template in (self.name, self.stream, self.file)))

    def with_extension(self, extension: str) -> NameTemplates:
        """The same templates, with another video file extension (see :func:`with_extension`)."""
        return dataclasses.replace(self, file=with_extension(self.file, extension))


DEFAULT_TEMPLATES = NameTemplates()


@dataclass
class CameraSetup:
    """One camera's settings.

    Attributes:
        camera: The camera.
        number: Its number: ``{i}`` in its names.
        enabled: Whether to record it.
        mode: The mode to record in; None picks the camera's preferred mode (and stays None
            only for a camera without modes).
        templates: Its names, as templates.
    """

    camera: CameraInfo
    number: int
    enabled: bool = True
    mode: CameraMode | None = None
    templates: NameTemplates = DEFAULT_TEMPLATES

    def __post_init__(self) -> None:
        """Picks the camera's preferred mode if none was given."""
        if self.mode is None:
            self.mode = preferred_mode(self.camera.modes)

    def names(self, session: str) -> Names:
        """The camera's names in a session.

        Args:
            session: The session, for ``{s}``.

        Returns:
            The camera, stream and file names.
        """
        return self.templates.expand(self.number, session)


@dataclass(frozen=True, slots=True)
class Problem:
    """Something that stops a recording, or needs confirming before it.

    Attributes:
        message: What is wrong, for people.
        camera: The key of the camera it concerns; None if it concerns all of them.
        confirm: Whether the recording may go ahead once confirmed (overwriting a file).
    """

    message: str
    camera: str | None = None
    confirm: bool = False


def check(setups: Iterable[CameraSetup], session: str, folder: Path) -> list[Problem]:
    """What is wrong with recording these cameras.

    Args:
        setups: The cameras' settings; only those of the enabled cameras count.
        session: The session, for ``{s}`` in the names.
        folder: The folder the video files go to.

    Returns:
        The problems: no camera enabled, blank or clashing names, unusable file names, and
        files that would be overwritten (the last only need confirming). Empty if there are none.
    """
    enabled = [setup for setup in setups if setup.enabled]
    if not enabled:
        return [Problem("Select at least one camera.")]
    problems = [problem for setup in enabled for problem in _setup_problems(setup, session, folder)]
    return problems + _clashes(enabled, session)


def _setup_problems(setup: CameraSetup, session: str, folder: Path) -> list[Problem]:
    """What is wrong with one camera's settings on their own.

    Args:
        setup: The camera's settings.
        session: The session, for ``{s}`` in the names.
        folder: The folder the video files go to.

    Returns:
        The problems: no mode, blank names, an unusable file name or one that exists.
    """
    key = setup.camera.key
    names = setup.names(session)
    problems = []
    if setup.mode is None:
        problems.append(Problem("This camera offers no mode TimeShot can record.", key))
    if not names.name:
        problems.append(Problem("The camera name is empty.", key))
    if not names.stream:
        problems.append(Problem("The stream name is empty.", key))
    if (file_problem := _file_problem(names.file)) is not None:
        problems.append(Problem(file_problem, key))
    elif (folder / names.file).exists():
        problems.append(Problem(f"{names.file} exists and will be overwritten.", key, confirm=True))
    return problems


def _clashes(setups: list[CameraSetup], session: str) -> list[Problem]:
    """Cameras that would share an LSL stream name or a video file.

    Args:
        setups: The enabled cameras' settings.
        session: The session, for ``{s}`` in the names.

    Returns:
        A problem for each camera of each clash. File names that differ in case only clash
        too: they name the same file on Windows and macOS.
    """
    cameras_by_stream: defaultdict[str, list[str]] = defaultdict(list)
    cameras_by_file: defaultdict[str, list[str]] = defaultdict(list)
    for setup in setups:
        names = setup.names(session)
        if names.stream:
            cameras_by_stream[names.stream].append(setup.camera.key)
        if _file_problem(names.file) is None:
            cameras_by_file[names.file.lower()].append(setup.camera.key)
    return [
        Problem(f"Another camera has the same {kind} ({value}).", key)
        for kind, cameras_by_name in (("stream name", cameras_by_stream), ("file name", cameras_by_file))
        for value, keys in cameras_by_name.items()
        if len(keys) > 1
        for key in keys
    ]


def _file_problem(file: str) -> str | None:
    """Why a name cannot be used as a video file name.

    Args:
        file: The file name, filled in.

    Returns:
        What is wrong with it, or None if it can be used.
    """
    if not file:
        return "The file name is empty."
    if set(file) & _UNSAFE_IN_FILE_NAMES:
        return f"The file name {file!r} contains a character not allowed in file names."
    if Path(file).suffix.lower() not in FILE_TYPES:
        *others, last = FILE_TYPES
        return f"The file name {file!r} must end in {', '.join(others)} or {last}."
    return None
