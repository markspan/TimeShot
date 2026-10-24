"""TimeShot's desktop app (PySide6)."""

import sys


def _load_windows_icu() -> None:
    """Loads the ICU library that comes with Windows, before Qt loads one.

    Qt for Windows uses Windows' own ICU (``icuuc.dll`` in System32), whose functions have plain
    names. Some Python distributions, notably Anaconda, put an ``icuuc.dll`` of their own on the
    DLL search path ahead of System32, with version-suffixed names (``ucnv_open_78``); Qt would
    get that one and fail to load: "DLL load failed while importing QtCore: The specified
    procedure could not be found". Windows gives a DLL that is already loaded to everything that
    asks for it by name, so loading Windows' own first settles which ICU Qt gets.
    """
    if sys.platform != "win32":
        return
    import contextlib  # noqa: PLC0415  (Windows only)
    import ctypes  # noqa: PLC0415
    import os  # noqa: PLC0415

    windows_icu = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "icuuc.dll")
    with contextlib.suppress(OSError):  # not there: Qt then finds no ICU of Windows' either
        ctypes.WinDLL(windows_icu)


_load_windows_icu()
