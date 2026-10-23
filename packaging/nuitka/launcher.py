"""Entry point that Nuitka compiles into TimeShot.exe (see build.py next to this file).

One executable is both the desktop app (started without arguments, e.g. by double-clicking)
and the command line (started with arguments: ``TimeShot.exe devices``), exactly like
``python -m timeshot``. The camera recorder processes start through it as well: Nuitka runs
this module in them under the name ``__parents_main__`` (so the block at the bottom does not
run) and then hands over to multiprocessing.
"""

import multiprocessing
import sys

from timeshot.stdio import reset_c_stream_modes

# First, in every TimeShot.exe process (recorder processes included): undo the UTF-8 mode in
# which Nuitka's console code leaves the C runtime's streams, before anything native writes to
# them (see timeshot.stdio).
reset_c_stream_modes()

from timeshot.cli import main  # noqa: E402  (after the reset above)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
