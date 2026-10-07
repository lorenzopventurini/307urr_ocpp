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

## Generating a statement

```powershell
uv run python scripts/generate_statement.py            # prompts for the month
uv run python scripts/generate_statement.py 2026-07    # or name it directly
uv run python scripts/generate_statement.py 2026 7
```

Writes `statements/<charger>_<YYYY>_<MM>.pdf`. With no argument it offers the
current month, or the previous one during the first days of a new month.

Add `--email` to send it to the tenant (`tenant_email` in `households.toml`),
copying the management company (`landlord_email`). `--previous-month` bills the
last complete calendar month, as the scheduled run does.

## Automated monthly email

A GitHub Actions workflow generates and emails the previous month's statement at
06:17 UTC on the 2nd of each month.

It runs from a **separate private repository** containing only
[deploy/github-actions/monthly-statement.yml](deploy/github-actions/monthly-statement.yml),
which checks out this public repo's code at run time. The billing logic stays
publicly auditable (each run logs the commit it billed with); the logs, PDFs and
credentials stay private. A fork won't do: GitHub keeps forks of public repos
public.

### One-time setup

1. **Sending mailbox.** Create a new Gmail account for statements, turn on
   2-Step Verification, then create an app password at
   <https://myaccount.google.com/apppasswords>. Gmail signs its mail (SPF, DKIM
   and DMARC all pass), which is what keeps it out of spam folders.
2. **Private repo.** Create one, e.g. `307urr-statements` (private), and add the
   workflow file at `.github/workflows/monthly-statement.yml`.
3. **Secrets** (Settings → Secrets and variables → Actions → *Secrets*):

   | Secret | Value |
   |---|---|
   | `EASEE_USERNAME`, `EASEE_PASSWORD`, `EASEE_CHARGER_SERIAL` | as in `.env` |
   | `HOUSEHOLDS_TOML` | the entire contents of `households.toml` |
   | `SMTP_USERNAME` | the new Gmail address |
   | `SMTP_PASSWORD` | the 16-character app password |

4. **Variables** (same page → *Variables*):

   | Variable | Value |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `TARIFF_FLAT_PRICE_PENCE` | `25.0` |
   | `TARIFF_INTERVAL_MINUTES` | `60` |
   | `STATEMENT_TEST_RECIPIENT` | your own address, while testing |
   | `EMAIL_FROM_NAME` | optional, display name on the email |
   | `EMAIL_REPLY_TO` | optional, e.g. the management company's address |
   | `CODE_REF` | optional, tag or commit of this repo to bill with (default `main`) |

5. **Test.** Actions → *Monthly EV charging statement* → *Run workflow*. While
   `STATEMENT_TEST_RECIPIENT` is set, the email goes **only** there, marked
   `[TEST]` and listing who it would have gone to. In Gmail, open it, choose
   *Show original*, and check SPF, DKIM and DMARC all say `PASS`.
6. **Go live.** Add `tenant_email` and `landlord_email` to `households.toml`,
   update the `HOUSEHOLDS_TOML` secret, then delete the
   `STATEMENT_TEST_RECIPIENT` variable.

If a run fails, GitHub emails the repository owner. Each run also keeps a copy
of the PDF as a downloadable artifact for 90 days. Re-running a workflow sends
the email again.

## Standalone Windows build

For an end user with no Python installed, the same tool builds into a single
executable:

```powershell
.\scripts\build_exe.ps1
```

This produces `dist\EV-Statement-Package\` containing `EV-Statement.exe`, a copy
of `.env` and `households.toml`, and an empty `statements\` folder. Hand over the
whole folder — the exe reads its configuration from its own directory, so the
tariff or tenant details can be changed by editing those files, with no rebuild.

The end user double-clicks the exe and enters a month as `YYYY-MM`.

Two things the build depends on, both enforced in `statement.spec`:

- **Config is never bundled into the exe.** A PyInstaller archive is not
  encrypted and is trivially extractable, so shipping `.env` inside it would
  hand over the Easee account password. The package folder holds credentials
  and tenant personal data — treat it accordingly.
- **Anaconda's OpenSSL DLLs are copied in explicitly.** They live in
  `Library\bin`, where PyInstaller's dependency scan cannot see them; without
  them the build succeeds but dies on the first HTTPS call to Easee.

Building requires `pyinstaller`, and Anaconda's obsolete `pathlib` backport
must be removed first (`pip uninstall pathlib`) — PyInstaller refuses to run
while it is installed.

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
  statement_cli.py        # statement tool (shared by script, .exe and CI)
  billing/mailer.py       # statement email: recipients, message, SMTP
scripts/
  discover_easee.py       # one-off: explore live API structure
  generate_statement.py   # entry point, also the frozen build's entry point
  build_exe.ps1           # builds the standalone Windows package
statement.spec            # PyInstaller build definition
deploy/github-actions/    # scheduled workflow, for the private ops repo
tests/                    # pytest suite
```

## See also
- [SPECIFICATION.md](SPECIFICATION.md) — full system specification
