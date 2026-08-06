"""R3 — the single centralized `is_synthetic()` predicate.
`docs/plan/shared/specs/BA_fleet_fuel.md` C2:

    is_synthetic(ref, vat_id) ==
        "INPUT" in ref or ref.startswith("ALL:") or ref == "UNMATCHED"
        or "INPUT" in str(vat_id)

"A pack containing ANY synthetic line CANNOT be filed. The same predicate is
used by: the lock gate ... the checklist gate ... the readiness check ... the
workbook builder. This centralization is deliberate: so they all block the
same set of synthetic refs." The consumers are now wired — the workbook/
evidence builders (WO-74, `claim_pack`), the LOCK GATE (WO-75,
`lock.submit_claim` via `claim_gates.enforce_no_synthetic_lines`, proven in
`test_r3_lock_gate.py`) and the checklist/stage view (WO-59/WO-60); this
test proves the ONE function they all import behaves exactly per the
harvested spec, clause by clause.
"""

from __future__ import annotations

import pytest

from app.services.transport.claim_gates import is_synthetic
from tests.factories.transport import synthetic_vat_id

# Synthetic, generated (tests/factories/transport.py) — never a literal VAT-id
# shape typed by hand (that trips the PII quarantine scanner, and for good
# reason: a hand-typed "plausible" id is exactly how real data leaks in).
_REAL_VAT_ID = synthetic_vat_id("LV", seed=1)


@pytest.mark.parametrize(
    "ref,vat_id,expected,reason",
    [
        # Clause 1: "INPUT" anywhere in the ref.
        ("INPUT", None, True, "bare INPUT placeholder"),
        ("INPUT-2025-03", None, True, "INPUT prefix with a date suffix"),
        ("FCA-0001234-INPUT", None, True, "INPUT suffix on an otherwise real-looking ref"),
        # Clause 2: ref STARTS WITH "ALL:" (a country-level aggregate).
        ("ALL:LV", None, True, "country aggregate"),
        ("ALL:LV-2025-Q4", None, True, "country aggregate with a period suffix"),
        # a ref that merely CONTAINS "ALL:" but doesn't start with it is NOT
        # synthetic by this clause (the predicate is startswith, not `in`) —
        # this is the exact distinction that would drift silently if a future
        # gate re-implemented the check as substring containment instead.
        ("SMALL:1234", None, False, "contains 'ALL:' but does not START with it"),
        # Clause 3: ref is EXACTLY "UNMATCHED" (not merely containing it).
        ("UNMATCHED", None, True, "the terminal _resolve_inv state"),
        ("UNMATCHED-2", None, False, "looks similar but is not the exact literal"),
        # Clause 4: "INPUT" anywhere in vat_id, even with a real-looking ref.
        ("FB/00012345", "INPUT", True, "real-looking ref, placeholder VAT id"),
        ("FB/00012345", "LV-INPUT-9999", True, "INPUT substring inside the vat_id"),
        # A genuinely resolved line: real ref, real (or absent) VAT id.
        ("FB/00012345", _REAL_VAT_ID, False, "real invoice ref + real VAT id"),
        ("FB/00012345", None, False, "real invoice ref, no VAT id captured"),
        ("TOLL-000042", "", False, "real ref, empty-string VAT id"),
    ],
)
def test_r3_is_synthetic_matches_the_harvested_predicate_clause_by_clause(
    ref, vat_id, expected, reason
):
    assert is_synthetic(ref, vat_id) is expected, reason


def test_r3_vat_id_defaults_to_none_and_is_safe():
    """The two-argument spec form: is_synthetic(ref) with no vat_id at all
    must not raise (str(None) == "None", which never contains "INPUT")."""
    assert is_synthetic("FB/00012345") is False
    assert is_synthetic("INPUT") is True


def test_r3_one_bad_line_is_enough_the_predicate_does_not_average():
    """Sanity-check the OR semantics: any single clause firing is decisive —
    there is no "mostly real" pass. This is what makes 'a pack containing ANY
    synthetic line cannot be filed' possible to enforce with one function."""
    # Every clause on its own is sufficient; combining clauses changes nothing.
    assert is_synthetic("ALL:EE", "INPUT") is True
    assert is_synthetic("UNMATCHED", _REAL_VAT_ID) is True
