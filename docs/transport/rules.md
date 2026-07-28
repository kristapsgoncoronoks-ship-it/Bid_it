# Transport vertical — harvested rule ledger

Every rule harvested from the retired Fleet Fuel system (requirements R1–R76,
specified in `docs/plan/shared/specs/BA_fleet_fuel.md`) lands here as one row:
R-number → implementing module → proving test → legal source. The binding
process — three artifacts per rule, no R-test no merge, synthetic fixtures
only — is defined in [`harvest-protocol.md`](harvest-protocol.md). Read that
first; it is not optional.

A row is added in the **same PR** that implements the rule. An empty table
below a rule's Epic-G task means the rule is not yet harvested — never that
it was waived.

| R | Rule (short) | Module | Test | Legal source |
|---|---|---|---|---|
| R1 | Claim grain `(org, entity, refund_country, ref_period)`; same key upserts, never duplicates | `app/models/transport/vat_claim.py` (`VatRefundClaim`), `app/services/transport/claim.py` (`get_or_create_claim`) | `backend/tests/transport/test_r1_claim_grain.py` | Art. 5 / Art. 16 Dir. 2008/9/EC |
| R3 | One centralized `is_synthetic(ref, vat_id)` predicate — used by (future) lock/checklist/readiness/workbook gates | `app/services/transport/claim_gates.py` | `backend/tests/transport/test_r3_is_synthetic.py` | Art. 9 Dir. 2008/9/EC (line must tie to a real invoice) |
| R11 (partial — schema only) | Goods code `"9"` (luxuries/entertainment) can never reach `vat_claim_lines.goods_code`; the mapping module itself (fuel→1, tolls→4, unknown→10) is future work | `app/models/transport/vat_claim.py` (`ck_vat_claim_lines_goods_code_never_9`) | `backend/tests/transport/test_r11_goods_code_shape.py` | Art. 9 Dir. 2008/9/EC; Reg. 1174/2009 & 79/2012 Annex III |

<!-- Row template:
| R9 | 30-Sep filing deadline is a fatal time-bar | app/services/transport/claim_gates.py | tests/transport/test_r9_deadline_time_bar.py | Art. 15 Dir. 2008/9/EC; CJEU C-294/11 Elsacom |
-->
