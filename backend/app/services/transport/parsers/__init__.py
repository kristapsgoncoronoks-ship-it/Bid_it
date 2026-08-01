"""Per-network fuel-card statement parsers (G3.2). Each module here
implements ONE `app.services.transport.fuel_card_parser.FuelCardParser`.

Shipped so far: `eurowag` (Eurowag/W.A.G. — R20's own first worked example,
per-country footer entity, WO-62/G3.2 slice 1) and `e100` (E100 — R20's
SECOND worked example, anchor-to-marker entity detection PLUS the reverse
VAT-inclusive-gross money model, WO-63/G3.2 slice 2). The remaining five
networks named in `BA_fleet_fuel.md` §5.1 (Q8/Port One, DKV, BP/Aral, TFC by
Moya, Moeve) are explicitly future slices — see `docs/plan/plan-a/wo/
WO-63-G3.2-slice2.md`'s "Out of scope" section for why each is its own work
order rather than being attempted here.
"""

from __future__ import annotations
