# ocpp-garage

EV charging DLM and per-household billing backend for a UK apartment block.

## Setup

### 1. Install Python ≥ 3.11

Recommended: [uv](https://docs.astral.sh/uv/getting-started/installation/) (fast, modern Python package manager)

```powershell
# Install uv on Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Install dependencies

```powershell
uv sync
```

### 3. Configure credentials

```powershell
copy .env.example .env
# Edit .env — already populated for this site
```

### 4. Discover Easee site structure

```powershell
uv run python scripts/discover_easee.py
```

This prints the full site/circuit/charger hierarchy and available data fields,
which informs the database schema and meter sampling strategy.

## Project structure

```
src/ocpp_garage/
  config.py               # settings from .env
  adapters/
    base.py               # vendor-neutral ChargerAdapter interface
    easee/                # Easee Cloud implementation (active)
    ocpp/                 # future: OCPP 1.6J CSMS (stub)
  billing/                # billing engine (coming next)
  tariff/                 # tariff providers (flat → Agile)
  dlm/                    # dynamic load management
  models/                 # SQLAlchemy ORM models
scripts/
  discover_easee.py       # one-off: explore live API structure
```

## See also
- [SPECIFICATION.md](SPECIFICATION.md) — full system specification
