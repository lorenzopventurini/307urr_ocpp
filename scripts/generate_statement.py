"""
Generate a PDF statement for a charger/household for a given month.

Usage:
    python scripts/generate_statement.py [YEAR] [MONTH]

Defaults to the previous calendar month if no arguments given.
Output is written to statements/<charger>_<YYYY>_<MM>.pdf
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from ocpp_garage.adapters.easee.client import EaseeClient
from ocpp_garage.billing.engine import BillingEngine
from ocpp_garage.billing.pdf import generate_pdf
from ocpp_garage.config import settings
from ocpp_garage.tariff.flat import FlatRateTariffProvider


def main() -> None:
    today = date.today()

    if len(sys.argv) == 3:
        year, month = int(sys.argv[1]), int(sys.argv[2])
    else:
        # Default to current month (or previous if before the 5th)
        if today.day < 5:
            first = today.replace(day=1)
            prev = first.replace(day=1) - __import__("datetime").timedelta(days=1)
            year, month = prev.year, prev.month
        else:
            year, month = today.year, today.month

    tariff = FlatRateTariffProvider(
        pence_per_kwh=settings.tariff_flat_price_pence,
        interval_minutes=settings.tariff_interval_minutes,
    )

    print(f"Generating statement for {settings.easee_charger_serial} — {year}/{month:02d} ...")

    with EaseeClient(settings.easee_username, settings.easee_password) as client:
        engine = BillingEngine(client, tariff)
        bill = engine.generate_bill(
            charger_serial=settings.easee_charger_serial,
            household_name="Bay 10 Resident",       # TODO: pull from household DB
            household_address="307 Upper Richmond Rd\nLondon SW15 6SS",
            year=year,
            month=month,
        )

    print(f"  Total energy : {bill.total_kwh:.3f} kWh")
    print(f"  Total cost   : £{bill.total_cost_pounds:.2f}")
    print(f"  Line items   : {len(bill.lines)} hours with consumption")

    out_dir = Path("statements")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{bill.charger_id}_{year}_{month:02d}.pdf"

    pdf_bytes = generate_pdf(bill)
    out_path.write_bytes(pdf_bytes)
    print(f"  Saved to     : {out_path}")


if __name__ == "__main__":
    main()
