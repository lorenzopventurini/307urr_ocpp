"""
Generate a PDF statement for a charger/household for a given month.

Usage:
    python scripts/generate_statement.py              # interactive, or default period
    python scripts/generate_statement.py 2026-07
    python scripts/generate_statement.py 2026 7

Defaults to the current month, or the previous one during the first days of a
new month. Output is written to statements/<charger>_<YYYY>_<MM>.pdf

This file is also the entry point for the frozen Windows build — see
statement.spec and scripts/build_exe.ps1. All the logic lives in
ocpp_garage.statement_cli so both paths behave identically.
"""

import sys
from pathlib import Path

# Running from source: make src/ importable. When frozen, the package is bundled.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ocpp_garage.statement_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
