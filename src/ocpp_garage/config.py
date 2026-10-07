from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # env_ignore_empty: an unset GitHub Actions variable arrives as "", which
    # would otherwise fail validation for numeric fields instead of defaulting.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True
    )

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

    # Email delivery (only needed when sending statements with --email)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"     # "starttls" (port 587) or "ssl" (port 465)
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""                # blank = same as smtp_username
    email_from_name: str = "EV Charging Statements"
    email_reply_to: str = ""
    # When set, EVERY statement email goes to this one address instead of the
    # tenant and management company. Use while testing; clear it to go live.
    statement_test_recipient: str = ""

    # Database (optional for now — used when DB is set up)
    database_url: str = Field(default="", alias="DATABASE_URL")


settings = Settings()
