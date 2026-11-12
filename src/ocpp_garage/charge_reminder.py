"""
Reminder email for a car that is plugged in but has not started charging.

Meant to run hourly (see deploy/github-actions/charge-reminder.yml). Each run
looks at the charger once and emails the tenant if their car has been plugged
in for longer than the threshold without taking any energy. One reminder per
plug-in: a small JSON state file remembers which sessions have had one, and is
carried between runs by the workflow.

Which charger states count as "plugged in but not charging" is decided here in
vendor-neutral terms; the Easee codes behind them are mapped in the adapter.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

from ocpp_garage.adapters.base import ChargerState, ChargerStatus, ConnectedSession

LONDON = ZoneInfo("Europe/London")

# Car connected, no power flowing, and something the tenant may want to act on.
# Not included: COMPLETED (the car has finished or paused itself), CHARGING,
# AVAILABLE (nothing plugged in), FAULTED, OFFLINE, DEAUTHENTICATING.
WAITING = {
    ChargerStatus.AWAITING_START,           # charger has not started supplying power
    ChargerStatus.READY_TO_CHARGE,          # charger offering power, car not taking it
    ChargerStatus.AWAITING_AUTHENTICATION,  # charge needs authorising
}

# Below this much energy in the session, the car has not really started. Above
# it, it charged and then stopped, which is not what this reminder is for.
STARTED_KWH = 0.1

# Without a session id from the charger, a gap longer than this between two
# sightings (e.g. overnight, when the workflow does not run) means we cannot
# tell it is the same plug-in, so it is treated as a new one.
SAME_SESSION_GAP = timedelta(hours=3)


class ReminderError(Exception):
    """A failure worth showing as a plain message, not a traceback."""


# ------------------------------------------------------------------ decision

@dataclass(frozen=True)
class Assessment:
    remind: bool
    reason: str                         # one line for the run log
    plugged_in_since: datetime | None
    record: dict | None                 # state to keep for this charger; None = forget


def assess(
    state: ChargerState,
    session: ConnectedSession | None,
    record: dict | None,
    now: datetime,
    threshold: timedelta,
) -> Assessment:
    """Decide whether to send a reminder now. Pure: no I/O."""
    if state.status not in WAITING:
        return Assessment(False, f"status {state.status.value}", None, None)
    if state.held_by_charger_schedule:
        return Assessment(False, "held by the charger's schedule", None, None)
    energy = session.energy_kwh if session else None
    if energy is not None and energy >= STARTED_KWH:
        return Assessment(
            False, f"{energy:.2f} kWh delivered this session, so it did start", None, None
        )

    key = session.session_key if session else None
    if not _same_session(record, key, now):
        record = {"session": key, "first_seen": now.isoformat(), "reminded": False}
    record = {**record, "last_seen": now.isoformat()}

    # Best evidence first: the session's plug-in time, then when the charger
    # entered its current state, then when this check first saw it waiting.
    since = (
        (session.started_utc if session else None)
        or state.status_since_utc
        or datetime.fromisoformat(record["first_seen"])
    )
    waited = now - since
    if record["reminded"]:
        return Assessment(False, "already reminded for this plug-in", since, record)
    if waited < threshold:
        return Assessment(
            False, f"plugged in {_duration(waited)}, under {_duration(threshold)}", since, record
        )
    return Assessment(
        True, f"{state.status.value} for {_duration(waited)} with no energy", since, record
    )


def _same_session(record: dict | None, key: str | None, now: datetime) -> bool:
    if not record or record.get("session") != key:
        return False
    if key is not None:
        return True
    last_seen = datetime.fromisoformat(record["last_seen"])
    return now - last_seen <= SAME_SESSION_GAP


def _duration(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


# ------------------------------------------------------------------ email

_HINTS = {
    ChargerStatus.AWAITING_AUTHENTICATION: (
        "The charger is waiting for the charge to be authorised. You can start it "
        "from the Easee app, or with your charging key tag if you have one."
    ),
    ChargerStatus.READY_TO_CHARGE: (
        "The charger is ready to supply power, but the car is not accepting it. "
        "It is worth checking the car's charging settings, and that the cable is "
        "pushed fully in at both ends."
    ),
    ChargerStatus.AWAITING_START: (
        "The charger has not started supplying power. Unplugging the cable and "
        "plugging it back in often clears this."
    ),
}


def _local_time(when: datetime, now: datetime) -> str:
    when, now = when.astimezone(LONDON), now.astimezone(LONDON)
    clock = when.strftime("%H:%M")
    if when.date() == now.date():
        return clock
    if when.date() == now.date() - timedelta(days=1):
        return f"{clock} yesterday"
    return f"{clock} on {when.strftime('%A')} {when.day} {when.strftime('%B')}"


def build_reminder(
    *,
    status: ChargerStatus,
    plugged_in_since: datetime,
    now: datetime,
    tenant_name: str,
    bay: str,
    to: list[str],
    intended: list[str],
    test_mode: bool,
    sender: str,
    sender_name: str,
    landlord_name: str,
    support_email: str = "",
    reply_to: str = "",
) -> EmailMessage:
    where = f"the charger at {bay}" if bay else "your charger"

    subject = "Your car is plugged in but not charging"
    if bay:
        subject += f" - {bay}"
    if test_mode:
        subject = f"[TEST] {subject}"

    lines: list[str] = []
    if test_mode:
        lines += [
            "*** TEST MESSAGE (charging reminder) ***",
            "In live use this email would be sent to:",
            f"  {', '.join(intended) or '(no address configured yet)'}",
            "",
            "-" * 60,
            "",
        ]
    lines += [
        f"Dear {tenant_name or 'resident'},",
        "",
        f"Your car has been plugged in to {where} since "
        f"{_local_time(plugged_in_since, now)}, but it has not started charging.",
        "",
        _HINTS.get(status, ""),
        "",
        "If you have set the car to charge later, for example on a timer for "
        "cheaper overnight electricity, you can ignore this message. You will "
        "only get one reminder each time you plug in.",
        "",
        f"This is an automated message sent on behalf of {landlord_name}.",
    ]
    if support_email:
        lines.append(f"If you have any questions, please contact {support_email}.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = ", ".join(to)
    if reply_to or support_email:
        msg["Reply-To"] = reply_to or support_email
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    msg.set_content("\n".join(lines) + "\n")
    return msg


# ------------------------------------------------------------------ state file

def load_state(path: Path) -> dict:
    """Per-charger records. A missing or unreadable file starts afresh."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ main flow

