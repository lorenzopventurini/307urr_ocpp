from datetime import datetime, timezone

import pytest

from ocpp_garage.billing import mailer
from ocpp_garage.billing.mailer import (
    LANDLORD,
    TENANT,
    MailerError,
    build_message,
    plan_deliveries,
    send,
)
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


def _body(msg) -> str:
    return msg.get_body(preferencelist=("plain",)).get_content()


def _message(delivery, bill=None, **kw):
    kw.setdefault("support_email", "help@example.com")
    return build_message(
        bill or _bill(), b"%PDF-fake", "ECL28GR3_2026_07.pdf", delivery,
        sender="statements@example.com", sender_name="EV Statements", bay="Bay 10", **kw,
    )


# ------------------------------------------------------------------ deliveries

def test_tenant_and_management_get_separate_emails():
    tenant, landlord = plan_deliveries("tenant@example.com", "a@mgmt.com, b@mgmt.com")
    assert (tenant.role, tenant.to) == (TENANT, ["tenant@example.com"])
    assert (landlord.role, landlord.to) == (LANDLORD, ["a@mgmt.com", "b@mgmt.com"])
    assert not tenant.test_mode and not landlord.test_mode


def test_only_configured_recipients_get_email():
    [only] = plan_deliveries("", "mgmt@example.com")
    assert only.role == LANDLORD      # worded as the management copy, not to the tenant
    [only] = plan_deliveries("tenant@example.com", "")
    assert only.role == TENANT


def test_test_recipient_overrides_everyone():
    deliveries = plan_deliveries("tenant@example.com", "mgmt@example.com", "me@example.com")
    assert [d.to for d in deliveries] == [["me@example.com"], ["me@example.com"]]
    assert all(d.test_mode for d in deliveries)
    assert [d.intended for d in deliveries] == [["tenant@example.com"], ["mgmt@example.com"]]


def test_test_mode_previews_both_versions_before_addresses_exist():
    deliveries = plan_deliveries("", "", "me@example.com")
    assert [d.role for d in deliveries] == [TENANT, LANDLORD]
    assert all(d.intended == [] for d in deliveries)


def test_no_recipients_is_an_error():
    with pytest.raises(MailerError, match="No recipients"):
        plan_deliveries("", "")


@pytest.mark.parametrize("bad", ["not-an-address", "two words@example.com"])
def test_malformed_address_rejected(bad):
    with pytest.raises(MailerError, match="not a valid email"):
        plan_deliveries(bad, "")


def test_test_recipient_must_be_single():
    with pytest.raises(MailerError, match="single address"):
        plan_deliveries("t@example.com", "", "a@example.com, b@example.com")


# ------------------------------------------------------------------ message

def test_tenant_email():
    [tenant, _] = plan_deliveries("tenant@example.com", "mgmt@example.com")
    msg = _message(tenant)

    assert msg["Subject"] == "EV charging statement - July 2026 - Bay 10"
    assert msg["From"] == "EV Statements <statements@example.com>"
    assert msg["To"] == "tenant@example.com"
    assert msg["Cc"] is None
    assert msg["Date"] and msg["Message-ID"].endswith("@example.com>")

    body = _body(msg)
    assert body.startswith("Dear Jane Smith,")
    assert "your EV charging statement for July 2026" in body
    assert "14.50 kWh" in body
    assert "25.0p per kWh" in body
    assert "£3.62" in body          # 14.5 kWh x 25p = 362.5p
    assert "sent on behalf of Test Management" in body
    assert "TEST" not in body

    [attachment] = list(msg.iter_attachments())
    assert attachment.get_filename() == "ECL28GR3_2026_07.pdf"
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_content() == b"%PDF-fake"


