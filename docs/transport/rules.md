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
| G1.2 (`ARCH_plan.md` tags this `R29`/`R30`, but those R-numbers are actually "engine owns product data, read-only app access" and "claims store separate from analytics store" — real rules, already satisfied by this codebase's existing separation, but not specific to this table; the real source for this row is `BA_fleet_fuel.md` **section 4.2** "the core business entity — a validated fuel TRANSACTION" + **section 8.1 items 4-6**, see `docs/plan/plan-a/wo/WO-50-G1.2.md`) | Typed `fuel_transactions` model (no duplicated positional schema); split `note` into `invoice_ref`+`provenance_note`; a structural natural key `(org, entity, supplier, period, line_seq)` so ingestion is insert-or-no-op, never DELETE-by-period; `product_group` derivation, precedence PROMO → HVO → {AdBlue,Parking,Toll/Fees} → Diesel → Service/Other (default) | `app/models/transport/fuel_transaction.py` (`FuelTransaction`), `app/services/transport/product_group.py` (`derive_product_group`), `app/services/transport/fuel_ingest.py` (`ingest_transaction`) | `backend/tests/transport/test_g1_2_fuel_transactions.py`, `backend/tests/transport/test_g1_2_product_group.py` | `BA_fleet_fuel.md` §4.2, §8.1 items 4-6 |

<!-- Row template:
| R9 | 30-Sep filing deadline is a fatal time-bar | app/services/transport/claim_gates.py | tests/transport/test_r9_deadline_time_bar.py | Art. 15 Dir. 2008/9/EC; CJEU C-294/11 Elsacom |
-->
