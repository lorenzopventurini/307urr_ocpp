"""Flat-rate tariff provider. Returns a constant price for every interval."""

from datetime import datetime, timedelta, timezone

from .base import PricePoint, TariffProvider


class FlatRateTariffProvider(TariffProvider):
    def __init__(self, pence_per_kwh: float, interval_minutes: int = 60) -> None:
        self._pence = pence_per_kwh
        self._interval_minutes = interval_minutes

    def get_price(self, interval_start_utc: datetime) -> PricePoint:
        return PricePoint(
            interval_start_utc=interval_start_utc,
            interval_minutes=self._interval_minutes,
            pence_per_kwh=self._pence,
        )

    def get_prices(
        self,
        period_start_utc: datetime,
        period_end_utc: datetime,
    ) -> list[PricePoint]:
        prices: list[PricePoint] = []
        current = period_start_utc.replace(minute=0, second=0, microsecond=0)
        step = timedelta(minutes=self._interval_minutes)
        while current < period_end_utc:
            prices.append(self.get_price(current))
            current += step
        return prices
