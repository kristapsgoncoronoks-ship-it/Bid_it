"""Tests for validate.py — first coverage for the batch tie-out and commit gate.

The tie-out is an EUR-threshold decision (±0.02) and therefore must use the
accounting rounding from money.py (Decimal, ROUND_HALF_UP), not bare round()
(banker's). These tests pin the boundary behaviour.
"""
import json

import validate


def line(no="INV1", net=100.0, vat=21.0, country="Latvia", date="2026-05-31"):
    return {"invoice_no": no, "date": date, "country": country,
            "currency": "EUR", "net": net, "vat": vat}


# ------------------------------------------------------------- rounding basis
def test_gross_uses_half_up_not_bankers():
    # 56057.985 rounds to .99 under HALF_UP; bare round() gives .98 (banker's).
    r = validate.validate_batch([line(net=56057.985, vat=0)])
    assert r["gross"] == 56057.99


def test_gross_sums_exactly_in_decimal():
    # 0.1 + 0.2 != 0.3 in binary float; the decimal sum is exact.
    r = validate.validate_batch([line(net=0.1, vat=0.2)])
    assert r["gross"] == 0.30


def test_half_up_residue_changes_tie_verdict():
    # Old code: round(56057.985, 2) -> 56057.98, diff vs 56058.01 = -0.03 (FAIL).
    # HALF_UP gross is 56057.99, diff = -0.02 -> within tolerance (OK).
    r = validate.validate_batch([line(net=56057.985, vat=0)],
                                coversheet_total=56058.01)
    assert r["tie"]["gross"] == 56057.99
    assert r["tie"]["diff"] == -0.02
    assert r["tie"]["ok"] is True
    # ...and symmetrically, a stated total the old rounding would have accepted
    # is now correctly 0.03 out.
    r = validate.validate_batch([line(net=56057.985, vat=0)],
                                coversheet_total=56057.96)
    assert r["tie"]["diff"] == 0.03
    assert r["tie"]["ok"] is False


def test_per_line_005_residues_accumulate_exactly():
    # Two .005-residue lines: exact decimal sum is 200.01 + 0 VAT.
    r = validate.validate_batch([line(no="A", net=100.005, vat=0),
                                 line(no="B", net=100.005, vat=0)],
                                coversheet_total=200.01)
    assert r["tie"]["gross"] == 200.01
    assert r["tie"]["diff"] == 0.0
    assert r["tie"]["ok"] is True


# -------------------------------------------------------- tolerance boundary
def test_tie_ok_at_exactly_002():
    r = validate.validate_batch([line(net=100.00, vat=0)], coversheet_total=99.98)
    assert r["tie"]["diff"] == 0.02
    assert r["tie"]["ok"] is True
    r = validate.validate_batch([line(net=100.00, vat=0)], coversheet_total=100.02)
    assert r["tie"]["diff"] == -0.02
    assert r["tie"]["ok"] is True


def test_tie_fails_beyond_002():
    # stated totals are quantized to cents, so the first step past the
    # tolerance is a 3-cent diff; 0.021 stated quantizes back inside it.
    r = validate.validate_batch([line(net=100.00, vat=0)], coversheet_total=99.97)
    assert r["tie"]["diff"] == 0.03
    assert r["tie"]["ok"] is False
    r = validate.validate_batch([line(net=100.00, vat=0)],
                                coversheet_total=100.021)
    assert r["tie"]["stated"] == 100.02
    assert r["tie"]["ok"] is True


# ------------------------------------------------------------ can_commit gate
def test_can_commit_requires_no_errors_and_tie_ok():
    clean = line(net=100.0, vat=21.0)
    bad = line(no="", net=100.0, vat=21.0)  # missing invoice_no -> error
    # clean lines + tie ok -> commit allowed
    r = validate.validate_batch([clean], coversheet_total=121.00)
    assert r["errors"] == 0 and r["tie"]["ok"] and r["can_commit"] is True
    # clean lines but tie fails -> blocked
    r = validate.validate_batch([clean], coversheet_total=130.00)
    assert r["errors"] == 0 and r["tie"]["ok"] is False
    assert r["can_commit"] is False
    # line error blocks commit even when the tie is fine
    r = validate.validate_batch([bad], coversheet_total=121.00)
    assert r["errors"] == 1 and r["tie"]["ok"] is True
    assert r["can_commit"] is False


def test_no_coversheet_tie_is_none():
    r = validate.validate_batch([line(net=100.0, vat=21.0)])
    assert r["tie"] is None
    assert r["can_commit"] is True  # gate falls back to errors only
    r = validate.validate_batch([line(no="", net=100.0, vat=21.0)])
    assert r["tie"] is None and r["can_commit"] is False


# ----------------------------------------------------------- JSON/UI contract
def test_result_shape_stays_json_native():
    r = validate.validate_batch([line()], coversheet_total=121.0)
    assert isinstance(r["gross"], float)
    assert isinstance(r["tie"]["gross"], float)
    assert isinstance(r["tie"]["stated"], float)
    assert isinstance(r["tie"]["diff"], float)
    assert isinstance(r["tie"]["ok"], bool)
    json.dumps(r["tie"])  # must not raise (no Decimal leaks into the payload)
