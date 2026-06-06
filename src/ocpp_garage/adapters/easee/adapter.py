"""
Easee Cloud adapter — implements the vendor-neutral ChargerAdapter interface.
Translates Easee API responses into the shared domain types.
"""

from datetime import datetime, timezone

from ..base import ChargerAdapter, ChargerState, ChargerStatus, MeterReading, SessionEvent
from .client import EaseeClient


# Easee observation keys that map to charger status
_STATUS_MAP: dict[int, ChargerStatus] = {
    1: ChargerStatus.OFFLINE,
    2: ChargerStatus.OFFLINE,
    3: ChargerStatus.AVAILABLE,
    4: ChargerStatus.CHARGING,
    5: ChargerStatus.CHARGING,
    6: ChargerStatus.SUSPENDED,     # charging paused / ready
    7: ChargerStatus.AVAILABLE,
    8: ChargerStatus.OFFLINE,
    9: ChargerStatus.FAULTED,
    10: ChargerStatus.OFFLINE,
}


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
