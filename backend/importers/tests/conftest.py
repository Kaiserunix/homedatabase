from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SRC_DIR = _BACKEND_DIR / "src"

for candidate in (_BACKEND_DIR, _SRC_DIR):
    path_str = str(candidate)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
