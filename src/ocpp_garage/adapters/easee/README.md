# Easee Cloud Adapter

Implements the `ChargerAdapter` interface using the Easee REST API.

## Key files
- `client.py` — low-level HTTP + auth (token refresh, error handling)
- `adapter.py` — translates Easee responses to the shared domain types

## Adding a future OCPP adapter
Create `src/ocpp_garage/adapters/ocpp/` implementing the same `ChargerAdapter`
interface from `adapters/base.py`. The billing engine and DLM controller never
need to change — swap the adapter in the composition root (`config.py`).
