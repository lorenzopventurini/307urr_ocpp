# EV Charging DLM & Billing Backend — Specification

**Status:** Draft v0.2
**Date:** 2026-06-06
**Context:** UK apartment block, Easee One chargers, per-household billing under a (future) dynamic hourly tariff. Current tariff assumed flat at **25p/kWh (VAT-inclusive)**, but infrastructure must support time-varying prices.

---

## 0. Confirmed Requirements (from stakeholder)

| # | Decision | Consequence |
|---|---|---|
| 1 | **Use Easee Cloud integration now** (keep Easee's built-in load balancing), but **stay forward-compatible with future non-Easee OCPP 1.6J chargers**. | Vendor-isolated ingestion + DLM adapters; Easee adapter for today, OCPP CSMS path kept open. |
| 2 | **One charger = one household.** | No RFID/per-user attribution needed; attribution is by charger via time-bounded `ChargerAssignment`. |
| 3 | **Supply: 32A, three-phase. No Equalizer.** | DLM uses **static headroom** only (cannot measure non-EV building load). See §7. |
| 4 | **1 charger today; up to 16 in future** (all 3-phase). | At 16 chargers the 32A supply is heavily oversubscribed → DLM must support **queuing/round-robin**, not just throttling. See §7.5. |
| 5 | **Monthly billing. No standing charge. Prices are VAT-inclusive.** Tariff source to be updated later. | Billing engine sums VAT-inclusive interval costs; no standing-charge apportionment; monthly statement runs. |

> **Resolved:** Easee API confirms circuit `ratedCurrent = 25A`, `fuse = 32A`. DLM budget is **25A** (Easee's configured limit), not 32A.

---

## 1. Goals & Scope

### 1.1 Primary goals
1. **Accurate per-household billing** of EV charging energy, where the unit price varies over time.
2. **Dynamic Load Management (DLM)** so the chargers never collectively exceed the building's electrical supply capacity.
3. A **tariff abstraction** that today returns a flat 25p/kWh but can later plug into a real dynamic tariff (e.g. Octopus Agile) with no change to the billing engine.

### 1.2 The core billing challenge
Under a dynamic tariff, **total energy per session is not enough**. A single charging session can span several pricing intervals at different prices. To bill correctly you must know **how much energy was consumed in each pricing interval** and apply that interval's price.

The system therefore must:
- Sample each charger's **cumulative energy register (kWh)** at fixed wall-clock interval boundaries.
- Compute consumption per interval as `reading(t) − reading(t−1)`.
- Multiply each interval's kWh by that interval's price and sum per household per billing period.

> **Important UK detail:** UK half-hourly settlement and real dynamic tariffs (Octopus Agile, etc.) price in **30-minute** intervals, not hourly. Even though the brief says "hourly", the system should treat the **interval length as configurable** and default to **30 minutes** to avoid a costly rework later. A flat 25p tariff is interval-agnostic, so adopting 30-minute buckets now costs nothing.

### 1.3 Out of scope (for v1, flag for later)
- Payment collection / direct debit / card processing (we generate invoices; collection is separate).
- Solar/battery/V2G accounting.
- Tenant-facing mobile app (we may expose an API / statements).

---

## 2. Integration with Easee — two possible paths

This is the most consequential architectural decision and **must be resolved early** (see §10 Information Needed).

### 2.1 Path A — Easee Cloud API (proprietary)
Easee chargers report to **Easee Cloud**. Integration is via Easee's REST API + a SignalR real-time stream.

- **Auth:** account credentials exchanged for a bearer access token + refresh token.
- **Entities:** Site → Circuit → Charger, plus the **Equalizer** (whole-building current sensor).
- **Data available:** charger state/observations (`lifetimeEnergy`, `sessionEnergy`, current/power per phase), charging sessions, and energy aggregates.
- **DLM:** keep using Easee's built-in **charger-to-charger load balancing** and **Equalizer** dynamic load balancing. Our backend mostly *monitors* and configures limits.
- **Pros:** retains Easee's mature, safety-certified load balancing; least disruptive; works today.
- **Cons:** energy is exposed as session totals / aggregates — getting clean **interval-aligned** meter readings requires polling the cumulative register on each boundary and reconciling; proprietary API subject to change.

### 2.2 Path B — OCPP 1.6J to our own CSMS
Point the chargers at our own **Charging Station Management System (CSMS)** speaking OCPP 1.6J. (The project name `ocpp_garage` suggests this is the intended direction.)

- **Meter data:** configure `ClockAlignedDataInterval = 1800` (30 min) so each charger emits **MeterValues** with `Energy.Active.Import.Register` aligned exactly to clock boundaries — **this is the ideal input for interval billing** and removes most reconciliation complexity. `MeterValueSampleInterval` gives additional in-session samples for DLM.
- **DLM:** we own it — implemented via OCPP **Smart Charging** (`SetChargingProfile` / `TxDefaultProfile`, charging schedules in Amps or Watts).
- **Pros:** clean clock-aligned meter values; full control of DLM; vendor-neutral (works with non-Easee hardware later).
- **Cons / risks:**
  - Easee's OCPP support **must be confirmed and enabled** for this hardware/firmware/market — historically Easee gated OCPP behind Easee Cloud and rollout varies. **Verify with Easee before committing.**
  - Pointing chargers at a third-party CSMS **typically disables Easee's built-in load balancing** — meaning **our CSMS becomes safety-critical for DLM**. This raises the engineering and reliability bar significantly.
  - We must host a publicly reachable, secure WebSocket endpoint (TLS, per-charger auth).

### 2.3 Decision (locked)
**Path A (Easee Cloud) is the active integration**, to retain Easee's built-in, certified load balancing (Decision #1). **Path B (OCPP 1.6J CSMS) is kept architecturally open** so future non-Easee OCPP-compliant chargers can be added without reworking the core.

This is enforced by a **vendor adapter boundary**: both Easee and OCPP adapters normalise into the same internal events (`MeterReading`, session start/stop, telemetry) and the same DLM control interface. The billing engine and data model never see vendor specifics.

```
ChargerAdapter (interface)
 ├── EaseeCloudAdapter   ← active (REST + SignalR, Easee load balancing)
 └── OcppCsmsAdapter     ← future (OCPP 1.6J, ClockAlignedDataInterval=1800)
```

The data model and billing engine below are **integration-agnostic** — every adapter feeds the same `MeterReading` table.

---

## 3. System Architecture

```
                ┌─────────────────────────────────────────────┐
   Chargers ──► │  Ingestion Adapter                           │
  (Easee/OCPP)  │   • Easee Cloud client (REST + SignalR)  OR  │
                │   • OCPP 1.6J CSMS (WebSocket)               │
                └───────────────┬─────────────────────────────┘
                                │ normalised events
                ┌───────────────▼─────────────────────────────┐
                │  Core Services                               │
                │  ┌──────────────┐  ┌──────────────────────┐  │
                │  │ Meter Sampler│  │ DLM Controller       │  │
                │  │ (interval    │  │ (allocate current,   │  │
                │  │  register    │  │  push limits)        │  │
                │  │  reads)      │  └──────────────────────┘  │
                │  └──────┬───────┘                            │
                │         ▼                                    │
                │  ┌──────────────┐  ┌──────────────────────┐  │
                │  │ Billing Engine│ │ Tariff Provider      │  │
                │  │ (bucket → £)  │◄┤ (Flat 25p → Agile…)  │  │
                │  └──────┬───────┘  └──────────────────────┘  │
                └─────────┼────────────────────────────────────┘
                          ▼
                ┌─────────────────────┐   ┌────────────────────┐
                │ PostgreSQL /         │   │ API + Statements / │
                │ TimescaleDB          │──►│ Admin UI           │
                └─────────────────────┘   └────────────────────┘
```

Core components:
- **Ingestion Adapter** — vendor-specific; normalises into common events (meter reads, session start/stop, charger telemetry).
- **Meter Sampler** — guarantees a cumulative-register reading per charger per interval boundary (polls on Path A; consumes ClockAligned MeterValues on Path B). Produces `EnergyBucket` rows.
- **DLM Controller** — computes per-charger current limits and applies them (monitor-only on Path A; active control on Path B).
- **Tariff Provider** — interface returning the price for a given timestamp/interval.
- **Billing Engine** — joins energy buckets to tariff prices to produce bills.
- **Storage** — time-series + relational (Postgres; TimescaleDB recommended for the reading series).
- **API / Admin** — manage chargers↔households, generate statements, view dashboards.

---

## 4. Data Model

### 4.1 Topology
- **Site** — the building; supply capacity (main fuse rating, A), phase configuration, grid connection limit, timezone (`Europe/London`).
- **Circuit** — a distribution circuit / sub-board with its own current limit.
- **Charger** — `id`, vendor serial, max current/power, phases (1- or 3-phase), MID-meter certified? (bool), firmware.
- **Equalizer / Building Meter** (optional) — whole-building import measurement for dynamic DLM.

### 4.2 People & assignment
- **Household / Tenant** — billing entity; contact details; billing preferences.
- **ChargerAssignment** — `(charger_id, household_id, valid_from, valid_to)`. Time-bounded so tenant changes and re-assignments are handled correctly. **Per Decision #2, each charger maps to exactly one household**, so attribution is purely by charger — no RFID/per-user attribution required. (The session `idTag` field is retained in the model only for future shared-charger scenarios.)

### 4.3 Energy & sessions
- **Session** — `charger_id`, start/stop, optional `idTag`/RFID, energy total (kWh), source ref. Used for reconciliation and per-user attribution on shared chargers.
- **MeterReading** — `charger_id`, `timestamp_utc`, `cumulative_kwh` (the cumulative import register). The raw truth; immutable; retained long-term.
- **EnergyBucket** — `charger_id`, `interval_start_utc`, `interval_length`, `kwh` (derived = register delta), `source` (`measured` | `interpolated` | `backfilled`), `quality_flags`. The billable unit.

### 4.4 Tariff
- **TariffPrice** — `tariff_id`, `interval_start_utc`, `interval_length`, `unit_price` (pence/kWh), `includes_vat` (bool), `currency`. Immutable price points with validity. Flat 25p is stored as a rule, not 17k rows.
- **TariffDefinition** — metadata: provider, interval length, standing-charge handling, VAT treatment.

### 4.5 Billing
- **Bill** — `household_id`, `period_start/end`, `total_kwh`, `total_energy_cost`, `standing_charge`, `vat`, `total_due`, status, generated_at.
- **BillLine** — per-interval or per-day breakdown for transparency/disputes (`interval`, `kwh`, `unit_price`, `cost`).
- **Invoice / Statement** — rendered, immutable, auditable document.

---

## 5. Meter Sampling & Energy Allocation (the heart of the system)

### 5.1 Principle
Bill from **differences of a monotonic cumulative register**, never by summing instantaneous power. The cumulative register is robust to missed reads (a gap just spans more time, the delta is still correct as long as you have the two endpoints).

### 5.2 Boundary alignment
- Buckets align to wall-clock boundaries in **Europe/London**, stored in **UTC**.
- **DST is mandatory to handle**: spring-forward day has 23 hours, autumn-fall-back has 25 hours (and a repeated local hour). Always compute boundaries in UTC and map to local for presentation; align tariff intervals to UTC settlement periods.

### 5.3 Easee Cloud — two available data sources (confirmed from API)

**Source 1 — Hourly usage API** (`/api/chargers/{serial}/usage/hourly/{from}/{to}`)
- Returns pre-computed UTC-aligned hourly `totalEnergy` buckets. **Used as the primary billing input for v1 (flat tariff).**
- Constraint: max ~7 days per request; a monthly billing run requires ~4–5 sequential calls (handled by `get_charger_usage_month()`).
- Limitation: hourly granularity only. When the tariff switches to 30-min pricing intervals, this source cannot be used alone — see Source 2.

**Source 2 — `lifetimeEnergy` polling** (from `/api/chargers/{serial}/state`)
- The monotonically-increasing cumulative kWh register. Polled every 30 minutes on interval boundaries; stored as `MeterReading`; deltas produce `EnergyBucket` rows.
- **Used as the billing source when the tariff is switched to 30-min dynamic pricing.**
- Also used now for DLM monitoring (current draw) regardless of tariff.
- Reconcile against session totals from the sessions API.

**Session API** (`/api/sessions/charger/{serial}/sessions/{from}/{to}`)
- Per-session `kiloWattHours`, `carConnected`/`carDisconnected`, `firstEnergyTransferPeriodStarted`/`lastEnergyTransferPeriodEnd`. Used for **reconciliation** against both sources above.

### 5.4 Path B (OCPP) sampling
- Set `ClockAlignedDataInterval = 1800`. Consume `Energy.Active.Import.Register` MeterValues at boundaries directly → near-zero interpolation.
- `MeterValueSampleInterval` (e.g. 60s) feeds DLM and fills gaps.

### 5.5 Reconciliation & data quality (required)
- **Invariant:** `Σ EnergyBucket.kwh` over a session window ≈ register delta over that window ≈ session total (within tolerance). Alert on divergence.
- **Gaps / offline chargers:** if boundary reads are missing, distribute the known delta across affected intervals using a documented rule (e.g. proportional to in-session sample power, else uniform), mark `interpolated`, and surface on the bill audit trail.
- **Idempotency:** ingestion is at-least-once; storage keyed so duplicates are no-ops.
- **Register resets / rollover:** detect non-monotonic register (firmware reset, replacement) and handle via session boundaries.

---

## 6. Tariff Abstraction

### 6.1 Interface
```
interface TariffProvider {
  // price (pence/kWh) effective for the interval containing `instant`
  getPrice(instant: UTCTimestamp): Promise<PricePoint>
  // bulk fetch for a billing period
  getPrices(periodStart, periodEnd): Promise<PricePoint[]>
}
```

### 6.2 Implementations
- **FlatRateProvider** — returns 25p/kWh for every interval (v1). Config-driven; price changes are versioned with `valid_from`.
- **AgileProvider** (future) — pulls Octopus Agile (or other) half-hourly prices from the supplier API; caches; handles publish lag (next-day prices released ~16:00).
- **ImportProvider** (future) — CSV / spreadsheet import for suppliers without an API.

### 6.3 Rules (locked per Decision #5)
- **VAT:** stored prices are **VAT-inclusive**. The 25p/kWh figure already includes VAT; bills present a VAT-inclusive total. (If an itemised VAT line is ever required, store the rate so net/VAT can be derived.)
- **Standing charge:** **not billed to households** — excluded from the billing engine. (The landlord absorbs it.)
- **Ofgem Maximum Resale Price:** a landlord reselling electricity to tenants **must not charge more than they paid**. Charging the VAT-inclusive supply unit price with no standing-charge markup is comfortably compliant; **document the methodology** for defensibility.

---

## 7. Dynamic Load Management (DLM)

### 7.1 Objective
Ensure `Σ charger current ≤ site/circuit capacity` at all times, per phase, while charging as many vehicles as fast as safely possible.

### 7.2 Budget for this site
- **Supply: 32A fuse, three-phase, no Equalizer** (Decision #3).
- **Easee's circuit is configured to 25A** (`ratedCurrent = 25`, confirmed from API). The DLM budget is therefore **25A per phase** — the 32A is the upstream fuse, not the circuit operating limit. `offlineMaxCircuitCurrentP1/2/3 = 25A` confirms the fail-safe is also set correctly.
- With **no Equalizer**, we cannot measure non-EV building load, so DLM uses **static headroom only**: the EV budget is fixed at 25A per phase.
- Easee's charger-to-charger load balancing enforces this in hardware/Easee Cloud; our backend reads the current draw for monitoring and can adjust the dynamic limit if needed.

### 7.3 Oversubscription at scale (important)
A 3-phase Easee One can draw up to **32A** — i.e. **one charger can consume the entire 32A budget**. With the planned maximum of **16 chargers**:
- Minimum charge current is **6A**. `32A ÷ 6A ≈ 5` → **at most ~5 chargers can charge simultaneously** at the minimum rate; the remaining 11 must **wait**.
- Therefore DLM cannot be throttle-only. At >5 active vehicles it must **queue / round-robin / time-share** access (e.g. rotate charging windows, or prioritise by arrival/fairness), pausing chargers rather than dropping them below 6A.
- This also has a **billing interaction**: paused chargers consume nothing, so households charging in cheaper intervals naturally pay less — fair, but worth communicating to tenants. Scheduling policy (who charges when) is a product decision once multiple chargers exist.

> For **1 charger today**, DLM is effectively a non-issue (one charger ≤ 32A). The queuing logic only needs to exist before the site grows past ~5 simultaneous chargers. Build the budget/limit plumbing now; defer the queuing scheduler until multi-charger rollout.

### 7.4 Allocation policy (configurable, for multi-charger future)
- **Static headroom** budget (above). Sharing strategy: equal share / priority / first-come, always respecting the **6A minimum** (below it → pause, don't trickle) and per-phase limits.
- **Dynamic headroom** remains available as a future option **if an Equalizer is fitted**.

### 7.5 Control & safety
- **Active path (Easee):** configure circuit/charger limits via Easee; rely on Easee's certified balancing for the hard safety guarantee. Our backend sets the budget and monitors current draw.
- **Future OCPP path:** push `SetChargingProfile`; **must implement fail-safe** — on comms loss, chargers fall back to a conservative local current limit. Never assume the network is up.
- Log all limit changes for audit.

> A future OCPP charger pointed at our own CSMS would **not** be covered by Easee's load balancing. If/when non-Easee chargers are added on the same supply, DLM across the mixed fleet becomes **safety-critical** and must be designed before that rollout (fail-safe defaults, conservative limits, alerting, testing).

---

## 8. Non-Functional Requirements

- **Timezone/clock:** store everything in UTC; present in Europe/London; DST-correct; NTP-synced hosts.
- **Reliability:** at-least-once ingestion + idempotent storage; retry with backoff; survive Easee Cloud / charger outages without losing billable energy (register-delta model tolerates gaps).
- **Retention:** keep raw `MeterReading`s and generated bills for **6 years** (UK billing/dispute norm). Immutable bills.
- **Security:** secrets in a vault (Easee credentials / OCPP charger certs); TLS everywhere; least privilege.
- **Privacy (GDPR/UK DPA):** tenant PII minimised, access-controlled, retention-limited; lawful basis for processing (contract/billing).
- **Auditability:** every bill traceable to the raw readings and the exact tariff prices used; data-quality flags surfaced.
- **Observability:** alerts on missed reads, reconciliation divergence, charger offline, DLM limit breaches.

---

## 9. UK Regulatory & Legal Notes (verify with a professional)

1. **Maximum Resale Price (Ofgem):** cannot charge tenants more than the landlord pays. Pass-through pricing complies; document it.
2. **VAT:** 5% domestic rate likely applies; confirm treatment for EV charging in a residential block.
3. **Legal-for-trade metering (MID):** for defensible billing, the meter should ideally be **MID-certified**. Easee One ships with an integrated MID-certified meter in many EU configurations — **confirm the UK units' MID status**; it materially affects dispute defensibility.
4. **Electricity supply licensing:** reselling to your own tenants generally falls under exemptions, but confirm given this is a managed block.
5. **Consumer billing transparency:** itemised statements (kWh, prices, period) support both compliance and tenant trust.

---

## 10. Information Needed From You

### ✅ Resolved (see §0)
- Integration: **Easee Cloud now, OCPP-forward-compatible**.
- Attribution: **one charger = one household**.
- Supply: **32A three-phase, no Equalizer**; **1 charger now, up to 16**.
- Billing: **monthly, no standing charge, VAT-inclusive prices**, flat 25p/kWh for now.

### ⏳ Still needed to start building
1. **Charger MID-certification status** — does your Easee One have the integrated MID-certified meter, and do you require legal-for-trade billing for disputes?
2. **Statement delivery** — email/PDF, a tenant portal, or just data via API?

*API credentials and site structure are now confirmed. DLM budget confirmed at 25A.*

### 🔭 Useful later (not blocking)
6. The **future dynamic tariff source** (e.g. Octopus Agile API?) when you switch from flat 25p.
7. **Hosting** (cloud/on-prem) and who maintains it.
8. Any **property-management / accounting systems** to integrate with.
9. **Tenant move-in/out handling** — how you'll notify the system of assignment changes.

---

## 11. Suggested Tech Stack & Roadmap

### 11.1 Stack (suggestion)
- **Language/runtime:** Python (FastAPI) or TypeScript (Node) — both have OCPP libraries and async support.
- **DB:** PostgreSQL + **TimescaleDB** extension for the meter-reading time series.
- **Scheduler:** APScheduler / cron / a job runner for boundary sampling and billing runs.
- **OCPP (Path B):** an OCPP 1.6J CSMS library (e.g. `mobilityhouse/ocpp` for Python).
- **Deployment:** containerised; secrets vault; TLS-terminated WebSocket for OCPP.

### 11.2 Phased roadmap
- **Phase 0 — Decisions & discovery:** resolve §10, confirm Easee OCPP feasibility, finalise interval = 30 min.
- **Phase 1 — Ingestion + storage:** connect to chosen path; persist `MeterReading`s; reconcile against sessions.
- **Phase 2 — Billing engine + FlatRateProvider:** interval buckets → 25p → per-household bills; statements + audit trail.
- **Phase 3 — DLM:** monitor (Path A) or active control with fail-safe (Path B).
- **Phase 4 — Dynamic tariff:** implement `AgileProvider`; swap provider with no billing-engine change.
- **Phase 5 — Hardening:** alerting, retention, dashboards, dispute tooling.

---

## 12. Key Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Easee OCPP not available/enabled in UK | Path B infeasible | Confirm with Easee in Phase 0; fall back to Path A |
| Disabling Easee load balancing for OCPP | DLM becomes safety-critical | Keep Equalizer as hardware safety net; fail-safe limits |
| Meter not MID-certified | Billing disputes hard to defend | Confirm MID status; document methodology |
| DST / interval misalignment | Wrong charges around clock changes | UTC storage, settlement-aligned buckets, explicit DST tests |
| Missed/aligned reads (Path A) | Inaccurate hourly allocation | Register-delta model + interpolation + reconciliation alerts |
| Proprietary API change (Path A) | Ingestion breaks | Adapter isolation; monitoring |
```