def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="charge_reminder",
        description="Email the tenant if their car is plugged in but not charging.",
    )
    parser.add_argument(
        "--email", action="store_true",
        help="send the reminder; without this, only report what would happen",
    )
    parser.add_argument(
        "--after-hours", type=float, default=1.0,
        help="remind once the car has waited longer than this (default 1)",
    )
    parser.add_argument(
        "--state", type=Path, default=None,
        help="state file remembering reminders sent (default: reminder_state.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Shared start-up with the statement tool: .env, settings, households.toml.
    from ocpp_garage.statement_cli import (
        StatementError,
        _bootstrap_env,
        _load_settings,
        _redact,
        app_dir,
        load_household,
    )

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    base = app_dir()
    state_path = args.state or base / "reminder_state.json"
    threshold = timedelta(hours=args.after_hours)

    try:
        _bootstrap_env(base)
        settings = _load_settings()
        site, household = load_household(base, settings)
        to, intended, test_mode = _recipients(settings, household) if args.email else ([], [], False)

        from ocpp_garage.adapters.easee.adapter import EaseeAdapter
        from ocpp_garage.adapters.easee.client import EaseeApiError, EaseeAuthError, EaseeClient

        serial = settings.easee_charger_serial
        now = datetime.now(timezone.utc)
        try:
            with EaseeClient(settings.easee_username, settings.easee_password) as client:
                adapter = EaseeAdapter(client, [serial])
                charger = adapter.get_charger_state(serial)
                # The session endpoint is rate limited; only ask when it matters.
                session = (
                    adapter.get_connected_session(serial) if charger.status in WAITING else None
                )
        except EaseeAuthError:
            raise ReminderError("Easee rejected the login. Check EASEE_USERNAME and EASEE_PASSWORD.")
        except EaseeApiError as exc:
            raise ReminderError(f"Easee returned an error: {exc}")
        except OSError as exc:
            raise ReminderError(f"Could not reach Easee: {exc}")

        state = load_state(state_path)
        result = assess(charger, session, state.get(serial), now, threshold)
        print(f"Charger {serial} ({household.bay or 'no bay'}): {result.reason}")

        if result.record is None:
            state.pop(serial, None)
        else:
            state[serial] = result.record

        if result.remind:
            if not args.email:
                print("  A reminder is due (not sent: run with --email to send it).")
            elif not to:
                print("  A reminder is due, but no tenant_email is set for this charger.")
            else:
                assert result.plugged_in_since is not None
                _send(
                    build_reminder(
                        status=charger.status,
                        plugged_in_since=result.plugged_in_since,
                        now=now,
                        tenant_name=household.tenant_name,
                        bay=household.bay,
                        to=to,
                        intended=intended,
                        test_mode=test_mode,
                        sender=settings.email_from or settings.smtp_username,
                        sender_name=settings.email_from_name,
                        landlord_name=site.landlord_name,
                        support_email=site.support_email,
                        reply_to=settings.email_reply_to,
                    ),
                    settings,
                )
                state[serial] = {**state[serial], "reminded": True}
                print(f"  Reminder sent to {_redact(', '.join(to))}"
                      + (" (TEST MODE - test address only)" if test_mode else ""))

        save_state(state_path, state)
    except (ReminderError, StatementError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def _recipients(settings, household) -> tuple[list[str], list[str], bool]:
    """(send to, would be sent to, test mode). Checked before contacting Easee."""
    from ocpp_garage.billing.mailer import MailerError, split_addresses

    missing = [
        name for name, value in (
            ("SMTP_HOST", settings.smtp_host),
            ("SMTP_USERNAME", settings.smtp_username),
            ("SMTP_PASSWORD", settings.smtp_password),
        ) if not value
    ]
    if missing:
        raise ReminderError(f"Cannot send email - missing settings: {', '.join(missing)}")
    try:
        intended = split_addresses(household.tenant_email)
        if settings.statement_test_recipient:
            test = split_addresses(settings.statement_test_recipient)
            if len(test) != 1:
                raise ReminderError("STATEMENT_TEST_RECIPIENT must be a single address.")
            return test, intended, True
    except MailerError as exc:
        raise ReminderError(str(exc))
    return intended, intended, False


def _send(msg: EmailMessage, settings) -> None:
    from ocpp_garage.billing.mailer import MailerError, send

    try:
        send(
            [msg],
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            security=settings.smtp_security,
        )
    except MailerError as exc:
        raise ReminderError(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
