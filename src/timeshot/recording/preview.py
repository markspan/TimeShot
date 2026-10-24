"""Hands a recorder process's newest preview image to the GUI through shared memory.

One slot per camera holds the newest image only: the recorder overwrites it at the preview
rate and the GUI copies it whenever it repaints, so neither ever waits for the other beyond a
copy, and no queue of images can build up.

The slot's memory is a header (a sequence number, the width and the height) followed by the
image's RGB pixels, guarded by a lock that both processes share.
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

_HEADER = struct.Struct("<QII")  # sequence number (0: no image yet), width, height
_SIZE = _HEADER.size + MAX_WIDTH * MAX_HEIGHT * 3


def preview_size(width: int, height: int) -> tuple[int, int]:
    """The size of a camera's preview image.

    Args:
        width: The camera's frame width.
        height: The camera's frame height.

    Returns:
        (width, height), fitted into the preview box with the aspect ratio kept, never
        enlarged, and even (as the YUV formats the pictures come in need).
    """
    scale = min(MAX_WIDTH / width, MAX_HEIGHT / height, 1.0)
    return max(2, round(width * scale) // 2 * 2), max(2, round(height * scale) // 2 * 2)


class PreviewSlot:
    """The shared memory holding one camera's newest preview image."""

    def __init__(self, memory: shared_memory.SharedMemory, lock: Lock, *, owner: bool) -> None:
        """Wraps shared memory; use :meth:`create` or :meth:`attach` instead.

        Args:
            memory: The slot's memory.
            lock: The lock both processes take to read or write it.
            owner: Whether this side created the memory, and so frees it on :meth:`close`.
        """
        if memory.buf is None:
            raise ValueError("We are sorry, but the shared memory came without a buffer.")
        self._memory = memory
        self._buffer: memoryview = memory.buf
        self.lock = lock
        self._owner = owner
        self._last_read = 0  # the sequence number of the image read() returned last

    @classmethod
    def create(cls, context: BaseContext) -> PreviewSlot:
        """A new, empty slot, created on the GUI side.

        Args:
            context: The multiprocessing context the recorder process is started with.

        Returns:
            The slot. Pass its :attr:`name` and :attr:`lock` to the recorder process, which
            attaches to it with :meth:`attach`.
        """
        slot = cls(shared_memory.SharedMemory(create=True, size=_SIZE), context.Lock(), owner=True)
        _HEADER.pack_into(slot._buffer, 0, 0, 0, 0)
        return slot

    @classmethod
    def attach(cls, name: str, lock: Lock) -> PreviewSlot:
        """The slot the GUI created, attached to on the recorder side.

        Args:
            name: The slot's :attr:`name`.
            lock: The slot's :attr:`lock`.

        Returns:
            The slot.
        """
        return cls(shared_memory.SharedMemory(name=name), lock, owner=False)

    @property
    def name(self) -> str:
        """The name the operating system knows the shared memory by."""
        return self._memory.name

    def write(self, rgb: npt.NDArray[np.uint8]) -> None:
        """Publishes a new image, replacing the previous one.

        Args:
            rgb: The image, as an RGB array no larger than the preview box.

        Raises:
            ValueError: The image is larger than the preview box.
        """
        height, width = rgb.shape[:2]
        if width > MAX_WIDTH or height > MAX_HEIGHT:
            raise ValueError(
                f"We are terribly sorry, but the preview image of {width}x{height} exceeds the"
                f" {MAX_WIDTH}x{MAX_HEIGHT} preview box."
            )
        with self.lock:
            sequence = _HEADER.unpack_from(self._buffer, 0)[0] + 1
            pixels = np.ndarray((height, width, 3), np.uint8, self._buffer, _HEADER.size)
            pixels[...] = rgb
            _HEADER.pack_into(self._buffer, 0, sequence, width, height)

    def read(self) -> npt.NDArray[np.uint8] | None:
        """The newest image, if it is new.

        Returns:
            A copy of the newest image, or None if there is none yet or it was read before.
        """
        with self.lock:
            sequence, width, height = _HEADER.unpack_from(self._buffer, 0)
            if sequence == self._last_read or not width:
                return None
            self._last_read = sequence
            pixels = np.ndarray((height, width, 3), np.uint8, self._buffer, _HEADER.size)
            return pixels.copy()

    def close(self) -> None:
        """Detaches from the slot; the side that created it also frees it."""
        self._memory.close()
        if self._owner:
            self._memory.unlink()
