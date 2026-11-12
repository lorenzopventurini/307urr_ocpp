"""Vendor-neutral adapter interface. All charger backends implement this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ChargerStatus(str, Enum):
    AVAILABLE = "available"                 # no car connected
    AWAITING_START = "awaiting_start"       # car connected; charger holding (DLM, schedule)
    AWAITING_AUTHENTICATION = "awaiting_authentication"  # car connected; needs authorising
    CHARGING = "charging"
    READY_TO_CHARGE = "ready_to_charge"     # car connected; charger offering, car not taking
    COMPLETED = "completed"                 # car connected; car paused or stopped charging
    DEAUTHENTICATING = "deauthenticating"
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
    # True when the charger itself is deliberately holding off: its own charging
    # schedule, or the car has finished and is parked in a "fully charged" queue.
    held_by_charger_schedule: bool = False
    status_since_utc: datetime | None = None   # when the status last changed, if known


@dataclass(frozen=True)
class ConnectedSession:
    """The session for the car currently plugged in."""
    charger_id: str
    session_key: str | None           # stable per plug-in; None if the backend has none
    started_utc: datetime | None      # when the car was plugged in, if known
    energy_kwh: float                 # delivered so far in this session


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
    def get_connected_session(self, charger_id: str) -> ConnectedSession | None:
        """The session for the car plugged in now, or None if there is none."""
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
