"""Vendor-neutral adapter interface. All charger backends implement this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ChargerStatus(str, Enum):
    AVAILABLE = "available"
    CHARGING = "charging"
    SUSPENDED = "suspended"   # paused by DLM
    OFFLINE = "offline"
    FAULTED = "faulted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MeterReading:
    """A point-in-time cumulative energy register read from a charger."""
    charger_id: str             # vendor serial / OCPP chargePointId
    timestamp_utc: datetime     # UTC
    cumulative_kwh: float       # monotonically increasing lifetime register
    source: str                 # "easee_api" | "ocpp_clock_aligned" | "ocpp_sample"


@dataclass(frozen=True)
class SessionEvent:
    charger_id: str
    session_id: str
    event: str                  # "start" | "stop"
    timestamp_utc: datetime
    id_tag: str | None          # RFID / Easee user token (None if not available)
    energy_kwh: float | None    # session total on stop; None on start


@dataclass(frozen=True)
class ChargerState:
    charger_id: str
    timestamp_utc: datetime
    status: ChargerStatus
    current_amps: float | None   # present draw (per-phase average)
    power_kw: float | None


class ChargerAdapter(ABC):
    """
    Implement this for each vendor/protocol backend.
    The billing engine and DLM controller only see this interface.
    """

    @abstractmethod
    def get_charger_state(self, charger_id: str) -> ChargerState:
        """Fetch the current state of a charger."""
        ...

    @abstractmethod
    def get_meter_reading(self, charger_id: str) -> MeterReading:
        """Read the cumulative energy register right now."""
        ...

    @abstractmethod
    def set_current_limit(self, charger_id: str, limit_amps: float) -> None:
        """
        Instruct the charger to draw no more than limit_amps per phase.
        Must be idempotent — safe to call repeatedly with the same value.
        """
        ...

    @abstractmethod
    def list_chargers(self) -> list[str]:
        """Return all charger IDs managed by this adapter."""
        ...
