"""PyInstaller entry point for the command line tool."""

import sys

from spacemap.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
