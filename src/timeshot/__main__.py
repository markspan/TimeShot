"""``python -m timeshot``: the desktop app without arguments, the command line with them."""

import multiprocessing
import sys

from timeshot.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # recorder processes of a compiled build start through here
    sys.exit(main())
