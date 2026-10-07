from datetime import datetime, timezone

import pytest

from ocpp_garage.billing import mailer
from ocpp_garage.billing.mailer import MailerError, build_message, resolve_recipients, send
from ocpp_garage.billing.models import Bill, BillLine


def _bill(lines=None) -> Bill:
    if lines is None:
        lines = [
            BillLine(datetime(2026, 7, 3, 20, tzinfo=timezone.utc), kwh=10.0, pence_per_kwh=25.0),
            BillLine(datetime(2026, 7, 3, 21, tzinfo=timezone.utc), kwh=4.5, pence_per_kwh=25.0),
        ]
    return Bill(
        household_name="Jane Smith",
        household_address="Flat 10\n307 Upper Richmond Rd",
        charger_id="ECL28GR3",
        period_year=2026,
        period_month=7,
        lines=lines,
        landlord_name="Test Management",
        landlord_address="Somewhere, London",
    )


# ------------------------------------------------------------------ recipients

def test_tenant_receives_and_management_is_copied():
    r = resolve_recipients("tenant@example.com", "mgmt@example.com")
    assert r.to == ["tenant@example.com"]
    assert r.cc == ["mgmt@example.com"]
    assert not r.test_mode


def test_test_recipient_overrides_everyone():
    r = resolve_recipients("tenant@example.com", "a@mgmt.com, b@mgmt.com", "me@example.com")
    assert r.to == ["me@example.com"]
    assert r.cc == []
    assert r.test_mode
    assert r.intended_to == ["tenant@example.com"]
    assert r.intended_cc == ["a@mgmt.com", "b@mgmt.com"]


def test_test_mode_works_before_real_addresses_exist():
    r = resolve_recipients("", "", "me@example.com")
    assert r.to == ["me@example.com"]
    assert r.intended_to == [] and r.intended_cc == []


def test_management_promoted_when_no_tenant_email():
    r = resolve_recipients("", "mgmt@example.com")
    assert r.to == ["mgmt@example.com"]
    assert r.cc == []


def test_no_recipients_is_an_error():
    with pytest.raises(MailerError, match="No recipients"):
        resolve_recipients("", "")


@pytest.mark.parametrize("bad", ["not-an-address", "two words@example.com"])
def test_malformed_address_rejected(bad):
    with pytest.raises(MailerError, match="not a valid email"):
        resolve_recipients(bad, "")


def test_test_recipient_must_be_single():
    with pytest.raises(MailerError, match="single address"):
        resolve_recipients("t@example.com", "", "a@example.com, b@example.com")


# ------------------------------------------------------------------ message

def test_live_message_headers_body_and_attachment():
    r = resolve_recipients("tenant@example.com", "mgmt@example.com")
    msg = build_message(
        _bill(), b"%PDF-fake", "ECL28GR3_2026_07.pdf", r,
        sender="statements@example.com", sender_name="EV Statements", bay="Bay 10",
        reply_to="mgmt@example.com",
    )

    assert msg["Subject"] == "EV charging statement - July 2026 - Bay 10"
    assert msg["From"] == "EV Statements <statements@example.com>"
    assert msg["To"] == "tenant@example.com"
    assert msg["Cc"] == "mgmt@example.com"
    assert msg["Reply-To"] == "mgmt@example.com"
    assert msg["Date"] and msg["Message-ID"].endswith("@example.com>")

    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Dear Jane Smith," in body
    assert "14.50 kWh" in body
    assert "25.0p per kWh" in body
    assert "£3.62" in body          # 14.5 kWh x 25p = 362.5p
    assert "TEST" not in body

    [attachment] = list(msg.iter_attachments())
    assert attachment.get_filename() == "ECL28GR3_2026_07.pdf"
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_content() == b"%PDF-fake"


def test_test_message_is_labelled_and_names_intended_recipients():
    r = resolve_recipients("tenant@example.com", "mgmt@example.com", "me@example.com")
    msg = build_message(_bill(), b"%PDF", "s.pdf", r, "from@example.com", "EV")

    assert msg["Subject"].startswith("[TEST] ")
    assert msg["To"] == "me@example.com"
    assert msg["Cc"] is None
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "To: tenant@example.com" in body
    assert "Cc: mgmt@example.com" in body


def test_zero_usage_month_says_nothing_is_due():
    r = resolve_recipients("t@example.com", "")
    msg = build_message(_bill(lines=[]), b"%PDF", "s.pdf", r, "f@example.com", "EV")
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "£0.00" in body
    assert "nothing is due" in body
    assert "per kWh" not in body     # no rate to quote without any lines


# ------------------------------------------------------------------ transport

class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.calls = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append("login")

    def send_message(self, msg):
        self.calls.append("send")


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def test_starttls_happens_before_the_password_is_sent(fake_smtp):
    send(object(), "smtp.example.com", 587, "u", "p", security="starttls")
    assert fake_smtp.instances[0].calls == ["starttls", "login", "send"]


def test_implicit_ssl_needs_no_starttls(fake_smtp):
    send(object(), "smtp.example.com", 465, "u", "p", security="ssl")
    assert fake_smtp.instances[0].calls == ["login", "send"]


def test_plaintext_smtp_is_refused(fake_smtp):
    with pytest.raises(MailerError, match="starttls' or 'ssl'"):
        send(object(), "smtp.example.com", 25, "u", "p", security="none")
    assert fake_smtp.instances == []
