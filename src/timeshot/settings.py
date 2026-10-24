"""TimeShot's remembered settings, as a JSON file in the user's configuration folder.

Cameras are remembered by their key (their name, numbered for identical cameras), so their
settings come back when they are plugged in again, whatever port or order they end up in;
that includes their number ({i} in the name templates), so their names stay the same too.
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from timeshot.cameras.base import CameraInfo, CameraMode
from timeshot.session import DEFAULT_FILE, DEFAULT_NAME, DEFAULT_STREAM, CameraSetup

log = logging.getLogger(__name__)


@dataclass
class CameraSettings:
    number: int | None = None  # {i}; None: not given yet
    enabled: bool = True
    mode: str | None = None  # CameraMode.key()
    name: str = DEFAULT_NAME
    stream: str = DEFAULT_STREAM
    file: str = DEFAULT_FILE


@dataclass
class Settings:
    session: str = "1"
    folder: str = ""  # empty: the GUI suggests the user's video folder
    wait_for_labrecorder: bool = True
    frame_numbers: bool = False
    cameras: dict[str, CameraSettings] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Settings:
        """The settings saved at ``path``; defaults if there are none or they cannot be read."""
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as error:
            log.warning("Ignoring unreadable settings %s: %s", path, error)
            return cls()
        known = {f.name for f in fields(cls)} - {"cameras"}
        settings = cls(**{k: v for k, v in data.items() if k in known})
        camera_fields = {f.name for f in fields(CameraSettings)}
        for key, values in (data.get("cameras") or {}).items():
            if isinstance(values, dict):
                settings.cameras[key] = CameraSettings(
                    **{k: v for k, v in values.items() if k in camera_fields}
                )
        return settings

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)  # never leaves a half-written file

    def setups(self, cameras: list[CameraInfo]) -> list[CameraSetup]:
        """The present cameras with their remembered settings (defaults for new ones). A camera
        keeps its number; a new one gets the lowest number that no remembered camera has, even
        an unplugged one, so that its names cannot clash with that camera's when it is back."""
        taken = {s.number for s in self.cameras.values() if s.number is not None}
        setups = []
        for camera in cameras:
            remembered = self.cameras.get(camera.key, CameraSettings())
            number = remembered.number
            if number is None:
                number = next(n for n in itertools.count() if n not in taken)
                taken.add(number)
            setups.append(
                CameraSetup(
                    camera,
                    number,
                    remembered.enabled,
                    _mode(remembered.mode, camera),
                    remembered.name,
                    remembered.stream,
                    remembered.file,
                )
            )
        return setups

    def remember(self, setups: list[CameraSetup]) -> None:
        for setup in setups:
            self.cameras[setup.camera.key] = CameraSettings(
                setup.number,
                setup.enabled,
                setup.mode.key() if setup.mode else None,
                setup.name,
                setup.stream,
                setup.file,
            )


def _mode(key: str | None, camera: CameraInfo) -> CameraMode | None:
    """The remembered mode if the camera still offers it, else None (the setup then picks one)."""
    if key is None:
        return None
    try:
        mode = CameraMode.parse(key)
    except ValueError:
        return None
    return mode if mode in camera.modes else None
