"""
Email delivery for statements. Plain SMTP via the standard library, so any
provider works (Gmail app password, Brevo, Resend, ...) by changing settings.

The tenant and the management company each get their own email, worded for
them, rather than one message with the other in Cc: a shared message can only
have one greeting.

Deliverability is mostly decided by the sending account, not this code, but the
messages are built to give spam filters nothing to object to: a plain-text body,
no links or HTML, a real Date and Message-ID, and a single PDF attachment.
"""

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from .models import Bill

TENANT = "tenant"
LANDLORD = "landlord"


class MailerError(Exception):
    """Configuration or delivery failure, with a message fit for the operator."""


@dataclass(frozen=True)
class Delivery:
    """One email: who it is worded for, and where it actually goes."""
    role: str               # TENANT or LANDLORD
    to: list[str]           # actual recipients
    intended: list[str]     # real recipients; differs from `to` only in test mode
    test_mode: bool


def _split(addresses: str) -> list[str]:
    """'a@x.com, b@y.com' -> ['a@x.com', 'b@y.com'], rejecting anything malformed."""
    result = []
    for raw in addresses.split(","):
        raw = raw.strip()
        if not raw:
            continue
        _, addr = parseaddr(raw)
        if "@" not in addr or " " in addr:
            raise MailerError(f"{raw!r} is not a valid email address.")
        result.append(addr)
    return result


def plan_deliveries(
    tenant_email: str,
    landlord_email: str,
    test_recipient: str = "",
) -> list[Delivery]:
    """
    The tenant gets their statement; the management company gets a copy for
    its records. Either may be unset.

    When test_recipient is set, every email goes there instead and nothing
    reaches the real recipients. Test mode always produces both versions, so
    each can be checked before going live, even with no addresses configured.
    """
    tenant = _split(tenant_email)
    landlord = _split(landlord_email)

    if test_recipient:
        test = _split(test_recipient)
        if len(test) != 1:
            raise MailerError("STATEMENT_TEST_RECIPIENT must be a single address.")
        return [
            Delivery(TENANT, to=test, intended=tenant, test_mode=True),
            Delivery(LANDLORD, to=test, intended=landlord, test_mode=True),
        ]

    deliveries = []
    if tenant:
        deliveries.append(Delivery(TENANT, to=tenant, intended=tenant, test_mode=False))
    if landlord:
        deliveries.append(Delivery(LANDLORD, to=landlord, intended=landlord, test_mode=False))
    if not deliveries:
        raise MailerError(
            "No recipients configured. Set tenant_email for the charger and/or "
            "landlord_email under [site] in households.toml, or set "
            "STATEMENT_TEST_RECIPIENT to send a test."
        )
    return deliveries


def build_message(
    bill: Bill,
    pdf_bytes: bytes,
    pdf_filename: str,
    delivery: Delivery,
    sender: str,
    sender_name: str,
    bay: str = "",
    reply_to: str = "",
    support_email: str = "",
) -> EmailMessage:
    msg = EmailMessage()

    subject = f"EV charging statement - {bill.period_label}"
    if bay:
        subject += f" - {bay}"
    if delivery.role == LANDLORD:
        subject += " (copy)"
    if delivery.test_mode:
        subject = f"[TEST] {subject}"

    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = ", ".join(delivery.to)
    # Replies should reach someone who can answer them, not the unattended
    # sending mailbox.
    if reply_to or support_email:
        msg["Reply-To"] = reply_to or support_email
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)

    msg.set_content(_body(bill, delivery, bay, support_email))
    msg.add_attachment(
        pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename
    )
    return msg


def _body(bill: Bill, delivery: Delivery, bay: str, support_email: str) -> str:
    lines: list[str] = []

    if delivery.test_mode:
        who = "management company copy" if delivery.role == LANDLORD else "tenant statement"
        lines += [
            f"*** TEST MESSAGE ({who}) ***",
            "In live use this email would be sent to:",
            f"  {', '.join(delivery.intended) or '(no address configured yet)'}",
            "",
            "-" * 60,
            "",
        ]

    where = f"charger {bill.charger_id}" + (f", {bay}" if bay else "")
    if delivery.role == LANDLORD:
        lines += [
            f"Dear {bill.landlord_name},",
            "",
            f"Please find attached a copy of the EV charging statement for "
            f"{bill.period_label} issued to {bill.household_name} ({where}).",
        ]
    else:
        lines += [
            f"Dear {bill.household_name},",
            "",
            f"Please find attached your EV charging statement for "
            f"{bill.period_label} ({where}).",
        ]

    lines += ["", f"  Energy used : {bill.total_kwh:.2f} kWh"]

    rates = {line.pence_per_kwh for line in bill.lines}
    if len(rates) == 1:
        lines.append(f"  Rate        : {rates.pop():.1f}p per kWh (including VAT)")

    lines.append(f"  Total       : £{bill.total_cost_pounds:.2f}")

    if not bill.lines:
        lines += ["", "No charging was recorded in this period, so nothing is due."]

    lines.append("")
    if delivery.role == LANDLORD:
        lines.append("This is an automated copy for your records.")
    else:
        lines.append(f"This is an automated message sent on behalf of {bill.landlord_name}.")

    contact = support_email or bill.landlord_name
    lines.append(f"If you have any questions about this statement, please contact {contact}.")

    return "\n".join(lines) + "\n"


def send(
    messages: list[EmailMessage],
    host: str,
    port: int,
    username: str,
    password: str,
    security: str = "starttls",
) -> None:
    """
    Deliver all messages over one encrypted connection. There is deliberately
    no plaintext option: the SMTP password would cross the network in the clear.
    """
    if security not in ("starttls", "ssl"):
        raise MailerError(f"SMTP_SECURITY must be 'starttls' or 'ssl', not {security!r}.")

    context = ssl.create_default_context()
    sent = 0
    try:
        if security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)

        with server:
            if security == "starttls":
                server.starttls(context=context)
            server.login(username, password)
            for msg in messages:
                server.send_message(msg)
                sent += 1
    except smtplib.SMTPAuthenticationError:
        raise MailerError(
            "The mail server rejected the login. Check SMTP_USERNAME and SMTP_PASSWORD "
            "(for Gmail this must be an app password, not the account password)."
        )
    except (smtplib.SMTPException, OSError) as exc:
        detail = (
            f" ({sent} of {len(messages)} emails had already been sent)" if sent else ""
        )
        raise MailerError(f"Could not send email via {host}:{port} - {exc}{detail}")
