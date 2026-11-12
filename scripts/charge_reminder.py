"""
Email the tenant if their car is plugged in but has not started charging.

Usage:
    python scripts/charge_reminder.py                # report only
    python scripts/charge_reminder.py --email        # send the reminder if due

Run hourly by deploy/github-actions/charge-reminder.yml. All the logic lives in
ocpp_garage.charge_reminder.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ocpp_garage.charge_reminder import main

if __name__ == "__main__":
    raise SystemExit(main())
