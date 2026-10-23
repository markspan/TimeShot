#!/usr/bin/env python3
"""Build a standalone TimeShot 2 for Windows with Nuitka.

Run on 64-bit Windows, in a fresh virtual environment with Python 3.12 or newer::

    py -3.12 -m venv .venv-build
    .venv-build\\Scripts\\activate
    pip install -r packaging\\nuitka\\requirements.txt
    pip install --no-deps .
    python packaging\\nuitka\\build.py
    python packaging\\nuitka\\smoke_test.py build\\TimeShot\\TimeShot.exe

Nuitka needs a C compiler: Visual Studio (or its Build Tools) with the "Desktop development
with C++" workload. Without one it offers to download MinGW64 itself.

The result is ``dist\\TimeShot-<version>-windows-x64.zip``, holding a ``TimeShot`` folder:

* ``TimeShot.exe`` is the desktop app when started without arguments (no console window),
  and the command line when started from a terminal with arguments
  (``TimeShot.exe devices``, ``TimeShot.exe record --help``);
* everything it needs is inside the folder: Python, Qt, FFmpeg (PyAV) and liblsl (pylsl),
  so nothing needs installing on the computer that runs it;
* ``LICENSE.txt`` and ``THIRD-PARTY-NOTICES.txt`` hold the licenses.

Under GitHub Actions the executable and archive paths are written to ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
APP_NAME = "TimeShot"
LAUNCHER = HERE / "launcher.py"
ICON_SVG = ROOT / "src" / "timeshot" / "resources" / "timeshot.svg"
NOTICES = HERE / "THIRD-PARTY-NOTICES.txt"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build", help="Nuitka's output folder")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist", help="folder for the zip")
    args = parser.parse_args()

    if sys.platform != "win32":
        raise SystemExit("This script builds the Windows version: run it on Windows.")
    import timeshot  # noqa: PLC0415  (after the platform check: a clear message beats an import error)

    version = timeshot.__version__
    print(f"Building {APP_NAME} {version} for windows-x64", flush=True)
    shutil.rmtree(args.build_dir, ignore_errors=True)
    icon = render_ico(args.build_dir / f"{APP_NAME}.ico")
    command = nuitka_command(version, args.build_dir, icon)
    print(" ".join(command), flush=True)
    if subprocess.run(command, check=False).returncode:
        raise SystemExit("Nuitka failed; see its output above.")

    folder = args.build_dir / APP_NAME
    (args.build_dir / f"{LAUNCHER.stem}.dist").rename(folder)
    executable = folder / f"{APP_NAME}.exe"
    if not executable.is_file():
        raise SystemExit(f"Nuitka did not produce {executable}")
    shutil.copy2(ROOT / "LICENSE", folder / "LICENSE.txt")
    shutil.copy2(NOTICES, folder / NOTICES.name)

    args.dist_dir.mkdir(parents=True, exist_ok=True)
    base = args.dist_dir / f"{APP_NAME}-{version}-windows-x64"
    archive = Path(shutil.make_archive(str(base), "zip", args.build_dir, APP_NAME))
    print(f"Executable: {executable}\nArchive:    {archive}")
    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as sink:
            sink.write(f"executable={executable}\narchive={archive}\nversion={version}\n")
    return 0


def nuitka_command(version: str, build_dir: Path, icon: Path) -> list[str]:
    """The Nuitka invocation."""
    return [
        sys.executable, "-m", "nuitka",
        "--mode=standalone",
        f"--output-dir={build_dir}",
        f"--output-filename={APP_NAME}.exe",
        f"--report={build_dir / 'compilation-report.xml'}",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--noinclude-qt-translations",  # the app is in English only
        # The camera backends are imported when chosen, so name the whole package.
        "--include-package=timeshot",
        "--include-package-data=timeshot:*.svg",  # the window icon
        "--nofollow-import-to=linuxpy",  # the Linux camera backend's library
        # No console window when double-clicked; command-line output still appears when
        # started from a terminal. The recorder processes (the same executable) get no
        # console window either.
        "--windows-console-mode=attach",
        f"--windows-icon-from-ico={icon}",
        "--company-name=Mark Span",
        f"--product-name={APP_NAME}",
        f"--file-version={windows_version(version)}",
        f"--product-version={windows_version(version)}",
        "--file-description=Record cameras with an LSL marker for every frame",
        "--copyright=GPL-2.0-only",
        str(LAUNCHER),
    ]  # fmt: skip


def windows_version(version: str) -> str:
    """The four numbers Windows wants for a file version: "2.0.0.dev0" becomes "2.0.0.0"."""
    match = re.match(r"\d+(?:\.\d+)*", version)
    numbers = match.group().split(".")[:4] if match else []
    return ".".join([*numbers, "0", "0", "0", "0"][:4])


def render_ico(path: Path) -> Path:
    """Renders the SVG icon into a Windows ``.ico``, drawing every size from the vector
    image (sharper small sizes than scaling one bitmap down)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window needed
    from PIL import Image  # noqa: PLC0415  (build-only dependencies)
    from PySide6.QtCore import QRectF, Qt  # noqa: PLC0415
    from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: PLC0415
    from PySide6.QtSvg import QSvgRenderer  # noqa: PLC0415

    _app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(ICON_SVG))
    if not renderer.isValid():
        raise SystemExit(f"{ICON_SVG} is not a valid SVG image.")

    def render(size: int) -> Image.Image:
        image = QImage(size, size, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        # RGBA8888 is byte-ordered R, G, B, A everywhere; rows may be padded.
        return Image.frombuffer(
            "RGBA", (size, size), bytes(image.constBits()), "raw", "RGBA", image.bytesPerLine(), 1
        )

    images = {size: render(size) for size in ICO_SIZES}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pillow takes the largest image as the base and the others, matched by size, as
    # replacements for what it would otherwise scale down.
    largest = max(ICO_SIZES)
    images[largest].save(
        path,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[images[s] for s in ICO_SIZES if s != largest],
    )
    return path


if __name__ == "__main__":
    sys.exit(main())
