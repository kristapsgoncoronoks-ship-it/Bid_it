"""Cash-recovery ROI dashboard: vat_refund.recovery_dashboard buckets every claim into the six
readiness states and totals the north-star euros (built on the canonical claims_overview +
recovery_report); the /recovery-dashboard page renders them."""
import vat_refund as VR


def test_recovery_dashboard_shape_and_invariants():
    d = VR.recovery_dashboard("2026")
    # the six states are present and total to n_claims
    assert set(d["buckets"]) == set(VR.RECOVERY_STATES)
    assert sum(d["buckets"][s]["n"] for s in VR.RECOVERY_STATES) == d["n_claims"]
    # euros are non-negative and the derived totals are consistent with the buckets
    for s in VR.RECOVERY_STATES:
        assert d["buckets"][s]["eur"] >= 0 and d["buckets"][s]["n"] >= 0
    assert d["recovered_eur"] == d["buckets"]["paid"]["eur"]
    assert round(d["claimable_eur"], 2) == round(
        d["buckets"]["ready"]["eur"] + d["buckets"]["deadline"]["eur"], 2)
    assert d["deadline_risk"]["n"] == d["buckets"]["deadline"]["n"]


def test_recovery_dashboard_never_raises_on_bad_year():
    d = VR.recovery_dashboard("not-a-year")
    assert set(d["buckets"]) == set(VR.RECOVERY_STATES)
    assert d["n_claims"] >= 0


def test_recovery_dashboard_page_renders(client):
    r = client.get("/recovery-dashboard?year=2026")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Cash to recover" in body
    assert "Claims by readiness" in body
    assert "claimable now" in body and "deadline risk" in body
