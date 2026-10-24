"""Finds the DLL that keeps Qt from loading on Windows.

When importing PySide6 fails with "DLL load failed while importing QtCore: The specified
procedure could not be found", Windows found a DLL that Qt needs, but a copy of it that lacks
functions Qt asks for. Run this with the Python that fails, from the repository folder:

    python tools\\check_qt_dlls.py

It reads the import tables of PySide6's QtCore module and of the DLLs it needs (without loading
them), and for every DLL name lists each copy in the folders Windows searches, in about the order
it searches them, with the functions that copy lacks. A copy that lacks functions and comes
before a complete one is the culprit. Then it tries the import for real and shows which copies
ended up loaded.
"""

from __future__ import annotations

import importlib.util
import os
import struct
import sys
from functools import cache
from pathlib import Path

# ── reading DLLs (Windows PE files) ──────────────────────────────────────────


class PeFile:
    """The import and export tables of a Windows DLL or executable."""

    def __init__(self, path: Path) -> None:
        self.data = data = path.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe : pe + 4] != b"PE\0\0":
            raise ValueError(f"{path} is not a PE file")
        sections, optional_size = struct.unpack_from("<H12xH", data, pe + 6)
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        self.wide = magic == 0x20B  # PE32+: 64-bit
        directories = optional + (112 if self.wide else 96)
        self.directories = [struct.unpack_from("<II", data, directories + 8 * i) for i in range(2)]
        self.sections: list[tuple[int, int, int, int]] = [
            struct.unpack_from("<8xIIII", data, optional + optional_size + 40 * i) for i in range(sections)
        ]

    def _offset(self, rva: int) -> int:
        for virtual_size, address, raw_size, raw_pointer in self.sections:
            if address <= rva < address + max(virtual_size, raw_size):
                return rva - address + raw_pointer
        raise ValueError(f"address {rva:#x} is in no section")

    def _string(self, rva: int) -> str:
        start = self._offset(rva)
        return self.data[start : self.data.index(b"\0", start)].decode("ascii", "replace")

    def exports(self) -> set[str]:
        rva, size = self.directories[0]
        if not size:
            return set()
        start = self._offset(rva)
        count, names = struct.unpack_from("<I4xI", self.data, start + 24)
        first = self._offset(names)
        return {self._string(struct.unpack_from("<I", self.data, first + 4 * i)[0]) for i in range(count)}

    def imports(self) -> dict[str, set[str]]:
        """The functions imported by name, per DLL (names in lower case)."""
        rva, size = self.directories[1]
        result: dict[str, set[str]] = {}
        if not size:
            return result
        entry = self._offset(rva)
        thunk_size, ordinal_flag = (8, 1 << 63) if self.wide else (4, 1 << 31)
        while True:
            lookup, _time, _chain, name, address = struct.unpack_from("<IIIII", self.data, entry)
            if not name:
                return result
            functions = result.setdefault(self._string(name).lower(), set())
            thunk = self._offset(lookup or address)
            while value := int.from_bytes(self.data[thunk : thunk + thunk_size], "little"):
                if not value & ordinal_flag:
                    functions.add(self._string(value + 2))  # skip the hint
                thunk += thunk_size
            entry += 20


@cache
def read(path: Path) -> PeFile | None:
    try:
        return PeFile(path)
    except (OSError, ValueError, struct.error):
        return None


# ── where Windows looks ──────────────────────────────────────────────────────


def search_folders(pyside: Path) -> list[tuple[str, Path]]:
    """The folders Windows searches for the DLLs of an extension module, roughly in order (the
    order among folders added with os.add_dll_directory is not defined), plus PATH."""
    prefix = Path(sys.prefix)
    windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    folders = [
        ("PySide6 (the module's own folder)", pyside),
        ("next to python.exe", Path(sys.executable).parent),
        ("shiboken6 (added by PySide6)", pyside.parent / "shiboken6"),
        ("Anaconda/conda Library\\bin", prefix / "Library" / "bin"),
        ("Anaconda/conda Library\\mingw-w64\\bin", prefix / "Library" / "mingw-w64" / "bin"),
        ("Anaconda/conda Library\\usr\\bin", prefix / "Library" / "usr" / "bin"),
        ("Windows System32", windows),
    ]
    folders += [
        ("on PATH (not searched for modules)", Path(p)) for p in os.environ.get("PATH", "").split(";") if p
    ]
    unique: dict[Path, tuple[str, Path]] = {}
    for role, folder in folders:
        if folder.is_dir():
            unique.setdefault(folder.resolve(), (role, folder))
    return list(unique.values())


@cache
def files_in(folder: Path) -> dict[str, Path]:
    """The files in ``folder`` by lower-case name: Windows matches DLL names regardless of case."""
    try:
        return {entry.name.lower(): entry for entry in folder.iterdir() if entry.is_file()}
    except OSError:
        return {}


def copies(name: str, folders: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    return [(role, path) for role, folder in folders if (path := files_in(folder).get(name.lower()))]


def is_windows_folder(path: Path) -> bool:
    return Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).resolve() in path.resolve().parents


# ── the check ────────────────────────────────────────────────────────────────


