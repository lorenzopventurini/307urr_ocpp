"""
User-facing statement generator.

Runs both from source and as a frozen Windows executable (see statement.spec).
Configuration is deliberately NOT bundled into the binary: `.env`, `households.toml`
and the `statements/` output folder are resolved relative to the application
directory — next to the .exe when frozen, the project root when run from source.
That keeps credentials and tenant details out of the distributed file, and lets
the tariff or tenant list be updated without a rebuild.

Also runs unattended in GitHub Actions (deploy/github-actions/), where settings
come from environment variables rather than a .env file, and names and email
addresses are kept out of the run log.
"""

import argparse
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ocpp_garage.billing.mailer import Recipients
    from ocpp_garage.billing.models import Bill
    from ocpp_garage.households import Household, SiteConfig

FROZEN = getattr(sys, "frozen", False)
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"

# The frozen build omits Pillow — these statements are text only — but fpdf2
# warns about it on import. Nothing the end user can act on, so keep it quiet.
warnings.filterwarnings("ignore", message="Pillow could not be imported")

# A Windows console runs a legacy code page, not UTF-8, and which one varies by
# machine — a pound sign printed here can arrive as a replacement character. So
# console output is plain ASCII ("GBP 18.64"); the PDF itself is unaffected and
# still renders a proper "£". This guard keeps an unusual character in a tenant
# name from taking the program down with a UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


class StatementError(Exception):
    """Any failure worth showing the end user as a plain message, not a traceback."""


# ------------------------------------------------------------------ locations

