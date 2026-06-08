"""
Loader for households.toml — maps charger serials to tenant and site details.
Uses stdlib tomllib (Python 3.11+), no extra dependency.
"""

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Household:
    charger_serial: str
    bay: str
    tenant_name: str
    tenant_address: str
    tenant_email: str           # may be empty string
    assigned_from: date


@dataclass(frozen=True)
class SiteConfig:
    landlord_name: str
    landlord_address: str
    chargers: dict[str, Household]  # keyed by charger serial

    def get_household(self, charger_serial: str) -> Household:
        try:
            return self.chargers[charger_serial]
        except KeyError:
            raise ValueError(
                f"Charger {charger_serial!r} not found in households.toml. "
                "Add a [chargers.{serial}] block and fill in tenant details."
            )


def load(path: Path | str | None = None) -> SiteConfig:
    """
    Load households.toml. Defaults to the file next to the project root
    (two levels above this module's package directory).
    """
    if path is None:
        path = Path(__file__).parent.parent.parent / "households.toml"
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"households.toml not found at {path}. "
            "Copy households.example.toml to households.toml and fill in tenant details."
        )

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    site_raw = raw.get("site", {})
    chargers: dict[str, Household] = {}

    for serial, ch in raw.get("chargers", {}).items():
        chargers[serial] = Household(
            charger_serial=serial,
            bay=ch.get("bay", ""),
            tenant_name=ch.get("tenant_name", ""),
            tenant_address=ch.get("tenant_address", ""),
            tenant_email=ch.get("tenant_email", ""),
            assigned_from=date.fromisoformat(ch["assigned_from"]),
        )

    return SiteConfig(
        landlord_name=site_raw.get("landlord_name", ""),
        landlord_address=site_raw.get("landlord_address", ""),
        chargers=chargers,
    )
