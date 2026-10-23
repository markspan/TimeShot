"""Entry point that Nuitka compiles into TimeShot.exe (see build.py next to this file).

One executable is both the desktop app (started without arguments, e.g. by double-clicking)
and the command line (started with arguments: ``TimeShot.exe devices``), exactly like
``python -m timeshot``. The camera recorder processes start through it as well, which
``freeze_support()`` recognises.
"""

import multiprocessing
import sys

from timeshot.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
