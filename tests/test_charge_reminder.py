from datetime import datetime, timedelta, timezone

import pytest

from ocpp_garage.adapters.base import ChargerState, ChargerStatus, ConnectedSession
from ocpp_garage.adapters.easee.adapter import _STATUS_MAP, EaseeAdapter
from ocpp_garage.charge_reminder import assess, build_reminder

NOW = datetime(2026, 11, 12, 15, 30, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _state(status, held=False):
    return ChargerState("ECL28GR3", NOW, status, None, None, held_by_charger_schedule=held)


def _session(started=NOW - 2 * HOUR, energy=0.0, key="123"):
    return ConnectedSession("ECL28GR3", key, started, energy)


# ------------------------------------------------------------------ Easee mapping

@pytest.mark.parametrize(
    "op_mode, status",
    [
        (0, ChargerStatus.OFFLINE),
        (1, ChargerStatus.AVAILABLE),
        (2, ChargerStatus.AWAITING_START),
        (3, ChargerStatus.CHARGING),
        (4, ChargerStatus.COMPLETED),
        (5, ChargerStatus.FAULTED),
        (6, ChargerStatus.READY_TO_CHARGE),
        (7, ChargerStatus.AWAITING_AUTHENTICATION),
        (8, ChargerStatus.DEAUTHENTICATING),
    ],
)
def test_easee_op_modes(op_mode, status):
    assert _STATUS_MAP[op_mode] is status


class _FakeClient:
    def __init__(self, state=None, session=None):
        self._state, self._session = state or {}, session

    def get_charger_state(self, serial):
        return self._state

    def get_ongoing_session(self, serial):
        return self._session


@pytest.mark.parametrize("reason, held", [(54, True), (76, True), (6, True), (0, False), (None, False)])
def test_schedule_hold_detected(reason, held):
    adapter = EaseeAdapter(_FakeClient({"chargerOpMode": 2, "reasonForNoCurrent": reason}), [])
    assert adapter.get_charger_state("X").held_by_charger_schedule is held


def test_state_read_from_observations():
    class Client(_FakeClient):
        def get_observations(self, serial, ids):
            return [
                {"id": 109, "value": 6, "timestamp": "2026-11-12T13:05:00.000Z", "dataType": 4},
                {"id": 96, "value": 79, "timestamp": "2026-11-12T13:05:00.000Z", "dataType": 4},
            ]

    from ocpp_garage.adapters.easee.client import EaseeClient

    client = Client()
    client.get_charger_state = lambda serial: EaseeClient.get_charger_state(client, serial)
    state = EaseeAdapter(client, []).get_charger_state("X")
    assert state.status is ChargerStatus.READY_TO_CHARGE
    assert not state.held_by_charger_schedule
    assert state.status_since_utc == datetime(2026, 11, 12, 13, 5, tzinfo=timezone.utc)


def test_ongoing_session_parsed():
    adapter = EaseeAdapter(_FakeClient(session={
        "sessionId": 42, "sessionStart": "2026-11-12T13:05:00Z", "sessionEnergy": 0.0,
    }), [])
    s = adapter.get_connected_session("X")
    assert s.session_key == "42"
    assert s.started_utc == datetime(2026, 11, 12, 13, 5, tzinfo=timezone.utc)
    assert EaseeAdapter(_FakeClient(session=None), []).get_connected_session("X") is None


# ------------------------------------------------------------------ decision

@pytest.mark.parametrize("status", sorted(
    {ChargerStatus.AWAITING_START, ChargerStatus.READY_TO_CHARGE,
     ChargerStatus.AWAITING_AUTHENTICATION}, key=lambda s: s.value))
def test_reminds_for_waiting_states(status):
    result = assess(_state(status), _session(), None, NOW, HOUR)
    assert result.remind
    assert result.plugged_in_since == NOW - 2 * HOUR


@pytest.mark.parametrize("status", [
    ChargerStatus.COMPLETED, ChargerStatus.CHARGING, ChargerStatus.AVAILABLE,
    ChargerStatus.OFFLINE, ChargerStatus.FAULTED, ChargerStatus.DEAUTHENTICATING,
])
def test_no_reminder_otherwise(status):
    result = assess(_state(status), _session(), {"session": "123"}, NOW, HOUR)
    assert not result.remind and result.record is None


def test_finished_car_in_waiting_state_is_left_alone():
    result = assess(_state(ChargerStatus.READY_TO_CHARGE), _session(energy=12.4), None, NOW, HOUR)
    assert not result.remind


def test_charger_schedule_is_left_alone():
    result = assess(_state(ChargerStatus.AWAITING_START, held=True), _session(), None, NOW, HOUR)
    assert not result.remind


def test_not_before_threshold():
    result = assess(
        _state(ChargerStatus.AWAITING_START), _session(started=NOW - timedelta(minutes=50)),
        None, NOW, HOUR,
    )
    assert not result.remind and result.record is not None


def test_only_one_reminder_per_session():
    first = assess(_state(ChargerStatus.AWAITING_START), _session(), None, NOW, HOUR)
    sent = {**first.record, "reminded": True}
    later = assess(_state(ChargerStatus.AWAITING_START), _session(), sent, NOW + HOUR, HOUR)
    assert not later.remind
    replugged = assess(
        _state(ChargerStatus.AWAITING_START), _session(key="124", started=NOW),
        sent, NOW + 2 * HOUR, HOUR,
    )
    assert replugged.remind


def test_without_session_falls_back_to_status_change_time():
    state = ChargerState("ECL28GR3", NOW, ChargerStatus.AWAITING_START, None, None,
                         status_since_utc=NOW - 3 * HOUR)
    result = assess(state, None, None, NOW, HOUR)
    assert result.remind and result.plugged_in_since == NOW - 3 * HOUR


def test_without_session_info_waits_from_first_sighting():
    state = _state(ChargerStatus.AWAITING_START)
    first = assess(state, None, None, NOW, HOUR)
    assert not first.remind
    assert first.plugged_in_since == NOW
    second = assess(state, None, first.record, NOW + HOUR, HOUR)
    assert second.remind
    # An overnight gap: cannot tell it is the same plug-in, so start again.
    sent = {**second.record, "reminded": True}
    next_day = assess(state, None, sent, NOW + 20 * HOUR, HOUR)
    assert not next_day.remind and next_day.plugged_in_since == NOW + 20 * HOUR


# ------------------------------------------------------------------ email

def _reminder(**kw):
    args = dict(
        status=ChargerStatus.AWAITING_AUTHENTICATION,
        plugged_in_since=datetime(2026, 7, 3, 12, 5, tzinfo=timezone.utc),
        now=datetime(2026, 7, 3, 14, 40, tzinfo=timezone.utc),
        tenant_name="Jane Smith", bay="Bay 10",
        to=["tenant@example.com"], intended=["tenant@example.com"], test_mode=False,
        sender="ev@example.com", sender_name="EV Charging",
        landlord_name="Test Management", support_email="help@example.com",
    )
    args.update(kw)
    return build_reminder(**args)


def test_reminder_email():
    msg = _reminder()
    body = msg.get_content()
    assert msg["Subject"] == "Your car is plugged in but not charging - Bay 10"
    assert msg["To"] == "tenant@example.com"
    assert msg["Reply-To"] == "help@example.com"
    assert "Dear Jane Smith" in body
    assert "since 13:05" in body          # London summer time, not UTC
    assert "authorised" in body
    assert "help@example.com" in body


def test_reminder_test_mode():
    msg = _reminder(to=["me@example.com"], test_mode=True)
    assert msg["Subject"].startswith("[TEST] ")
    assert msg["To"] == "me@example.com"
    assert "would be sent to:\n  tenant@example.com" in msg.get_content()


def test_reminder_names_the_day_when_not_today():
    body = _reminder(now=datetime(2026, 7, 4, 14, 0, tzinfo=timezone.utc)).get_content()
    assert "since 13:05 yesterday" in body
