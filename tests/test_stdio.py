"""The C runtime's standard streams stay usable by native code (the Microsoft C runtime only)."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Microsoft C runtime behaviour")

# A process whose C stderr is in UTF-8 text mode, as Nuitka's console code leaves it, then
# written to the way liblsl logs: narrow text, through the C runtime's stderr stream.
NARROW_WRITE = textwrap.dedent(
    """
    import ctypes, msvcrt, sys
    from timeshot.stdio import reset_c_stream_modes

    ucrt = ctypes.CDLL("ucrtbase")
    ucrt.__acrt_iob_func.restype = ctypes.c_void_p
    ucrt.__acrt_iob_func.argtypes = (ctypes.c_uint,)
    ucrt._fileno.argtypes = (ctypes.c_void_p,)
    ucrt.fputs.argtypes = (ctypes.c_char_p, ctypes.c_void_p)
    ucrt.fflush.argtypes = (ctypes.c_void_p,)
    stderr = ucrt.__acrt_iob_func(2)
    msvcrt.setmode(ucrt._fileno(stderr), 0x40000)  # _O_U8TEXT
    if sys.argv[1] == "reset":
        reset_c_stream_modes()
    ucrt.fputs(b"narrow text\\n", stderr)
    ucrt.fflush(stderr)
    """
)

# The exit code with which the C runtime ends a process that passed it an invalid parameter.
STATUS_STACK_BUFFER_OVERRUN = 0xC0000409


def write_narrow_text(reset: bool) -> subprocess.CompletedProcess[bytes]:
    argument = "reset" if reset else "keep"
    command = [sys.executable, "-c", NARROW_WRITE, argument]
    return subprocess.run(command, capture_output=True, timeout=60, check=False)


def test_narrow_text_to_a_utf8_mode_stream_ends_the_process() -> None:
    """What reset_c_stream_modes() prevents."""
    assert write_narrow_text(reset=False).returncode == STATUS_STACK_BUFFER_OVERRUN


def test_after_the_reset_narrow_text_is_written() -> None:
    result = write_narrow_text(reset=True)
    assert result.returncode == 0, result.stderr
    assert b"narrow text" in result.stderr
