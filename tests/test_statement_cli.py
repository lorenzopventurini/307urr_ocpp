from datetime import date
from types import SimpleNamespace

import pytest

from ocpp_garage import statement_cli as cli
from ocpp_garage.statement_cli import StatementError


@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2026, 10, 2), (2026, 9)),
        (date(2026, 10, 31), (2026, 9)),
        (date(2027, 1, 2), (2026, 12)),   # year boundary
        (date(2028, 3, 1), (2028, 2)),    # leap year
    ],
)
def test_previous_month(today, expected):
    assert cli.previous_month(today) == expected


def test_previous_month_and_explicit_period_conflict():
    args = cli._parse_args(["2026-07", "--previous-month"])
    with pytest.raises(StatementError, match="not both"):
        cli._resolve_period(args)


@pytest.mark.parametrize("argv", [["2026-07"], ["2026", "7"], ["2026/07"]])
def test_period_forms(argv):
    assert cli._resolve_period(cli._parse_args(argv)) == (2026, 7)


def _settings(**overrides):
    base = dict(
        smtp_host="smtp.example.com", smtp_username="u@example.com", smtp_password="pw",
        statement_test_recipient="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


SITE = SimpleNamespace(landlord_email="mgmt@example.com")
HOUSEHOLD = SimpleNamespace(tenant_email="tenant@example.com")


def test_email_misconfiguration_fails_before_any_work():
    with pytest.raises(StatementError, match="SMTP_HOST, SMTP_PASSWORD"):
        cli.prepare_email(_settings(smtp_host="", smtp_password=""), SITE, HOUSEHOLD)


def test_prepare_email_honours_test_recipient():
    deliveries = cli.prepare_email(
        _settings(statement_test_recipient="me@example.com"), SITE, HOUSEHOLD
    )
    assert all(d.to == ["me@example.com"] and d.test_mode for d in deliveries)


def test_names_redacted_only_in_ci(monkeypatch):
    monkeypatch.setattr(cli, "IN_CI", True)
    assert cli._redact("Jane Smith") == "(redacted)"
    monkeypatch.setattr(cli, "IN_CI", False)
    assert cli._redact("Jane Smith") == "Jane Smith"
