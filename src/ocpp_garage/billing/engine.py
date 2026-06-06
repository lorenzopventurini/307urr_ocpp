"""
Billing engine. Fetches hourly usage from Easee, joins to tariff prices,
and produces a Bill. No database required at this stage.
"""

from datetime import datetime, timezone

from ..adapters.easee.client import EaseeClient
from ..tariff.base import TariffProvider
from .models import Bill, BillLine


class BillingEngine:
    def __init__(self, easee_client: EaseeClient, tariff: TariffProvider) -> None:
        self._client = easee_client
        self._tariff = tariff

    def generate_bill(
        self,
        charger_serial: str,
        household_name: str,
        household_address: str,
        year: int,
        month: int,
        landlord_name: str = "307 Upper Richmond Road Management",
        landlord_address: str = "Upper Richmond Rd, London SW15 6SS",
    ) -> Bill:
        """
        Generate a bill for one charger/household for a calendar month.

        Energy source: Easee hourly usage API (UTC-aligned hourly buckets).
        Only intervals with non-zero energy are included as bill lines;
        zero-energy hours are silently dropped (no charge, no clutter).
        """
        buckets = self._client.get_charger_usage_month(charger_serial, year, month)

        lines: list[BillLine] = []
        for bucket in buckets:
            kwh = float(bucket.get("totalEnergy", 0.0))
            if kwh <= 0:
                continue

            interval_start = _parse_utc(bucket["from"])
            price = self._tariff.get_price(interval_start)

            lines.append(BillLine(
                interval_start_utc=interval_start,
                kwh=kwh,
                pence_per_kwh=price.pence_per_kwh,
            ))

        return Bill(
            household_name=household_name,
            household_address=household_address,
            charger_id=charger_serial,
            period_year=year,
            period_month=month,
            lines=lines,
            landlord_name=landlord_name,
            landlord_address=landlord_address,
        )


def _parse_utc(iso: str) -> datetime:
    """Parse an ISO 8601 UTC string from the Easee API into an aware datetime."""
    # Easee returns e.g. "2026-06-04T08:00:00Z"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)