def test_management_copy_is_addressed_to_management():
    [_, landlord] = plan_deliveries("tenant@example.com", "mgmt@example.com")
    msg = _message(landlord)

    assert msg["Subject"] == "EV charging statement - July 2026 - Bay 10 (copy)"
    assert msg["To"] == "mgmt@example.com"
    body = _body(msg)
    assert body.startswith("Dear Test Management,")
    assert "Dear Jane Smith" not in body
    assert "a copy of the EV charging statement for July 2026 issued to Jane Smith" in body
    assert "automated copy for your records" in body
    assert len(list(msg.iter_attachments())) == 1


def test_questions_go_to_support_email_and_there_is_no_signature():
    [tenant] = plan_deliveries("tenant@example.com", "")
    body = _body(_message(tenant, support_email="help@example.com"))
    assert body.rstrip().endswith(
        "If you have any questions about this statement, please contact help@example.com."
    )
    assert "Somewhere, London" not in body      # landlord address signature removed


def test_without_support_email_questions_go_to_landlord():
    [tenant] = plan_deliveries("tenant@example.com", "")
    body = _body(_message(tenant, support_email=""))
    assert "please contact Test Management." in body


def test_reply_to_is_support_email_unless_overridden():
    [tenant] = plan_deliveries("tenant@example.com", "")
    assert _message(tenant, support_email="help@example.com")["Reply-To"] == "help@example.com"
    msg = _message(tenant, support_email="help@example.com", reply_to="other@example.com")
    assert msg["Reply-To"] == "other@example.com"
    assert _message(tenant, support_email="")["Reply-To"] is None


def test_test_emails_are_labelled_with_role_and_intended_recipient():
    tenant, landlord = plan_deliveries("tenant@example.com", "mgmt@example.com", "me@example.com")

    t = _message(tenant)
    assert t["Subject"].startswith("[TEST] ")
    assert t["To"] == "me@example.com"
    assert "TEST MESSAGE (tenant statement)" in _body(t)
    assert "  tenant@example.com" in _body(t)

    m = _message(landlord)
    assert m["Subject"] == "[TEST] EV charging statement - July 2026 - Bay 10 (copy)"
    assert m["To"] == "me@example.com"
    assert "TEST MESSAGE (management company copy)" in _body(m)
    assert "  mgmt@example.com" in _body(m)


def test_zero_usage_month_says_nothing_is_due():
    [tenant] = plan_deliveries("t@example.com", "")
    body = _body(_message(tenant, bill=_bill(lines=[])))
    assert "£0.00" in body
    assert "nothing is due" in body
    assert "per kWh" not in body     # no rate to quote without any lines


# ------------------------------------------------------------------ transport

class _FakeSMTP:
    instances: list["_FakeSMTP"] = []
    fail_on_send: int | None = None

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
        if self.calls.count("send") == _FakeSMTP.fail_on_send:
            raise mailer.smtplib.SMTPDataError(554, b"rejected")
        self.calls.append("send")


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances.clear()
    _FakeSMTP.fail_on_send = None
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def test_starttls_happens_before_the_password_is_sent(fake_smtp):
    send([object(), object()], "smtp.example.com", 587, "u", "p", security="starttls")
    [conn] = fake_smtp.instances        # both messages over one connection
    assert conn.calls == ["starttls", "login", "send", "send"]


def test_implicit_ssl_needs_no_starttls(fake_smtp):
    send([object()], "smtp.example.com", 465, "u", "p", security="ssl")
    assert fake_smtp.instances[0].calls == ["login", "send"]


def test_plaintext_smtp_is_refused(fake_smtp):
    with pytest.raises(MailerError, match="starttls' or 'ssl'"):
        send([object()], "smtp.example.com", 25, "u", "p", security="none")
    assert fake_smtp.instances == []


def test_partial_failure_reports_what_was_already_sent(fake_smtp):
    fake_smtp.fail_on_send = 1          # first email goes, second is rejected
    with pytest.raises(MailerError, match="1 of 2 emails had already been sent"):
        send([object(), object()], "smtp.example.com", 587, "u", "p")
