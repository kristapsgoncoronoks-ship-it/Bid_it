"""Per-network fuel-card statement parsers (G3.2). Each module here
implements ONE `app.services.transport.fuel_card_parser.FuelCardParser`.

Shipped in this slice: `eurowag` (Eurowag/W.A.G. — R20's own worked example,
per-country footer entity). The remaining six networks named in
`BA_fleet_fuel.md` §5.1 (E100, DKV, Q8/Port One, BP/Aral, TFC by Moya,
Moeve) are explicitly future slices — see `docs/plan/plan-a/wo/
WO-62-G3.2-slice1.md`'s "Out of scope" section for why each is its own
work order rather than being attempted here.
"""

from __future__ import annotations
