"""Compatibility development entry that references the production entry in main/."""

from __future__ import annotations

import sys
from pathlib import Path

_MAIN_BACKEND = Path(__file__).resolve().parent.parent / "main" / "backend"
if str(_MAIN_BACKEND) not in sys.path:
    sys.path.insert(0, str(_MAIN_BACKEND))

from app.production import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