def static_check(pyside: Path, folders: list[tuple[str, Path]]) -> None:
    """Walks QtCore's DLL dependencies and reports DLL names with doubtful copies."""
    needs: dict[str, dict[str, set[str]]] = {}  # DLL name -> importer -> functions
    queue, seen = [pyside / "QtCore.pyd"], set()
    while queue:
        module = queue.pop()
        if module in seen or (pe := read(module)) is None:
            continue
        seen.add(module)
        for dll, functions in pe.imports().items():
            if dll.startswith(("api-ms-win-", "ext-ms-")):
                continue  # Windows API sets, resolved by Windows itself
            needs.setdefault(dll, {}).setdefault(module.name, set()).update(functions)
            found = copies(dll, folders)
            if found and not is_windows_folder(found[0][1]):
                queue.append(found[0][1])

    doubtful = 0
    for dll, importers in sorted(needs.items()):
        wanted = set().union(*importers.values())
        found = copies(dll, folders)
        report = []
        for role, path in found:
            pe = read(path)
            missing = wanted - pe.exports() if pe else wanted
            status = (
                "complete"
                if not missing
                else f"LACKS {len(missing)} of {len(wanted)}, e.g. {sorted(missing)[:3]}"
            )
            report.append(f"    {status:60}  {path}  [{role}]")
        if len(found) > 1 or not found or any("LACKS" in line for line in report):
            doubtful += 1
            print(f"\n{dll}, wanted by {', '.join(sorted(importers))} ({len(wanted)} functions):")
            print("\n".join(report) if report else "    NOT FOUND in any folder Windows searches")
    if not doubtful:
        print("\nEvery DLL QtCore needs has a single, complete copy.")


def live_check(pyside: Path, names: list[str]) -> None:
    """Imports TimeShot's app package and PySide6's QtCore; if that fails, resolves the imports of
    QtCore and the PySide6 DLLs it needs the way Windows' loader does, naming each function the
    loaded DLLs lack. Then lists which copies of ``names`` are loaded."""
    import ctypes  # noqa: PLC0415  (Windows only)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleFileNameW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
    kernel32.GetProcAddress.restype = ctypes.c_void_p
    kernel32.GetProcAddress.argtypes = (ctypes.c_void_p, ctypes.c_char_p)

    def file_of(handle: int) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        kernel32.GetModuleFileNameW(handle, buffer, len(buffer))
        return buffer.value

    try:
        import timeshot.gui  # noqa: PLC0415  (loads the right DLLs first, if this checkout does)

        loads = (
            " (it loads Windows' ICU and PySide6's Qt first)" if hasattr(timeshot.gui, "QT_LIBRARIES") else ""
        )
        print(f"\ntimeshot.gui imported{loads}")
    except ImportError as error:
        print(f"\ntimeshot.gui not imported: {error}")
    try:
        import PySide6.QtCore  # noqa: PLC0415

        print(f"PySide6.QtCore imported fine (Qt {PySide6.QtCore.qVersion()})")
    except ImportError as error:
        print(f"PySide6.QtCore failed: {error}")
        # GetProcAddress follows forwarded functions and API sets, exactly as the loader does.
        for module in (
            pyside.parent / "shiboken6" / "shiboken6.abi3.dll",
            pyside / "pyside6.abi3.dll",
            pyside / "QtCore.pyd",
        ):
            pe = read(module)
            if pe is None:
                continue
            print(f"  {module.name}, its imports resolved against the loaded DLLs:")
            problems = 0
            for dll, functions in sorted(pe.imports().items()):
                # The copy already loaded, else the one next to the module, else as Windows finds it.
                handle = kernel32.GetModuleHandleW(dll)
                if not handle:
                    beside = module.parent / dll
                    try:
                        handle = ctypes.WinDLL(str(beside) if beside.is_file() else dll)._handle
                    except OSError as load_error:
                        problems += 1
                        print(f"    {dll}: cannot be loaded ({load_error})")
                        continue
                missing = sorted(f for f in functions if not kernel32.GetProcAddress(handle, f.encode()))
                if missing:
                    problems += 1
                    print(f"    {dll} ({file_of(handle)}) LACKS {len(missing)}: {missing[:6]}")
            if not problems:
                print("    all imports resolve")
    print("Loaded now:")
    for name in names:
        if handle := kernel32.GetModuleHandleW(name):
            print(f"    {name:24} {file_of(handle)}")


def main() -> int:
    spec = importlib.util.find_spec("PySide6")
    if spec is None or not spec.submodule_search_locations:
        print("PySide6 is not installed for this Python.")
        return 1
    pyside = Path(next(iter(spec.submodule_search_locations)))
    print(f"Python {sys.version.split()[0]} at {sys.executable}\nPySide6 at {pyside}")
    folders = search_folders(pyside)
    static_check(pyside, folders)
    if sys.platform == "win32":
        live_check(
            pyside,
            [
                "icuuc.dll",
                "icu.dll",
                "Qt6Core.dll",
                "Qt6Gui.dll",
                "Qt6Widgets.dll",
                "Qt6Svg.dll",
                "pyside6.abi3.dll",
                "shiboken6.abi3.dll",
                "msvcp140.dll",
                "vcruntime140.dll",
                "vcruntime140_1.dll",
                "ucrtbase.dll",
                "python3.dll",
                "python312.dll",
            ],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
