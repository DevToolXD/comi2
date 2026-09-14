"""Enables `python3 -m scanner <target>`."""

import os
import sys

# Ensure the parent dir (containing scan.py) is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scan import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
