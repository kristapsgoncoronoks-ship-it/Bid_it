# Supplier cost analytics & external price data — owner idea, recorded 2026-08-20

> **Status: recorded, not yet scheduled.** Owner's words: "KPI supplier cost
> analytics, data scraping from suppliers. Cost changes and control. Cost
> change graphs. Data scraping module what we can implement —
> https://github.com/D4Vinci/Scrapling".

## 1. What the tree already has (build on, don't duplicate)

- `line_items.quantity` + `line_items.unit_price` — per-line unit economics on
  every captured supplier invoice.
- `services/benchmark.py` — cross-supplier **effective unit price**
  (spend / quantity per category), single-currency discipline (C1.7).
- The validation engine (ADR-0026) — one rule registry, `block | advise`.
- The transport vertical's overcharge machinery (`VatOverchargeClaim`,
  off-invoice rebates, tie-outs) — the *shape* of price control, currently
  industry-specific; the generic feature must follow the industry-neutral rule
  (nouns only in examples).
- Jobs queue + scheduler (worker tier), module gating (`org_modules`),
  webhooks/mailer for alerts, Explore + `report_writers` for graphs/exports.
- ADR-0027: **zero external calls by default, CI-enforced** — any fetching of
  external data must be an opt-in module, never ambient.

## 2. Phased proposal

**Phase 1 — cost KPIs from data already in the system (no scraping).**
Per supplier × item price history from invoice lines (normalised units, EUR at
recorded rates); cost-change detection (latest vs trailing average, % change,
top movers); cost-change graphs (time series per supplier / category / item);
KPI cards on the dashboard/benchmark surface. Pure read models over data the
tenant already owns — no legal surface, immediate value.

**Phase 2 — cost control (agreed prices).**
A per-supplier **agreed price list** (tenant table): item/category → agreed
unit price + validity window. New validation rule: invoiced unit price above
the agreed price ⇒ advisory finding (optionally `block` per org), plus an
overcharge worklist generalised from the transport vertical's shape. This is
"cost changes and control": the system knows what the price *should* be and
says so at capture time.

**Phase 3 — external price data module (the scraping part).**
An opt-in module (`org_modules`, default OFF, honouring ADR-0027) that ingests
external price points with provenance (`source`, `fetched_at`, `url/sha`) for
comparison against invoiced prices. Pluggable per-source adapters running as
worker-tier jobs; the fetch engine is a seam, with three source classes:
1. structured feeds/APIs and CSV price lists a supplier provides (prefer);
2. authorised portal logins the CLIENT owns (their credentials, their data);
3. public pages (respect robots.txt/ToS).

### Scrapling assessment (owner's link)

- MIT licence — compatible with this repo's GPL-3.0-or-later. Python, fits the
  backend; heavy optional deps (browser engines) belong on the worker tier
  only, never in web requests.
- Its headline features include **anti-bot stealth fetching**. That is a
  compliance decision, not a technical one: fetching a page against the site
  operator's expressed controls is a ToS/CFAA-adjacent risk the PLATFORM would
  carry for every tenant. Recommendation: adopt Scrapling (if at all) for its
  parsing/adaptive-selector value with the plain fetcher; stealth mode stays
  off unless the owner explicitly accepts that risk in writing per source.
- Deterministic-first, same as capture: a scraped price is advisory evidence
  with provenance, never the sole source of a booked figure.

## 2b. Engine choice (owner question 2026-08-20: adopt e.g. Apache Spark, or build?)

Neither — the engine already exists and is the right one at this scale.
**Postgres with DB-side aggregation IS the analytics engine** (`analytics.py`,
`explore.py`, `benchmark.py` all aggregate in the database), and it carries a
property no external engine gives us for free: the three-layer tenant
isolation (ORM guard + RLS) applies to every analytical query because the
data never leaves the guarded database. Copying data into a separate engine
means REIMPLEMENTING tenant isolation in that engine — a standing risk for
the product's core promise, paid on every future query.

**Spark specifically is the wrong size.** It is a JVM cluster platform for
distributed petabyte processing; production here is a single 4–8 GB VPS, and
an SME tenant's invoice history is thousands-to-millions of rows — Postgres
territory by orders of magnitude. Spark would add cluster ops, JVM memory
pressure, and a second data platform to secure, and would return answers no
faster on this data.

**The upgrade ladder if a MEASURED need appears** (in order, each step only
on a real p95 breach, per the index-strategy rule):
1. Postgres itself: covering/partial indexes, materialised read models,
   partitioning (already the documented plan).
2. **DuckDB** (MIT, in-process, zero-ops) embedded on the worker tier for
   heavy one-off analytical jobs over Parquet exports — the "ready engine"
   that actually fits this architecture if Postgres ever strains.
3. A dedicated OLAP store (e.g. ClickHouse) only at real multi-tenant
   platform-analytics scale — still simpler to run than Spark.

Phases 1–2 above need nothing beyond step 0 (the current engine).

## 3. Open questions for the owner (decision-gated)

1. Phase order confirmed? (1 → 2 → 3; phases 1–2 need no external data.)
2. Phase 2 default: advisory finding only, or org-configurable block?
3. Phase 3 sources actually needed by the pilot customers — supplier portals
   (whose credentials?), public catalogues, or emailed price lists (which the
   email-intake channel could already receive with far less machinery)?
4. Who owns ToS risk for scraped sources — platform or tenant? (Shapes whether
   adapters ship built-in or as per-tenant configuration.)

Industry-neutral rule applies throughout: "fuel card portal" is an EXAMPLE in
docs only; schema/copy say supplier, item, price list, source.
