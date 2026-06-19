"""Tie-out HARD BLOCK at the interactive /extract/confirm gate.

When the extraction parsed a document/coversheet GROSS total (net+VAT) onto the draft,
the confirm route threads it into validate.validate_batch as `coversheet_total`, so a
mis-keyed line total cannot be confirmed (a hard block, no override). When NO total was
parsed, the gate is byte-identical to before (no tie, no block).

Two layers are tested:
  * validate.validate_batch — the tie basis (sum(net+vat)) and the can_commit gate.
  * the /extract/confirm web route — that the stashed draft's coversheet_total drives a
    real block, and a tie allows the commit; absent total never blocks.
"""
import os
import re

import app as A
import validate as VAL


# the tie basis is GROSS = sum(net + vat) (validate.validate_batch line 102/107)
def test_validate_batch_tie_basis_is_net_plus_vat():
    lines = [{"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium",
              "net": 1000.0, "vat": 210.0}]
    r = VAL.validate_batch(lines, coversheet_total=1210.0)   # net+vat
    assert r["tie"] is not None and r["tie"]["ok"] is True
    assert r["can_commit"] is True
    # the same lines tied against the NET-only total must FAIL (proves net+vat basis)
    r2 = VAL.validate_batch(lines, coversheet_total=1000.0)
    assert r2["tie"]["ok"] is False and r2["can_commit"] is False


def test_validate_batch_tolerance_and_absent_total():
    lines = [{"invoice_no": "BE001", "country": "Belgium", "net": 1000.0, "vat": 210.0}]
    # within the ~0.02 tolerance -> ties
    assert VAL.validate_batch(lines, coversheet_total=1210.02)["tie"]["ok"] is True
    # just outside -> fails
    assert VAL.validate_batch(lines, coversheet_total=1210.05)["tie"]["ok"] is False
    # no total -> no tie, no block (byte-identical to before)
    r = VAL.validate_batch(lines)
    assert r["tie"] is None and r["can_commit"] is True


# --------------------------------------------------------------------------- web route
def _csrf(client, path="/extract"):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def _stash(token, coversheet_total=None):
    draft = {"supplier": "DKV", "statement_ref": "S-TIE", "lines":
             [{"invoice_no": "BE001", "country": "Belgium", "net": 1000.0, "vat": 210.0}]}
    if coversheet_total is not None:
        draft["coversheet_total"] = coversheet_total
    A._stash_draft(token, draft)


def _drop(token):
    for ext in (".draft.json", ".pkl"):
        p = os.path.join(A.WORKDIR, ".extract_tmp", token + ext)
        try:
            os.unlink(p)
        except OSError:
            pass


def _confirm_form(client, net="1000", vat="210", token="aaaaaaaaaaaaaaaa"):
    return {
        "_csrf": _csrf(client), "token": token, "nlines": "1",
        "supplier": "DKV", "period": "2026-05", "stmt_ref": "S-TIE",
        "stmt_date": "2026-05-31", "customer": "OUR ENTITY",
        "inv_0": "BE001", "date_0": "2026-05-31", "ctry_0": "Belgium",
        "ccy_0": "EUR", "net_0": net, "vat_0": vat,
    }


def test_confirm_blocks_when_lines_do_not_tie_to_total(client):
    token = "00000000000000b1"
    _stash(token, coversheet_total=1210.0)             # document says gross 1210
    try:
        # operator mis-keys VAT so the line sum is 1090, not 1210 -> HARD BLOCK
        form = _confirm_form(client, net="1000", vat="90", token=token)
        r = client.post("/extract/confirm", data=form)
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "Commit blocked" in body
        assert "Tie-out FAILED" in body
        # the verdict shows the captured sum, the stated total, and the difference
        assert "1,090.00" in body and "1,210.00" in body
        # the commit was refused -> nothing queued for registration
        assert "queued for registration" not in body
    finally:
        _drop(token)


def test_confirm_allows_when_lines_tie_to_total(client):
    token = "00000000000000c2"
    _stash(token, coversheet_total=1210.0)
    try:
        form = _confirm_form(client, net="1000", vat="210", token=token)
        r = client.post("/extract/confirm", data=form)
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "queued for registration" in body
        assert "Tie-out FAILED" not in body
    finally:
        _drop(token)


def test_confirm_allows_when_no_total_present(client):
    # clean line, NO parsed total -> no tie-out gate, confirms as before
    token = "00000000000000d3"
    _stash(token, coversheet_total=None)
    try:
        form = _confirm_form(client, net="1000", vat="210", token=token)
        r = client.post("/extract/confirm", data=form)
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "queued for registration" in body
        assert "Tie-out FAILED" not in body
    finally:
        _drop(token)
