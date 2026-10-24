"""TimeShot's remembered settings: a JSON file in the user's configuration folder.

Cameras are remembered by their key (their name, numbered for identical cameras), so their
settings come back when they are plugged in again, whatever port or order they end up in.
That includes their number (``{i}`` in the name templates), so their names stay the same too.
A camera seen for the first time starts with the name templates under "templates".

The file is meant to be readable and editable by hand while TimeShot is closed. Entries
TimeShot does not know, and values of the wrong type, are ignored on loading.
"""

from __future__ import annotations

import itertools
import json
import logging
import typing
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from timeshot.cameras.base import CameraInfo, CameraMode
from timeshot.setup import DEFAULT_TEMPLATES, CameraSetup, NameTemplates

log = logging.getLogger(__name__)


@dataclass
class CameraSettings:
    """One camera's remembered settings, as the settings file holds them.

    Attributes:
        number: The camera's number (``{i}``); None if it has none yet.
        enabled: Whether to record it.
        mode: Its mode, as :meth:`CameraMode.key` writes it; None for its preferred mode.
        name: Its camera name template.
        stream: Its LSL stream name template.
        file: Its video file name template.
    """

    number: int | None = None
    enabled: bool = True
    mode: str | None = None
    name: str = DEFAULT_TEMPLATES.name
    stream: str = DEFAULT_TEMPLATES.stream
    file: str = DEFAULT_TEMPLATES.file

    @classmethod
    def starting_with(cls, templates: NameTemplates) -> CameraSettings:
        """The settings of a camera seen for the first time.

        Args:
            templates: The name templates new cameras start with.

        Returns:
            Default settings, with these templates and no number yet.
        """
        return cls(name=templates.name, stream=templates.stream, file=templates.file)

    @classmethod
    def of(cls, setup: CameraSetup) -> CameraSettings:
        """The settings to remember of a camera.

        Args:
            setup: The camera's current settings.

        Returns:
            Them, as the settings file holds them.
        """
        templates = setup.templates
        mode = setup.mode.key() if setup.mode else None
        return cls(setup.number, setup.enabled, mode, templates.name, templates.stream, templates.file)

    @property
    def templates(self) -> NameTemplates:
        """The camera's name templates."""
        return NameTemplates(self.name, self.stream, self.file)

    def setup(self, camera: CameraInfo, number: int) -> CameraSetup:
        """The camera's settings, ready to use.

        Args:
            camera: The camera, as found now.
            number: Its number (the remembered one, or a new one if it has none).

        Returns:
            The settings; the remembered mode only if the camera still offers it.
        """
        return CameraSetup(camera, number, self.enabled, _offered_mode(self.mode, camera), self.templates)


@dataclass
class Settings:
    """Everything TimeShot remembers.

    Attributes:
        session: The session, for ``{s}`` in the names.
        folder: The folder for the video files; empty for the GUI's suggestion (the user's
            video folder).
        wait_for_labrecorder: Whether recording waits until LabRecorder records every stream.
        frame_numbers: Whether to burn each frame's number into its picture.
        templates: The name templates a camera seen for the first time starts with.
        cameras: Each camera's settings, by camera key, including those of cameras not
            plugged in now.
    """

    session: str = "1"
    folder: str = ""
    wait_for_labrecorder: bool = True
    frame_numbers: bool = False
    templates: NameTemplates = DEFAULT_TEMPLATES
    cameras: dict[str, CameraSettings] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Settings:
        """Reads the settings file.

        Args:
            path: The file.

        Returns:
            The settings in it. Defaults if there is no file or it cannot be read, and for
            each entry that is missing, unknown, or of the wrong type.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as error:
            log.warning(
                "We are terribly sorry, but the settings in %s could not be read (%s), so we have taken the"
                " liberty of starting with the defaults.",
                path,
                error,
            )
            return cls()
        if not isinstance(data, dict):
            log.warning(
                "We are sorry, but the settings in %s are not a JSON object, so we have taken the liberty of"
                " starting with the defaults.",
                path,
            )
            return cls()

        settings = cls(**_valid_fields(cls, data, skip={"templates", "cameras"}))
        if isinstance(templates := data.get("templates"), dict):
            settings.templates = NameTemplates(**_valid_fields(NameTemplates, templates))
        if isinstance(cameras := data.get("cameras"), dict):
            settings.cameras = {
                key: CameraSettings(**_valid_fields(CameraSettings, values))
                for key, values in cameras.items()
                if isinstance(values, dict)
            }
        return settings

    def save(self, path: Path) -> None:
        """Writes the settings file, replacing it in one step, so that it is never left half written.

        Args:
            path: The file; its folder is created if it does not exist.

        Raises:
            OSError: The file could not be written.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)

    def setups(self, cameras: list[CameraInfo]) -> list[CameraSetup]:
        """The cameras present now, with their remembered settings.

        A camera keeps its number. A new one gets the lowest number no remembered camera has,
        not even an unplugged one, so that its names cannot clash with that camera's once it
        is back.

        Args:
            cameras: The cameras present now.

        Returns:
            Their settings, in the same order.
        """
        taken = {camera.number for camera in self.cameras.values() if camera.number is not None}
        setups = []
        for camera in cameras:
            remembered = self.cameras.get(camera.key) or CameraSettings.starting_with(self.templates)
            number = remembered.number
            if number is None:
                number = next(n for n in itertools.count() if n not in taken)
                taken.add(number)
            setups.append(remembered.setup(camera, number))
        return setups

    def remember(self, setups: list[CameraSetup]) -> None:
        """Takes in the cameras' current settings (save() writes them to the file).

        Args:
            setups: The settings of the cameras present now; others stay remembered as they were.
        """
        for setup in setups:
            self.cameras[setup.camera.key] = CameraSettings.of(setup)


def _valid_fields(cls: type, values: dict[str, Any], skip: Collection[str] = ()) -> dict[str, Any]:
    """The entries of a JSON object that make good fields of a dataclass.

    Args:
        cls: The dataclass.
        values: The JSON object's entries.
        skip: Fields to leave out (those holding other dataclasses, which the caller reads).

    Returns:
        The entries that ``cls`` has a field for, holding a value of that field's type. Others
        are left out, so that the field's default applies: entries of a newer TimeShot, and hand
        edits that went wrong (such as a number in quotes).
    """
    types = typing.get_type_hints(cls)
    return {
        name: value
        for name, value in values.items()
        if name in types and name not in skip and isinstance(value, types[name])
    }


def _offered_mode(key: str | None, camera: CameraInfo) -> CameraMode | None:
    """A remembered mode, if the camera still offers it.

    Args:
        key: The mode, as :meth:`CameraMode.key` wrote it; None if none was remembered.
        camera: The camera, as found now.

    Returns:
        The mode; None if none was remembered, it cannot be read, or the camera no longer
        offers it (its preferred mode is then used).
    """
    if key is None:
        return None
    try:
        mode = CameraMode.parse(key)
    except ValueError:
        return None
    return mode if mode in camera.modes else None
