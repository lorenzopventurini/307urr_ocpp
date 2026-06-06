"""
Run once to discover the Easee site/circuit/charger structure and validate
what meter data is available. Prints raw API responses to guide data modelling.

Usage:
    uv run python scripts/discover_easee.py
"""

import json
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from ocpp_garage.config import settings
from ocpp_garage.adapters.easee.client import EaseeClient, EaseeAuthError, EaseeApiError


def pretty(label: str, data: object) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)
    print(json.dumps(data, indent=2, default=str))


def main() -> None:
    print(f"Connecting as {settings.easee_username} ...")
    print(f"Target charger: {settings.easee_charger_serial}")

    try:
        with EaseeClient(settings.easee_username, settings.easee_password) as client:

            profile = client.get_profile()
            pretty("Account profile", profile)

            sites = client.get_sites()
            pretty("Sites", sites)

            for site in sites:
                site_id = site.get("id") or site.get("siteId")
                if site_id:
                    site_detail = client.get_site(site_id)
                    pretty(f"Site detail (id={site_id})", site_detail)
                    # Circuits are embedded in site detail; no separate call needed.

            serial = settings.easee_charger_serial
            charger = client.get_charger(serial)
            pretty(f"Charger {serial}", charger)

            state = client.get_charger_state(serial)
            pretty(f"Charger state {serial}", state)

            config = client.get_charger_config(serial)
            pretty(f"Charger config {serial}", config)

            # Last 7 days of sessions
            from datetime import date, timedelta
            to_date = date.today().isoformat()
            from_date = (date.today() - timedelta(days=7)).isoformat()
            try:
                sessions = client.get_charger_sessions(serial, from_date, to_date)
                pretty(f"Sessions {from_date} to {to_date}", sessions)
            except EaseeApiError as e:
                print(f"\n[sessions endpoint] {e}")

            try:
                usage = client.get_charger_usage(serial, from_date, to_date)
                pretty(f"Hourly usage {from_date} to {to_date}", usage)
            except EaseeApiError as e:
                print(f"\n[usage endpoint] {e}")

    except EaseeAuthError as e:
        print(f"\nAuthentication failed: {e}")
        sys.exit(1)
    except EaseeApiError as e:
        print(f"\nAPI error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
