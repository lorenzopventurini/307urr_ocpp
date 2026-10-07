"""
Email delivery for statements. Plain SMTP via the standard library, so any
provider works (Gmail app password, Brevo, Resend, ...) by changing settings.

Deliverability is mostly decided by the sending account, not this code, but the
message is built to give spam filters nothing to object to: a plain-text body,
no links or HTML, a real Date and Message-ID, and a single PDF attachment.
"""

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from .models import Bill


class MailerError(Exception):
    """Configuration or delivery failure, with a message fit for the operator."""


@dataclass(frozen=True)
class Recipients:
    to: list[str]
    cc: list[str]
    # Who the statement is really for. Equal to to/cc in live use; in test mode
    # to/cc are redirected and these record where it would have gone.
    intended_to: list[str]
    intended_cc: list[str]
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


def resolve_recipients(
    tenant_email: str,
    landlord_email: str,
    test_recipient: str = "",
) -> Recipients:
    """
    The tenant receives the statement; the management company is copied in.

    When test_recipient is set, every message goes there instead and nothing
    reaches the real recipients. This check comes before anything else so a
    test configuration can never leak a statement to a tenant.
    """
    to = _split(tenant_email)
    cc = _split(landlord_email)
    if not to:
        # No tenant address yet: management becomes the primary recipient.
        to, cc = cc, []

    if test_recipient:
        test = _split(test_recipient)
        if len(test) != 1:
            raise MailerError("STATEMENT_TEST_RECIPIENT must be a single address.")
        return Recipients(to=test, cc=[], intended_to=to, intended_cc=cc, test_mode=True)

    if not to:
        raise MailerError(
            "No recipients configured. Set tenant_email for the charger and/or "
            "landlord_email under [site] in households.toml, or set "
            "STATEMENT_TEST_RECIPIENT to send a test."
        )
    return Recipients(to=to, cc=cc, intended_to=to, intended_cc=cc, test_mode=False)


def build_message(
    bill: Bill,
    pdf_bytes: bytes,
    pdf_filename: str,
    recipients: Recipients,
    sender: str,
    sender_name: str,
    bay: str = "",
    reply_to: str = "",
) -> EmailMessage:
    msg = EmailMessage()

    subject = f"EV charging statement - {bill.period_label}"
    if bay:
        subject += f" - {bay}"
    if recipients.test_mode:
        subject = f"[TEST] {subject}"

    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = ", ".join(recipients.to)
    if recipients.cc:
        msg["Cc"] = ", ".join(recipients.cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)

    msg.set_content(_body(bill, recipients, bay))
    msg.add_attachment(
        pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename
    )
    return msg


def _body(bill: Bill, recipients: Recipients, bay: str) -> str:
    lines: list[str] = []

    if recipients.test_mode:
        lines += [
            "*** TEST MESSAGE ***",
            "In live use this statement would be sent to:",
            f"  To: {', '.join(recipients.intended_to) or '(none configured yet)'}",
            f"  Cc: {', '.join(recipients.intended_cc) or '(none configured yet)'}",
            "",
            "-" * 60,
            "",
        ]

    where = f"charger {bill.charger_id}" + (f", {bay}" if bay else "")
    lines += [
        f"Dear {bill.household_name},",
        "",
        f"Please find attached your EV charging statement for {bill.period_label} ({where}).",
        "",
        f"  Energy used : {bill.total_kwh:.2f} kWh",
    ]

    rates = {line.pence_per_kwh for line in bill.lines}
    if len(rates) == 1:
        lines.append(f"  Rate        : {rates.pop():.1f}p per kWh (including VAT)")

    lines.append(f"  Total       : £{bill.total_cost_pounds:.2f}")

    if not bill.lines:
        lines += ["", "No charging was recorded in this period, so nothing is due."]

    lines += [
        "",
        f"This is an automated message sent on behalf of {bill.landlord_name}.",
        f"If you have any questions about this statement, please contact {bill.landlord_name}.",
        "",
        bill.landlord_name,
        bill.landlord_address,
    ]
    return "\n".join(lines) + "\n"


def send(
    msg: EmailMessage,
    host: str,
    port: int,
    username: str,
    password: str,
    security: str = "starttls",
) -> None:
    """
    Deliver over an encrypted connection only. There is deliberately no
    plaintext option: the SMTP password would cross the network in the clear.
    """
    context = ssl.create_default_context()
    try:
        if security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
        elif security == "starttls":
            server = smtplib.SMTP(host, port, timeout=30)
        else:
            raise MailerError(f"SMTP_SECURITY must be 'starttls' or 'ssl', not {security!r}.")

        with server:
            if security == "starttls":
                server.starttls(context=context)
            server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailerError(
            "The mail server rejected the login. Check SMTP_USERNAME and SMTP_PASSWORD "
            "(for Gmail this must be an app password, not the account password)."
        )
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailerError(f"The mail server refused the recipients: {list(exc.recipients)}")
    except (smtplib.SMTPException, OSError) as exc:
        raise MailerError(f"Could not send email via {host}:{port} - {exc}")
