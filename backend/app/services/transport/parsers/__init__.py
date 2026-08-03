"""Per-network fuel-card statement parsers (G3.2). Each module here
implements ONE `app.services.transport.fuel_card_parser.FuelCardParser`.

Shipped so far: `eurowag` (Eurowag/W.A.G. — R20's own first worked example,
per-country footer entity, WO-62/G3.2 slice 1), `e100` (E100 — R20's SECOND
worked example, anchor-to-marker entity detection PLUS the reverse
VAT-inclusive-gross money model, WO-63/G3.2 slice 2), and `q8` (Q8/Kuwait
Petroleum — list-price money model identical in shape to Eurowag's, the
first parser to prove a single statement can carry more than one country/
currency, and deliberately NO seller-entity detection since Q8 has no R20
worked example; the Port One off-invoice rebate merge that would populate
`net_eur_eff` is explicitly deferred to G4.2/M5 — WO-64/G3.2 slice 3), and
`dkv` (DKV Euro Service — the supplier-STATED-EUR money model: the
statement's own per-line EUR figure is trusted verbatim and the VAT is
pro-rated at the same implied rate, `fx_source="stated"`, per
`BA_fleet_fuel.md` §5.1's "trusts the supplier's per-line EUR and
pro-rates"; likewise NO seller-entity detection, no R20 worked example —
WO-65/G3.2 slice 4), and `tfc` (TFC by Moya — the DERIVED-NET hub-only
discount money model: litres x LIST price minus the station-classified
per-litre tier (−0.205/L hub, −0.19/L Meer, 0 third-party) at a flat 21%
VAT, all parser-local arithmetic over the unchanged `ParsedFuelLine`
contract; likewise NO seller-entity detection, no R20 worked example —
WO-67/G3.2 slice 5), and `moeve` (Moeve/ex-Cepsa — the per-line-IVA
VAT-inclusive money model: each line reverse-calculated at ITS OWN
printed IVA rate (the harvested 10% gasoleo / 21% EcoBlue examples) at
the harvested 6-dp internal precision, with the per-line cash-at-pump
settlement flag riding `provenance_note` only (a flagged row is still an
invoiced, VAT-bearing fuel line — the netting is settlement-side, M4/G3.5
territory); likewise NO seller-entity detection, no R20 worked example —
WO-68/G3.2 slice 6). The one remaining network named in
`BA_fleet_fuel.md` §5.1 (BP/Aral) is explicitly a future slice — see
`docs/plan/plan-a/wo/WO-68-G3.2-slice6.md`'s "Out of scope" section for
why it is its own work order rather than being attempted here.
"""

from __future__ import annotations
