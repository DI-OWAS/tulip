"""Launcher for the Liberty generator.

Kept so the historical command line still works:

    python src/py/tulip/scripts/generate_lib.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tulip.scripts.libgen import main  # noqa: E402  pylint: disable=C0413

if __name__ == "__main__":
    main()
