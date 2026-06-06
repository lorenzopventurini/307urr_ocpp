"""In-memory billing data model. No database dependency — pure data classes."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BillLine:
    """One hourly (or other interval) line on the bill."""
    interval_start_utc: datetime
    kwh: float
    pence_per_kwh: float

    @property
    def cost_pence(self) -> float:
        return self.kwh * self.pence_per_kwh

    @property
    def cost_pounds(self) -> float:
        return self.cost_pence / 100


@dataclass
class Bill:
    # Household / charger identity
    household_name: str
    household_address: str
    charger_id: str

    # Period (calendar month, local time expressed as dates)
    period_year: int
    period_month: int

    # Itemised lines (one per occupied hour)
    lines: list[BillLine] = field(default_factory=list)

    # Site / landlord identity (for statement header)
    landlord_name: str = "307 Upper Richmond Road Management"
    landlord_address: str = "Upper Richmond Rd, London SW15 6SS"

    @property
    def total_kwh(self) -> float:
        return sum(line.kwh for line in self.lines)

    @property
    def total_cost_pence(self) -> float:
        return sum(line.cost_pence for line in self.lines)

    @property
    def total_cost_pounds(self) -> float:
        return self.total_cost_pence / 100

    @property
    def period_label(self) -> str:
        import calendar
        return f"{calendar.month_name[self.period_month]} {self.period_year}"
