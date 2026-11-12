"""
Easee Cloud adapter — implements the vendor-neutral ChargerAdapter interface.
Translates Easee API responses into the shared domain types.
"""

from datetime import datetime, timezone

from ..base import (
    ChargerAdapter,
    ChargerState,
    ChargerStatus,
    ConnectedSession,
    MeterReading,
    SessionEvent,
)
from .client import EaseeClient


# Easee chargerOpMode values (enumeration 109), from
# https://developer.easee.com/docs/enumerations
_STATUS_MAP: dict[int, ChargerStatus] = {
    0: ChargerStatus.OFFLINE,                   # Offline
    1: ChargerStatus.AVAILABLE,                 # Disconnected: no car connected
    2: ChargerStatus.AWAITING_START,            # Awaiting Start (SuspendedEVSE)
    3: ChargerStatus.CHARGING,                  # Charging
    4: ChargerStatus.COMPLETED,                 # Completed: car paused or stopped charging
    5: ChargerStatus.FAULTED,                   # Error
    6: ChargerStatus.READY_TO_CHARGE,           # Ready to Charge (SuspendedEV)
    7: ChargerStatus.AWAITING_AUTHENTICATION,   # Awaiting Authentication
    8: ChargerStatus.DEAUTHENTICATING,          # De-authenticating
}

# reasonForNoCurrent values (enumeration 96) meaning the charger is holding off
# on purpose: 6 = finished, waiting in the "fully charged" queue;
# 54 = pending scheduled charging; 76 = current limited by schedule.
_HELD_BY_SCHEDULE = {6, 54, 76}


class EaseeAdapter(ChargerAdapter):
    """
    Wraps EaseeClient with the ChargerAdapter interface.
    One adapter instance manages one Easee account / set of chargers.
    """

    def __init__(self, client: EaseeClient, charger_serials: list[str]) -> None:
        self._client = client
        self._serials = charger_serials

    def list_chargers(self) -> list[str]:
        return list(self._serials)

    def get_charger_state(self, charger_id: str) -> ChargerState:
        raw = self._client.get_charger_state(charger_id)
        now = datetime.now(timezone.utc)

        easee_status = raw.get("chargerOpMode", 0)
        status = _STATUS_MAP.get(int(easee_status), ChargerStatus.UNKNOWN)

        # Easee reports per-phase amps; use phase 1 as representative
        current = _float_or_none(raw.get("outputCurrent"))
        power = _float_or_none(raw.get("totalPower"))

        return ChargerState(
            charger_id=charger_id,
            timestamp_utc=now,
            status=status,
            current_amps=current,
            power_kw=power,
            held_by_charger_schedule=_int_or_none(raw.get("reasonForNoCurrent"))
            in _HELD_BY_SCHEDULE,
            status_since_utc=_datetime_or_none(raw.get("chargerOpModeTimestamp")),
        )

    def get_connected_session(self, charger_id: str) -> ConnectedSession | None:
        raw = self._client.get_ongoing_session(charger_id)
        if not raw:
            return None
        session_id = raw.get("sessionId")
        started = _datetime_or_none(raw.get("sessionStart"))
        # Prefer Easee's session id; fall back to the start time, which is
        # equally unique per plug-in.
        if session_id is not None:
            key: str | None = str(session_id)
        else:
            key = started.isoformat() if started else None
        return ConnectedSession(
            charger_id=charger_id,
            session_key=key,
            started_utc=started,
            energy_kwh=_float_or_none(raw.get("sessionEnergy")) or 0.0,
        )

    def get_meter_reading(self, charger_id: str) -> MeterReading:
        raw = self._client.get_charger_state(charger_id)
        now = datetime.now(timezone.utc)
        lifetime_kwh = float(raw.get("lifetimeEnergy", 0.0))

        return MeterReading(
            charger_id=charger_id,
            timestamp_utc=now,
            cumulative_kwh=lifetime_kwh,
            source="easee_api",
        )

    def set_current_limit(self, charger_id: str, limit_amps: float) -> None:
        # Apply the same limit to all three phases (three-phase charger)
        self._client.set_dynamic_charger_current(
            charger_id,
            phase1=limit_amps,
            phase2=limit_amps,
            phase3=limit_amps,
        )


# --------------------------------------------------------------------------

def _float_or_none(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int_or_none(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _datetime_or_none(v: object) -> datetime | None:
    """Parse an Easee timestamp; one without a zone is taken to be UTC."""
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
