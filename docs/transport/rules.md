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
| _none yet_ | — | — | — | — |

<!-- Row template:
| R9 | 30-Sep filing deadline is a fatal time-bar | app/services/transport/claim_gates.py | tests/transport/test_r9_deadline_time_bar.py | Art. 15 Dir. 2008/9/EC; CJEU C-294/11 Elsacom |
-->
