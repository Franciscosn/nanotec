from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INNER_RUN = ROOT / "python_rewrite" / "run.py"


def main() -> None:
    os.execv(sys.executable, [sys.executable, str(INNER_RUN), *sys.argv[1:]])


if __name__ == "__main__":
    main()
