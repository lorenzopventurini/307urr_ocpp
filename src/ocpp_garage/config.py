from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Easee
    easee_username: str
    easee_password: str
    easee_charger_serial: str
    easee_site_id: int = 0
    easee_circuit_id: int = 0

    # Electrical
    # Use Easee's circuit ratedCurrent (25A), not the raw fuse (32A)
    site_circuit_limit_amps: int = 25
    site_phases: int = 3

    # Tariff
    tariff_flat_price_pence: float = 25.0
    tariff_interval_minutes: int = 30  # 30 = UK half-hourly settlement

    # Billing
    billing_timezone: str = "Europe/London"

    # Database (optional for now — used when DB is set up)
    database_url: str = Field(default="", alias="DATABASE_URL")


settings = Settings()
