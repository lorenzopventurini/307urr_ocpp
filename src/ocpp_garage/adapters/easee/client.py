"""
Low-level Easee REST API client.
Handles authentication, token refresh, and raw API calls.
All higher-level Easee logic lives in adapter.py, not here.
"""

import time
from dataclasses import dataclass, field

import httpx

EASEE_BASE = "https://api.easee.com"
# Access tokens expire after 24h; refresh 5 min before expiry to be safe.
_TOKEN_EXPIRY_BUFFER_SECS = 300


@dataclass
class _TokenPair:
    access_token: str
    refresh_token: str
    expires_at: float   # unix timestamp


class EaseeAuthError(Exception):
    pass


class EaseeApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Easee API {status_code}: {message}")
        self.status_code = status_code


class EaseeClient:
    """
    Thread-safe (for single-threaded use) Easee REST client.
    Automatically logs in and refreshes the bearer token as needed.
    """

    def __init__(self, username: str, password: str, timeout: float = 30.0) -> None:
        self._username = username
        self._password = password
        self._tokens: _TokenPair | None = None
        self._http = httpx.Client(base_url=EASEE_BASE, timeout=timeout)

    # ------------------------------------------------------------------ auth

    def _login(self) -> _TokenPair:
        resp = self._http.post(
            "/api/accounts/login",
            json={"userName": self._username, "password": self._password},
        )
        if resp.status_code == 401:
            raise EaseeAuthError("Invalid Easee credentials")
        _raise_for_status(resp)
        return _parse_token(resp.json())

    def _refresh(self, tokens: _TokenPair) -> _TokenPair:
        resp = self._http.post(
            "/api/accounts/refresh_token",
            json={
                "accessToken": tokens.access_token,
                "refreshToken": tokens.refresh_token,
            },
        )
        if resp.status_code in (400, 401):
            # Refresh token expired — re-login
            return self._login()
        _raise_for_status(resp)
        return _parse_token(resp.json())

    def _ensure_token(self) -> str:
        now = time.time()
        if self._tokens is None:
            self._tokens = self._login()
        elif self._tokens.expires_at - now < _TOKEN_EXPIRY_BUFFER_SECS:
            self._tokens = self._refresh(self._tokens)
        return self._tokens.access_token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._ensure_token()}"}

    # ------------------------------------------------------------------ HTTP helpers

    def get(self, path: str, **kwargs: object) -> object:
        resp = self._http.get(path, headers=self._auth_headers(), **kwargs)
        _raise_for_status(resp)
        return resp.json()

    def post(self, path: str, **kwargs: object) -> object:
        resp = self._http.post(path, headers=self._auth_headers(), **kwargs)
        _raise_for_status(resp)
        return resp.json() if resp.content else None

    # ------------------------------------------------------------------ Easee API endpoints

    def get_profile(self) -> dict:
        return self.get("/api/accounts/profile")  # type: ignore[return-value]

    def get_sites(self) -> list[dict]:
        return self.get("/api/sites")  # type: ignore[return-value]

    def get_site(self, site_id: int) -> dict:
        return self.get(f"/api/sites/{site_id}")  # type: ignore[return-value]

    def get_circuits(self, site_id: int) -> list[dict]:
        return self.get(f"/api/sites/{site_id}/circuits")  # type: ignore[return-value]

    def get_charger(self, serial: str) -> dict:
        return self.get(f"/api/chargers/{serial}")  # type: ignore[return-value]

    def get_charger_state(self, serial: str) -> dict:
        return self.get(f"/api/chargers/{serial}/state")  # type: ignore[return-value]

    def get_charger_config(self, serial: str) -> dict:
        return self.get(f"/api/chargers/{serial}/config")  # type: ignore[return-value]

    def get_charger_sessions(self, serial: str, from_: str, to: str) -> list[dict]:
        """from_ / to: ISO 8601 date strings e.g. '2025-01-01'"""
        return self.get(  # type: ignore[return-value]
            f"/api/sessions/charger/{serial}/sessions/{from_}/{to}"
        )

    def get_charger_usage(self, serial: str, from_: str, to: str) -> list[dict]:
        """
        Hourly energy usage for a charger. from_/to are ISO 8601 date strings.
        Easee limits this to ~7 days (168 hourly buckets) per request.
        For longer periods use get_charger_usage_month().
        """
        return self.get(  # type: ignore[return-value]
            f"/api/chargers/{serial}/usage/hourly/{from_}/{to}"
        )

    def get_charger_usage_month(self, serial: str, year: int, month: int) -> list[dict]:
        """
        Fetch all hourly usage for a calendar month, splitting into weekly
        chunks to stay within Easee's 7-day limit per request.
        Returns a flat list of hourly buckets sorted by `from`.
        """
        import calendar
        from datetime import date, timedelta

        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])

        buckets: list[dict] = []
        chunk_start = first
        while chunk_start <= last:
            chunk_end = min(chunk_start + timedelta(days=6), last)
            chunk = self.get_charger_usage(
                serial,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
            )
            buckets.extend(chunk)
            chunk_start = chunk_end + timedelta(days=1)

        return sorted(buckets, key=lambda b: b["from"])

    def set_dynamic_charger_current(
        self,
        serial: str,
        phase1: float,
        phase2: float,
        phase3: float,
    ) -> None:
        """
        Set the dynamic current limit per phase via Easee Cloud.
        Easee's own load balancer will respect the lower of this and its own limit.
        """
        self.post(
            f"/api/chargers/{serial}/settings",
            json={
                "dynamicChargerCurrent": max(phase1, phase2, phase3),
                "dynamicCircuitCurrentP1": phase1,
                "dynamicCircuitCurrentP2": phase2,
                "dynamicCircuitCurrentP3": phase3,
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EaseeClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ------------------------------------------------------------------ helpers

def _parse_token(data: dict) -> _TokenPair:
    # Easee returns expiresIn in seconds from now
    expires_in = data.get("expiresIn", 86400)
    return _TokenPair(
        access_token=data["accessToken"],
        refresh_token=data["refreshToken"],
        expires_at=time.time() + float(expires_in),
    )


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_error:
        try:
            msg = resp.json()
        except Exception:
            msg = resp.text
        raise EaseeApiError(resp.status_code, str(msg))
