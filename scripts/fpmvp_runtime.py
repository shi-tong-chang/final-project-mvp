#!/usr/bin/env python3
"""從任意 cwd 啟動 repository-local stdlib runtime CLI。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.cli import main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(main())
