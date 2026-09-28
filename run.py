"""Start Bass Stem Studio from any working directory:  python run.py [--no-browser]"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)

from bss.app import main  # noqa: E402

if __name__ == "__main__":
    main()
