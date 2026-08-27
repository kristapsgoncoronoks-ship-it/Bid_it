# Supplier Reliability Rating — Design (WO-Q, deliverable 1)

> **Status:** DESIGN — code is gated on this document per the owner's §12
> decision and TODO's own note ("needs a design pass before code").
> Origin: owner decision 2026-08-08 §12 (recorded in
> [DECISIONS-NEEDED](../DECISIONS-NEEDED.md)): *every supplier carries a
> reliability rating, computed from multiple criteria* — three named:
> **overcharges**, **exchange-rate treatment**, and **lines charged that were
> never agreed** — presented *"so it reads as evidence rather than a verdict
> on a counterparty."*

## 1. Scope and stance

**Transport suppliers** (the fuel-card/toll counterparties behind
`fuel_transactions.supplier` and the §2.5 contract terms) — that is the
context §12 was decided in, and all three named criteria are transport
facts. The AP-side agreed-price machinery (WO-G2) stays a separate surface;
if the owner later wants a vendor-side rating, it gets its own order.

**Everything is DERIVED, no new tables.** All three criteria are computable
from rows that already exist — `vat_overcharge_claims` (WO-82),
`fuel_transactions.fx_source` (WO-88/89 made it trustworthy at the writer),
and `contract_audit.audit()`'s two harvested flags (`short discount`,
`over ceiling`) plus its never-agreed detection. The G4.7-era note that
reliability "needs an append-only `advertised_prices` table" predates the
owner's criteria and is **dropped**: advertised-price tracking is a
different, fourth criterion the owner did not name; the design leaves a slot
for it but builds nothing for it.

**The R53 constraint governs this surface too.** The rating is negotiation
and vigilance EVIDENCE about the org's own captured data. It must carry the
same structural guarantees the savings workspace carries: no contract-breach
vocabulary in field names or routes, `savings.LEGAL_FRAMING`-style framing
text server-owned and rendered verbatim, no path from a rating into the
claim-back flow's euros (import scan both directions), read-only router.

## 2. The three criteria — contribution, source, window

Window: **rolling 12 months** from "today", recomputed on every read (same
derivation-first stance as next-actions and onboarding). The window is
stated on the wire and rendered; a supplier with under 3 months of activity
in window is labelled `insufficient history` and gets NO rating — a thin
sample must not read as a clean bill or an indictment.

| Criterion | Source rows (existing) | The evidence figure | Normalisation |
|---|---|---|---|
| **Overcharges** | `vat_overcharge_claims` for the supplier, `period` in window | count of cases; `detected_eur` sum; outcome split (recovered / rejected / written_off / **ignored** — shown, see §4) | € detected per €1,000 net spend in window (spend = supplier's `net_eur` sum over validated lines) |
| **Exchange-rate treatment** | `fuel_transactions` in window with a non-EUR `currency` | share of lines by `fx_source` (`stated` vs `ecb`); where a stated rate AND an ECB rate both exist for the line's date: the median markup of stated over ECB, in basis points | share (%) + median markup (bps); EUR-native lines are excluded from the denominator (no rate involved) |
| **Never-agreed lines** | `contract_audit.audit()` over the supplier's validated lines in window | count and share of lines that priced OUTSIDE any governing term — no `expected_discount_eur_l` and no ceiling term covering them — plus the two flagged breaches as separate counts (`short discount`, `over ceiling`) | flagged-or-ungoverned lines / all validated lines (%) |

Each criterion returns: the raw counts, the euros where euros exist, the
normalised figure, and **row-level drill-down links** (the overcharge cases,
the statement lines) — the drill-down IS what makes it evidence.

## 3. The rating — bands, not scores

The owner asked for *a rating computed from multiple criteria*; the
presentation requirement forbids a verdict. The design resolves that pair:

- Per criterion, a three-value band computed from the normalised figure:
  `clean` (zero findings in window) · `findings` (present, below the
  threshold) · `recurring` (at or above the threshold). Thresholds are
  **org-configurable with stated defaults** (overcharges: ≥ 3 cases or
  ≥ €5 per €1,000 spend; FX: median stated markup ≥ 50 bps; never-agreed:
  ≥ 10% of lines) — defaults live in one constants block, rendered on the
  screen next to the band so the reader always sees the rule that produced
  the label.
- The overall rating is the **worst criterion band**, never a weighted
  composite — a weighted number invites reading precision into a judgment
  call, and "worst of three" is explainable in one sentence.
- Vocabulary is deliberately about the DATA, not the counterparty:
  `clean / findings / recurring`, never "good/bad supplier", "risk",
  "trustworthy", or claim words. The framing line (server-owned, verbatim):
  *"Computed from this workspace's own captured statements and contract
  terms over the stated window. It describes patterns in the data — it is
  not an assessment of the counterparty."*

## 4. Design decisions taken here (and the one left open)

1. **Ignored claim-backs count as evidence.** The §12 ignore action is an
   operator's choice not to REACT; the overcharge still happened. Ignored
   cases appear in the outcome split with their audited reasons one click
   away, and they count toward the overcharge criterion. (The alternative —
   excluding them — would let the rating be managed by ignoring.)
2. **No history table, no snapshots.** The rating is recomputed; if the
   underlying rows change (a rebate merge corrects `net_eur_eff`, a case is
   reinstated), the rating follows. An auditor asking "what did it say in
   March" reads the March data through the same derivation.
3. **Permission:** `TRANSPORT_READ` (the analytics permission reserved for
   exactly this derived slice, per WO-79's wording). Threshold configuration:
   `VAT_WRITE`, audited old→new like the excise rates.
4. **Surface:** a "Reliability" tab on the recovery workspace (`/recovery`),
   one card per supplier: three criterion rows with band + figures +
   drill-down, the overall band, the window, the framing line. No control
   mutates anything.
5. **OPEN (owner, non-blocking):** whether the rating may appear on the
   claim-back demand letter or evidence packet. Recommendation: **no** —
   those artifacts are R53-framed formal demands; importing a
   pattern-of-behaviour rating into them changes their legal character. The
   build proceeds with the rating web-only; the letter question goes to
   DECISIONS-NEEDED.

## 5. Build plan (deliverable 2 — its own certification)

1. `app/services/transport/reliability.py` — the derivation: per-supplier
   criterion figures + bands + framing constants; reads ONLY through the
   WO-85 canonical registry (new registry builders where a cut is missing);
   structural scans mirroring WO-87/WO-90 (vocabulary, import direction,
   read-only router) each with a seeded-violation self-test.
2. Route: `GET /transport/reliability` (+ `GET/PUT
   /transport/reliability/thresholds`) under the transport router batch.
3. SPA: the Reliability tab on `/recovery`, e2e specs asserting the framing
   verbatim, the absence scans, and the insufficient-history rendering.
4. Tests: hand-computed expectations over seeded lines that place one
   supplier in each band per criterion; the ignored-case-counts rule pinned;
   both seeded violations proven.
5. Docs: MANUAL §7b paragraph, diagram-matrix row is NOT needed (no new
   diagram), DECISIONS-NEEDED gains the §4.5 letter question, TODO/plan
   shipped stamps.
