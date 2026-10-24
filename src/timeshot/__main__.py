"""``python -m timeshot``: the desktop app without arguments, the command line with them."""

import multiprocessing
import sys

from timeshot.cli import main

if __name__ == "__main__":
    # The recorder processes of a compiled build start through here: freeze_support() hands
    # them over to multiprocessing instead of starting TimeShot again.
    multiprocessing.freeze_support()
    sys.exit(main())
