"""TimeShot's desktop app (PySide6)."""

import sys

# The Qt libraries behind the PySide6 modules TimeShot imports: QtCore needs Qt6Core.dll, and
# so on. Listed in dependency order; a test checks that every imported module is listed.
QT_LIBRARIES = ("Core", "Gui", "Widgets", "Svg")


def _load_qt_dlls() -> None:
    """Loads the DLLs Qt needs from where Qt expects them, before Windows finds other copies.

    Anaconda's Python searches its own folder of DLLs (Library\\bin) before PySide6's folder and
    before System32, and that folder can hold other builds of DLLs that Qt needs: an older Qt
    (Qt6Core.dll and others, from conda's Qt package) and an ICU (icuuc.dll) whose functions are
    named differently from those of the ICU that comes with Windows, which Qt uses. Qt would get
    those and fail to load: "DLL load failed while importing QtCore: The specified procedure
    could not be found". Windows gives a DLL that is already loaded to everything that asks for
    it by name, so loading the right copies first settles which ones Qt gets: Windows' own ICU,
    then PySide6's own Qt libraries.
    """
    if sys.platform != "win32":
        return
    import contextlib  # noqa: PLC0415  (Windows only)
    import ctypes  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    libraries = [Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "icuuc.dll"]
    spec = importlib.util.find_spec("PySide6")  # finds the package without importing it
    if spec is not None and spec.submodule_search_locations:
        pyside = Path(next(iter(spec.submodule_search_locations)))
        libraries += [pyside / f"Qt6{name}.dll" for name in QT_LIBRARIES]
    for library in libraries:
        with contextlib.suppress(OSError):  # not there (e.g. compiled TimeShot): Qt finds its own
            ctypes.WinDLL(str(library))


_load_qt_dlls()
