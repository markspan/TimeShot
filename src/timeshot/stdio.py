"""Keeps the C runtime's standard streams usable by native libraries (Windows).

TimeShot.exe is compiled by Nuitka with ``--windows-console-mode=attach``: it is a GUI program
that, when started from a console, attaches to that console. A process that starts without
standard handles but finds a console to attach to (a recorder process started from a console
session, or TimeShot.exe itself run in a terminal) gets its C runtime streams stdin, stdout and
stderr wired to that console by Nuitka, in UTF-8 text mode (``_O_U8TEXT``).

In that mode the Microsoft C runtime accepts only wide-character I/O on those streams. The
first narrow write by native code, such as a liblsl log line (``fprintf(stderr, ...)``), fails
the runtime's parameter check, and the runtime ends the process on the spot with exit code
0xC0000409. :func:`reset_c_stream_modes` puts the streams back in the runtime's default text
mode, which accepts narrow and wide I/O alike.

Python's own ``sys.stdout`` and ``sys.stderr`` do not write through these C streams, so they
are unaffected either way.
"""

from __future__ import annotations

import contextlib
import os
import sys

# The C runtime's stdin, stdout and stderr, as numbered by its __acrt_iob_func().
_C_STREAMS = (0, 1, 2)


def reset_c_stream_modes() -> None:
    """Puts the C runtime's stdin, stdout and stderr back in its default text mode.

    Call it first thing in a process, before native code writes to these streams. It does
    nothing outside Windows, and nothing to a stream without a file (a process without a
    console has none).
    """
    if sys.platform != "win32":
        return
    import ctypes  # noqa: PLC0415  (Windows only)
    import msvcrt  # noqa: PLC0415

    # The Universal C runtime, which Python and liblsl share: one process-wide table of file
    # descriptors and streams, so msvcrt.setmode() changes the modes liblsl writes with.
    ucrt = ctypes.CDLL("ucrtbase")
    stream = ucrt.__acrt_iob_func
    stream.restype = ctypes.c_void_p
    stream.argtypes = (ctypes.c_uint,)
    file_number = ucrt._fileno
    file_number.restype = ctypes.c_int
    file_number.argtypes = (ctypes.c_void_p,)
    for number in _C_STREAMS:
        fd = file_number(stream(number))
        if fd >= 0:  # -2: the stream has no file
            with contextlib.suppress(OSError):
                msvcrt.setmode(fd, os.O_TEXT)
