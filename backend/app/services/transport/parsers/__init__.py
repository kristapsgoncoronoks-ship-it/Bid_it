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
WO-65/G3.2 slice 4). The remaining three networks named in
`BA_fleet_fuel.md` §5.1 (TFC by Moya, Moeve, BP/Aral) are explicitly
future slices — see `docs/plan/plan-a/wo/WO-65-G3.2-slice4.md`'s "Out of
scope" section for why each is its own work order rather than being
attempted here.
"""

from __future__ import annotations
