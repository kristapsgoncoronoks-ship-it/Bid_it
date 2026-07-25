# Plan A — evolve Bid_it (runbook)

**Goal:** chargeable in 105–145 engineer-days. Plan: `ARCH_plan.md`. Orders: `wo/`.

1. `wo/WO-01.md` structural authz  →  2. `wo/WO-02.md` vendor bank-detail control →
3. `wo/WO-03.md` partners lockdown → then WO-04…WO-10 per Depends lines
(parallelizable set: 4,5,6,7,8,10; WO-9 needs WO-2).
Fleet Fuel deletion: `../shared/DECOMMISSION_FLEET_FUEL.md` after WO-6 Step 1.
Every session: prepend `../shared/00_MASTER_CONTEXT.md`. Review each order with
`../shared/REVIEW_PROMPTS.md`. M0 exit gate = WO-10's checklist. Then M1 per ARCH_plan §3.
