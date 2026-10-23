"""Hands a recorder process's latest preview image to the GUI through shared memory.

One slot per camera holds the newest image only: the recorder overwrites it at the preview
rate and the GUI copies it whenever it repaints, so neither ever waits for the other beyond
a copy, and no image queue can build up.
"""

from __future__ import annotations

import struct
from multiprocessing import shared_memory
from multiprocessing.context import BaseContext
from multiprocessing.synchronize import Lock

import numpy as np
import numpy.typing as npt

# Preview images fit in this box (aspect ratio kept): plenty for a card in the GUI.
MAX_WIDTH, MAX_HEIGHT = 640, 360

_HEADER = struct.Struct("<QII")  # sequence number, width, height
_SIZE = _HEADER.size + MAX_WIDTH * MAX_HEIGHT * 3


def preview_size(width: int, height: int) -> tuple[int, int]:
    """The size of a camera's preview image: fitted into the preview box, even dimensions."""
    scale = min(MAX_WIDTH / width, MAX_HEIGHT / height, 1.0)
    return max(2, round(width * scale) // 2 * 2), max(2, round(height * scale) // 2 * 2)


class PreviewSlot:
    """The shared memory of one camera's preview, guarded by a lock both processes share."""

    def __init__(self, memory: shared_memory.SharedMemory, lock: Lock, *, owner: bool) -> None:
        if memory.buf is None:
            raise ValueError("shared memory without a buffer")
        self._memory = memory
        self._buffer: memoryview = memory.buf
        self.lock = lock
        self._owner = owner
        self._last_read = 0

    @classmethod
    def create(cls, context: BaseContext) -> PreviewSlot:
        """A new, empty slot (GUI side). Pass :attr:`name` and :attr:`lock` to the recorder process."""
        slot = cls(shared_memory.SharedMemory(create=True, size=_SIZE), context.Lock(), owner=True)
        _HEADER.pack_into(slot._buffer, 0, 0, 0, 0)
        return slot

    @classmethod
    def attach(cls, name: str, lock: Lock) -> PreviewSlot:
        """The slot created by the GUI (recorder side)."""
        return cls(shared_memory.SharedMemory(name=name), lock, owner=False)

    @property
    def name(self) -> str:
        return self._memory.name

    def write(self, rgb: npt.NDArray[np.uint8]) -> None:
        """Publishes an RGB image no larger than the preview box."""
        height, width = rgb.shape[:2]
        if width > MAX_WIDTH or height > MAX_HEIGHT:
            raise ValueError(f"preview image {width}x{height} exceeds {MAX_WIDTH}x{MAX_HEIGHT}")
        with self.lock:
            sequence = _HEADER.unpack_from(self._buffer, 0)[0] + 1
            pixels = np.ndarray((height, width, 3), np.uint8, self._buffer, _HEADER.size)
            pixels[...] = rgb
            _HEADER.pack_into(self._buffer, 0, sequence, width, height)

    def read(self) -> npt.NDArray[np.uint8] | None:
        """A copy of the newest image, or None if there is none newer than the last one read."""
        with self.lock:
            sequence, width, height = _HEADER.unpack_from(self._buffer, 0)
            if sequence == self._last_read or not width:
                return None
            self._last_read = sequence
            pixels = np.ndarray((height, width, 3), np.uint8, self._buffer, _HEADER.size)
            return pixels.copy()

    def close(self) -> None:
        """Detaches from the slot; the creating side also frees it."""
        self._memory.close()
        if self._owner:
            self._memory.unlink()
