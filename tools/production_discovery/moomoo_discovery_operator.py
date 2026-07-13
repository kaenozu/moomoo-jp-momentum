#!/usr/bin/env python3
"""Run and review the moomoo production read-only discovery bundle."""

from __future__ import annotations

import sys
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from moomoo_operator_common import *  # noqa: F401,F403,E402
from moomoo_operator_review import *  # noqa: F401,F403,E402
from moomoo_operator_cli import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    raise SystemExit(main())