def app_dir() -> Path:
    """Directory holding .env, households.toml and the statements/ output folder."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def _bootstrap_env(base: Path) -> None:
    """
    Load .env into the environment, if there is one.

    Must run before ocpp_garage.config is imported: config.py instantiates
    Settings() at import time, so the variables have to exist by then.
    Without a .env file the settings must already be in the environment, as
    they are in a GitHub Actions run.
    """
    from dotenv import load_dotenv

    env_path = base / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    elif "EASEE_USERNAME" not in os.environ:
        raise StatementError(
            f"Configuration file not found:\n  {env_path}\n\n"
            "Place the .env file in the same folder as this program."
        )


def _load_settings():
    """Import the settings, turning a validation failure into a readable message."""
    from pydantic import ValidationError

    try:
        from ocpp_garage.config import settings
    except ValidationError as exc:
        fields = ", ".join(str(e["loc"][0]).upper() for e in exc.errors())
        raise StatementError(f"Missing or invalid settings: {fields}")
    return settings


def _redact(text: str) -> str:
    """Personal details stay out of CI logs; the person at the console sees them."""
    return "(redacted)" if IN_CI else text


# ------------------------------------------------------------------ period handling

def default_period(today: date | None = None) -> tuple[int, int]:
    """
    The period to offer by default: the current month, or the previous month
    if we are in the first few days of a new one (usage for the month just
    ended is what you normally want to bill on the 1st).
    """
    today = today or date.today()
    if today.day < 5:
        prev = today.replace(day=1) - timedelta(days=1)
        return prev.year, prev.month
    return today.year, today.month


def previous_month(today: date | None = None) -> tuple[int, int]:
    """The last complete calendar month — what a scheduled run bills."""
    today = today or date.today()
    prev = today.replace(day=1) - timedelta(days=1)
    return prev.year, prev.month


def parse_period(text: str) -> tuple[int, int]:
    """Accept 'YYYY-MM', 'YYYY/MM' or 'YYYY MM'."""
    cleaned = text.strip().replace("/", "-").replace(" ", "-")
    parts = [p for p in cleaned.split("-") if p]
    if len(parts) != 2:
        raise StatementError(f"Could not read {text!r} as a period. Use the form 2026-07.")
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        raise StatementError(f"Could not read {text!r} as a period. Use the form 2026-07.")
    return validate_period(year, month)


def validate_period(year: int, month: int, today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    if not 1 <= month <= 12:
        raise StatementError(f"{month} is not a valid month - use 1 to 12.")
    if year < 2020 or year > today.year + 1:
        raise StatementError(f"{year} is outside the range this tool supports.")
    if (year, month) > (today.year, today.month):
        raise StatementError(
            f"{month:02d}/{year} is in the future - no usage data exists for it yet."
        )
    return year, month


def period_label(year: int, month: int) -> str:
    import calendar

    return f"{calendar.month_name[month]} {year}"


def prompt_period() -> tuple[int, int]:
    """Ask the end user which month to bill, defaulting to the obvious one."""
    dy, dm = default_period()
    while True:
        answer = input(
            f"Billing period as YYYY-MM (press Enter for {period_label(dy, dm)}): "
        ).strip()
        if not answer:
            return dy, dm
        try:
            return parse_period(answer)
        except StatementError as exc:
            print(f"  {exc}\n")


# ------------------------------------------------------------------ main flow

USAGE = "Usage: generate_statement [YYYY-MM | YYYY MM | --previous-month] [--email]"


@dataclass
class Statement:
    bill: "Bill"
    pdf_bytes: bytes
    path: Path


def load_household(base: Path, settings) -> tuple["SiteConfig", "Household"]:
    from ocpp_garage.households import load as load_households

    households_path = base / "households.toml"
    if not households_path.exists():
        raise StatementError(
            f"Configuration file not found:\n  {households_path}\n\n"
            "Place households.toml in the same folder as this program."
        )
    site = load_households(households_path)
    try:
        return site, site.get_household(settings.easee_charger_serial)
    except ValueError as exc:
        raise StatementError(str(exc))


def generate(year: int, month: int, base: Path, settings, site, household) -> Statement:
    """Fetch usage, build the bill, write the PDF."""
    from ocpp_garage.adapters.easee.client import EaseeApiError, EaseeAuthError, EaseeClient
    from ocpp_garage.billing.engine import BillingEngine
    from ocpp_garage.billing.pdf import generate_pdf
    from ocpp_garage.tariff.flat import FlatRateTariffProvider

    serial = settings.easee_charger_serial
    tariff = FlatRateTariffProvider(
        pence_per_kwh=settings.tariff_flat_price_pence,
        interval_minutes=settings.tariff_interval_minutes,
    )

    print(f"\nCharger {serial} - {_redact(household.tenant_name)}")
    print(f"Period  {period_label(year, month)}")
    print("\nContacting Easee ...")

    try:
        with EaseeClient(settings.easee_username, settings.easee_password) as client:
            engine = BillingEngine(client, tariff)
            bill = engine.generate_bill(
                charger_serial=serial,
                household_name=household.tenant_name,
                household_address=household.tenant_address,
                year=year,
                month=month,
                landlord_name=site.landlord_name,
                landlord_address=site.landlord_address,
            )
    except EaseeAuthError:
        raise StatementError(
            "Easee rejected the login. Check EASEE_USERNAME and EASEE_PASSWORD."
        )
    except EaseeApiError as exc:
        raise StatementError(f"Easee returned an error: {exc}")
    except OSError as exc:
        raise StatementError(f"Could not reach Easee - check the internet connection.\n  {exc}")

    print(f"  Total energy : {bill.total_kwh:.3f} kWh")
    print(f"  Total cost   : GBP {bill.total_cost_pounds:.2f}")
    print(f"  Line items   : {len(bill.lines)} hours with consumption")

    if not bill.lines:
        print("\n  Note: no charging was recorded in this period.")
        print("  The statement will still be produced, showing a zero balance.")

    out_dir = base / "statements"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{bill.charger_id}_{year}_{month:02d}.pdf"

    pdf_bytes = generate_pdf(bill)
    try:
        out_path.write_bytes(pdf_bytes)
    except OSError as exc:
        raise StatementError(f"Could not write the statement to {out_path}:\n  {exc}")

    print(f"\n  Saved to     : {out_path}")
    return Statement(bill=bill, pdf_bytes=pdf_bytes, path=out_path)


def prepare_email(settings, site, household) -> "Recipients":
    """
    Check the email settings and work out recipients BEFORE contacting Easee,
    so a misconfiguration fails in seconds rather than after the slow part.
    """
    from ocpp_garage.billing.mailer import MailerError, resolve_recipients

    missing = [
        name for name, value in (
            ("SMTP_HOST", settings.smtp_host),
            ("SMTP_USERNAME", settings.smtp_username),
            ("SMTP_PASSWORD", settings.smtp_password),
        ) if not value
    ]
    if missing:
        raise StatementError(f"Cannot send email - missing settings: {', '.join(missing)}")

    try:
        return resolve_recipients(
            tenant_email=household.tenant_email,
            landlord_email=site.landlord_email,
            test_recipient=settings.statement_test_recipient,
        )
    except MailerError as exc:
        raise StatementError(str(exc))


def email_statement(statement: Statement, recipients: "Recipients", settings, household) -> None:
    from ocpp_garage.billing.mailer import MailerError, build_message, send

    msg = build_message(
        bill=statement.bill,
        pdf_bytes=statement.pdf_bytes,
        pdf_filename=statement.path.name,
        recipients=recipients,
        sender=settings.email_from or settings.smtp_username,
        sender_name=settings.email_from_name,
        bay=household.bay,
        reply_to=settings.email_reply_to,
    )

    print("\nSending email ...")
    try:
        send(
            msg,
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            security=settings.smtp_security,
        )
    except MailerError as exc:
        raise StatementError(str(exc))

    if recipients.test_mode:
        print(f"  TEST MODE - sent only to {_redact(recipients.to[0])}")
        print("  (clear STATEMENT_TEST_RECIPIENT to send to the real recipients)")
    else:
        print(f"  To : {_redact(', '.join(recipients.to))}")
        if recipients.cc:
            print(f"  Cc : {_redact(', '.join(recipients.cc))}")
    print("  Sent.")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate_statement",
        description="Generate, and optionally email, a monthly EV charging statement.",
    )
    parser.add_argument("period", nargs="*", help="billing month as YYYY-MM, or YYYY MM")
    parser.add_argument(
        "--previous-month", action="store_true",
        help="bill the last complete calendar month (for scheduled runs)",
    )
    parser.add_argument(
        "--email", action="store_true",
        help="email the statement to the tenant, copying the management company",
    )
    return parser.parse_args(argv)


def _resolve_period(args: argparse.Namespace) -> tuple[int, int]:
    if args.previous_month:
        if args.period:
            raise StatementError("Give either a period or --previous-month, not both.")
        return previous_month()
    if len(args.period) == 2:
        try:
            year, month = int(args.period[0]), int(args.period[1])
        except ValueError:
            raise StatementError(USAGE)
        return validate_period(year, month)
    if len(args.period) == 1:
        return parse_period(args.period[0])
    if not args.period:
        # No period given: interactive when a console user can answer,
        # otherwise fall back to the default period.
        if sys.stdin is not None and sys.stdin.isatty():
            return prompt_period()
        return default_period()
    raise StatementError(USAGE)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    base = app_dir()

    print("=" * 62)
    print(" EV Charging Statement Generator")
    print("=" * 62)

    try:
        _bootstrap_env(base)
        settings = _load_settings()
        site, household = load_household(base, settings)
        recipients = prepare_email(settings, site, household) if args.email else None
        year, month = _resolve_period(args)

        statement = generate(year, month, base, settings, site, household)
        if recipients is not None:
            email_statement(statement, recipients, settings, household)
    except StatementError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        _pause()
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130

    _pause()
    return 0


def _pause() -> None:
    """Stop a double-clicked .exe window from vanishing before it can be read."""
    if FROZEN and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nPress Enter to close ...")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
