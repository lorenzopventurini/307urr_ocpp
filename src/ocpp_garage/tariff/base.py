"""Tariff provider interface. Swap implementations without touching the billing engine."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PricePoint:
    interval_start_utc: datetime   # start of the pricing interval
    interval_minutes: int          # length of the interval (60 for hourly)
    pence_per_kwh: float           # VAT-inclusive


class TariffProvider(ABC):
    @abstractmethod
    def get_price(self, interval_start_utc: datetime) -> PricePoint:
        """Return the price for the interval that starts at interval_start_utc."""
        ...

    @abstractmethod
    def get_prices(
        self,
        period_start_utc: datetime,
        period_end_utc: datetime,
    ) -> list[PricePoint]:
        """Return all price points covering [period_start_utc, period_end_utc)."""
        ...
