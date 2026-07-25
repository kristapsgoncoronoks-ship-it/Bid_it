# Decision sheet — Plan A (evolve Bid_it) vs Plan B (greenfield from zero)

**Status: OPEN — owner decides.** Both plans are fully specified and executable.
Plan A detail: `ARCH_plan.md` + `PROMPTS.md` Part B (WO-1…10).
Plan B detail: `GREENFIELD_plan.md` (+ its §8 G-1…10).

## Common to both

Same charter/product; same specs (`BA_bidit.md`, `BA_fleet_fuel.md` R1–R76); same
20 invariants; same architecture (8 contexts + 2 projections); same stack
(Python 3.12/FastAPI/SQLAlchemy 2/Postgres 16 + React/TS); Fleet Fuel repo deleted
in both (archive first — PII + statutory retention; owner continuity plan required);
PII quarantine binds the archives; prompt-library Parts C/D/E serve both.

## Plan A — keep developing Bid_it

Keep repo, 86k LOC, 761 tests, proven RLS + concurrency-safe numbering, CI.
Delete only Fleet Fuel.

- M0 debt sprint 35–45d: 5 live security findings (vendor-IBAN fraud vector first),
  structural authz over 38 routers, one validation engine, one FX convention
  (fixes the SEPA original-currency-as-EUR bug), finish the org_id migration.
- M1 frontend gap 50–70d → M2 billing 20–30d → **chargeable 105–145d (~5–7 mo)**.
- M3 VAT vertical 70–100d; full charter **330–460d**.
- Pros: fastest revenue; keeps ~12–18 months of tested financial correctness.
- Cons: debt paid first; inherited gaps live until M0; legacy decisions ride along.

## Plan B — start from zero (both repos deleted)

New repo; invariants structural from the first migration. Ordering: create repo →
commit specs → build the PII deny-list while the old repos are readable → archive
both → delete both.

- M0 foundation 35–45d → M1 AR engine 45–60d → M2 AP 40–55d → M3 expenses/payments
  35–50d → M4 sellable 30–40d → **chargeable 185–250d (~9–12 mo)**.
- M5 transport 70–100d; full charter **≈ 315–440d**.
- Pros: no inherited debt/security holes; clean structure born right.
- Cons: ~80–105 more days to first revenue; 3,183 tests' encoded behaviour
  discarded (mitigated by the specs); second-system risk.

## One-line trade

**A buys time-to-revenue with inherited debt; B buys cleanliness with runway.**
Biggest measurable delta: ~80–105 engineer-days to first paying customer.
Engineering recommendation on commercial grounds: **A**. Decision rests with the owner.
