"""
DOCUMENT RETENTION POLICIES + LEGAL HOLD (A5, retention.py) — the ADVISORY records-
management overlay (retention schedule + legal hold) over vaulted documents, in an
APP-OWNED, separate retention.db (the engine product DBs stay read-only; this module
never opens one writable). These tests repoint retention.DB at a temp file and exercise
the SAFETY INVARIANTS that are the load-bearing behaviour:

  - define policies (an 'all' policy + a tag-scoped policy); applies_to/action/basis
    validation; bad inputs rejected as a VALUE (never a raise);
  - retain_until math from EACH basis (doc_date / registered_date) + leap-day clamp;
  - a document PAST retention shows past_due and appears in due_for_review;
  - placing a LEGAL HOLD removes it from due_for_review and flips on_hold (legal hold
    OVERRIDES retention); releasing restores it;
  - LONGEST-retention-wins when an 'all' and a tag policy both apply;
  - tag-policy resolution via A3 tags;
  - never-raise on bad/missing dates;
  - hold place/release are AUDIT-logged (changed_by);
  - the /retention admin page + /retention/review worklist render and ESCAPE a planted
    XSS policy name / hold reason; the doc_meta legal-hold control places/releases a hold;
  - retention.db is a SEPARATE app-owned DB and NO code path deletes a vaulted document.
"""
import os
import re
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import retention  # noqa: E402


@pytest.fixture()
def ret(tmp_path, monkeypatch):
    """Repoint retention.DB at a temp DB (so we never touch the live retention.db). Also
    inject a deterministic document source so due_for_review never reads a product DB."""
    monkeypatch.setattr(retention, "DB", str(tmp_path / "retention.db"), raising=True)
    monkeypatch.setattr(retention, "_SCHEMA_READY", set(), raising=True)
    return retention


# ----------------------------------------------------------------- policy definition
def test_define_all_and_tag_policy(ret):
    p, err = ret.define_policy("EU VAT 10y", "all", retain_years=10, basis="doc_date")
    assert err == "" and p is not None
    assert p["applies_to"] == "all" and p["tag_id"] is None and p["retain_years"] == 10
    pt, err = ret.define_policy("Contracts 7y", "tag", tag_id=5, retain_years=7,
                                action="dispose_review", basis="registered_date")
    assert err == "" and pt is not None
    assert pt["applies_to"] == "tag" and pt["tag_id"] == 5 and pt["action"] == "dispose_review"
    names = {p["name"] for p in ret.list_policies()}
    assert names == {"EU VAT 10y", "Contracts 7y"}


def test_define_policy_validation_returns_value_not_raise(ret):
    # missing name / a tag policy without a tag / bad enums / negative years -> (None, err)
    assert ret.define_policy("")[0] is None
    assert ret.define_policy("x", "tag", tag_id=None)[0] is None
    assert ret.define_policy("x", "weird")[0] is None
    assert ret.define_policy("x", "all", action="delete_now")[0] is None
    assert ret.define_policy("x", "all", basis="random")[0] is None
    assert ret.define_policy("x", "all", retain_years=-1)[0] is None
    assert ret.define_policy("x", "all", retain_years="lots")[0] is None
    # an 'all' policy ignores any tag_id
    p, _ = ret.define_policy("ok", "all", tag_id=9)
    assert p["tag_id"] is None


def test_delete_policy_is_idempotent(ret):
    p, _ = ret.define_policy("P", "all")
    ok, _ = ret.delete_policy(p["id"])
    assert ok and ret.list_policies() == []
    ok, _ = ret.delete_policy(p["id"])   # idempotent
    assert ok


# ----------------------------------------------------------------- retain_until math
def test_retain_until_from_each_basis(ret):
    pol_doc = {"basis": "doc_date", "retain_years": 10}
    assert ret.retain_until(pol_doc, "2010-03-15").isoformat() == "2020-03-15"
    # registered_date basis uses the registered date, not the doc date
    pol_reg = {"basis": "registered_date", "retain_years": 7}
    assert ret.retain_until(pol_reg, "2010-01-01",
                            registered_date="2012-06-30").isoformat() == "2019-06-30"
    # leap-day clamp: 29 Feb + 1yr (non-leap target) -> 28 Feb
    assert ret.retain_until({"basis": "doc_date", "retain_years": 1},
                            "2020-02-29").isoformat() == "2021-02-28"


def test_retain_until_basis_fallback_and_bad_date(ret):
    # registered basis missing -> falls back to doc_date
    pol = {"basis": "registered_date", "retain_years": 10}
    assert ret.retain_until(pol, "2010-01-01", registered_date=None).isoformat() == "2020-01-01"
    # no usable date at all -> None (never raises)
    assert ret.retain_until(pol, None, None) is None
    assert ret.retain_until(pol, "not a date") is None


# ----------------------------------------------------------------- past_due / status
def test_past_due_status_and_within(ret):
    ret.define_policy("EU VAT 10y", "all", retain_years=10, basis="doc_date")
    old = ret.retention_status("doc:1", "2005-01-01", asof="2025-01-01")
    assert old["past_due"] is True and old["on_hold"] is False
    assert old["retain_until"] == "2015-01-01"
    assert old["days_remaining"] < 0
    fresh = ret.retention_status("doc:2", "2024-01-01", asof="2025-01-01")
    assert fresh["past_due"] is False and fresh["days_remaining"] > 0


def test_no_policy_means_not_due(ret):
    st = ret.retention_status("doc:1", "1990-01-01", asof="2025-01-01")
    assert st["policy"] is None and st["retain_until"] is None and st["past_due"] is False


def test_status_never_raises_on_bad_dates(ret):
    ret.define_policy("P", "all", retain_years=10)
    st = ret.retention_status("doc:1", None, asof="2025-01-01")
    assert st["past_due"] is False and st["retain_until"] is None
    st2 = ret.retention_status("doc:1", "garbage", asof="not a date either")
    assert st2["past_due"] is False


# ----------------------------------------------------------------- due_for_review + legal hold
def _docsrc(monkeypatch, docs):
    monkeypatch.setattr(retention, "_DOC_SOURCE", lambda: docs, raising=False)


def test_due_for_review_lists_past_due(ret, monkeypatch):
    ret.define_policy("EU VAT 10y", "all", retain_years=10, basis="doc_date")
    _docsrc(monkeypatch, [
        {"subject_ref": "doc:1", "doc_id": 1, "entity": "E", "supplier": "BP",
         "invoice_ref": "INV-1", "filename": "a.pdf",
         "doc_date": "2005-01-01", "registered_date": "2005-02-01"},
        {"subject_ref": "doc:2", "doc_id": 2, "entity": "E", "supplier": "BP",
         "invoice_ref": "INV-2", "filename": "b.pdf",
         "doc_date": "2024-01-01", "registered_date": "2024-02-01"}])
    due = ret.due_for_review(asof="2025-01-01")
    refs = {d["subject_ref"] for d in due}
    assert refs == {"doc:1"}   # only the old one is past retention


def test_legal_hold_overrides_retention(ret, monkeypatch):
    ret.define_policy("EU VAT 10y", "all", retain_years=10, basis="doc_date")
    _docsrc(monkeypatch, [
        {"subject_ref": "doc:1", "doc_id": 1, "entity": "E", "supplier": "BP",
         "invoice_ref": "INV-1", "filename": "a.pdf",
         "doc_date": "2005-01-01", "registered_date": "2005-02-01"}])
    # past due before any hold
    assert {d["subject_ref"] for d in ret.due_for_review(asof="2025-01-01")} == {"doc:1"}
    assert ret.is_on_hold("doc:1") is False

    h, err = ret.place_hold("doc:1", "litigation X", "alice")
    assert err == "" and h is not None
    assert ret.is_on_hold("doc:1") is True
    # legal hold OVERRIDES retention -> removed from the worklist + status not past_due
    assert ret.due_for_review(asof="2025-01-01") == []
    st = ret.retention_status("doc:1", "2005-01-01", asof="2025-01-01")
    assert st["on_hold"] is True and st["past_due"] is False

    # releasing restores the document to the worklist
    ok, err = ret.release_hold(h["id"], "bob")
    assert ok and err == ""
    assert ret.is_on_hold("doc:1") is False
    assert {d["subject_ref"] for d in ret.due_for_review(asof="2025-01-01")} == {"doc:1"}


def test_place_hold_idempotent(ret):
    h1, _ = ret.place_hold("doc:7", "r1", "alice")
    h2, _ = ret.place_hold("doc:7", "r2", "alice")
    assert h1["id"] == h2["id"]   # one active hold per subject
    active = [h for h in ret.holds_for("doc:7") if h["released_at"] is None]
    assert len(active) == 1


# ----------------------------------------------------------------- policy resolution
def test_longest_retention_wins_all_vs_tag(ret, monkeypatch):
    # an 'all' policy of 10y and a tag policy of 5y both apply to doc:1; the SAFEST
    # (longest) retention must win — i.e. 10y here, even though tag normally "wins".
    pa, _ = ret.define_policy("All 10y", "all", retain_years=10, basis="doc_date")
    pt, _ = ret.define_policy("Tag 5y", "tag", tag_id=5, retain_years=5, basis="doc_date")
    # doc:1 carries tag 5
    monkeypatch.setattr(retention, "_DOC_SOURCE", None, raising=False)
    import metadata as MD
    monkeypatch.setattr(MD, "tags_for", lambda ref: [{"id": 5}] if ref == "doc:1" else [])
    st = ret.retention_status("doc:1", "2010-01-01", asof="2025-01-01")
    # 10y (the longer) wins -> retain_until 2020, NOT 2015
    assert st["retain_until"] == "2020-01-01"
    assert st["policy"]["name"] == "All 10y"


def test_tag_policy_wins_over_all_when_longer(ret, monkeypatch):
    # tag policy 12y vs all 10y -> tag wins (it is both tag-scoped AND longest)
    ret.define_policy("All 10y", "all", retain_years=10)
    ret.define_policy("Tag 12y", "tag", tag_id=5, retain_years=12)
    import metadata as MD
    monkeypatch.setattr(MD, "tags_for", lambda ref: [{"id": 5}])
    st = ret.retention_status("doc:1", "2010-01-01", asof="2025-01-01")
    assert st["policy"]["name"] == "Tag 12y" and st["retain_until"] == "2022-01-01"


def test_tag_policy_resolution_only_for_tagged_docs(ret, monkeypatch):
    ret.define_policy("Tag only 5y", "tag", tag_id=5, retain_years=5)
    import metadata as MD
    monkeypatch.setattr(MD, "tags_for",
                        lambda ref: [{"id": 5}] if ref == "doc:tagged" else [])
    tagged = ret.retention_status("doc:tagged", "2010-01-01", asof="2025-01-01")
    untagged = ret.retention_status("doc:other", "2010-01-01", asof="2025-01-01")
    assert tagged["policy"] is not None and tagged["retain_until"] == "2015-01-01"
    assert untagged["policy"] is None   # no 'all' fallback exists


# ----------------------------------------------------------------- audit + isolation
def test_hold_place_and_release_are_audited(ret):
    h, _ = ret.place_hold("doc:9", "audit me", "carol")
    ret.release_hold(h["id"], "dave")
    con = sqlite3.connect(ret.DB)
    try:
        rows = con.execute(
            "SELECT action, changed_by FROM audit_log WHERE tbl='legal_holds' "
            "AND action<>'BASELINE' ORDER BY id").fetchall()
    finally:
        con.close()
    actions = [(r[0], r[1]) for r in rows]
    assert ("INSERT", "carol") in actions   # place_hold attributed to carol
    assert ("UPDATE", "dave") in actions     # release_hold attributed to dave


def test_retention_db_is_separate_and_not_a_product_db(ret, tmp_path):
    ret.define_policy("P", "all")
    assert ret.DB == str(tmp_path / "retention.db")
    con = sqlite3.connect(ret.DB)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"retention_policies", "legal_holds"} <= tables


def test_no_code_path_deletes_a_vaulted_document(ret):
    # The module's source must contain NO document/byte-deletion: it only ever DELETEs its
    # OWN overlay rows (policies). Assert no DELETE touches invoice_documents / the vault,
    # and the module never imports document_vault for a delete.
    src = open(os.path.join(WORKDIR, "retention.py")).read()
    assert "invoice_documents" in src   # it reads the index...
    # ...but only via SELECT — never DELETE/UPDATE/INSERT against it
    assert not re.search(r"(DELETE|UPDATE|INSERT)[^\n;]*invoice_documents", src, re.I)
    # the only DELETE statements target the overlay tables
    for m in re.finditer(r"DELETE FROM (\w+)", src):
        assert m.group(1) in ("retention_policies", "legal_holds")
    # never removes vault bytes
    assert "os.remove" not in src and "document_vault" not in src


# ----------------------------------------------------------------- web UI (Flask client)
@pytest.fixture()
def webret(tmp_path, monkeypatch):
    """Repoint retention.DB for the Flask client tests."""
    monkeypatch.setattr(retention, "DB", str(tmp_path / "retention.db"), raising=True)
    monkeypatch.setattr(retention, "_SCHEMA_READY", set(), raising=True)
    return retention


def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_retention_admin_page_renders_and_escapes_xss(client, webret):
    payload = "<script>alert('policy')</script>"
    webret.define_policy(payload, "all", retain_years=10)
    r = client.get("/retention")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Retention policies" in body
    assert payload not in body and "&lt;script&gt;" in body


def test_retention_admin_define_via_post(client, webret):
    tok = _tok(client, "/retention")
    r = client.post("/retention", data={
        "_csrf": tok, "__act": "define_policy", "name": "VAT 10y",
        "applies_to": "all", "retain_years": "10", "basis": "doc_date",
        "action": "review"})
    assert r.status_code == 200
    assert any(p["name"] == "VAT 10y" for p in webret.list_policies())


def test_retention_review_page_renders_and_escapes(client, webret, monkeypatch):
    webret.define_policy("EU VAT 10y", "all", retain_years=10, basis="doc_date")
    monkeypatch.setattr(retention, "_DOC_SOURCE", lambda: [
        {"subject_ref": "doc:1", "doc_id": 1, "entity": "E",
         "supplier": "<b>BP</b>", "invoice_ref": "INV-1", "filename": "a.pdf",
         "doc_date": "2005-01-01", "registered_date": "2005-02-01"}], raising=False)
    r = client.get("/retention/review")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Retention review" in body
    assert "/doc/1" in body                    # links to the document
    assert "<b>BP</b>" not in body and "&lt;b&gt;BP&lt;/b&gt;" in body  # escaped


def test_review_excludes_held_documents(client, webret, monkeypatch):
    webret.define_policy("EU VAT 10y", "all", retain_years=10, basis="doc_date")
    monkeypatch.setattr(retention, "_DOC_SOURCE", lambda: [
        {"subject_ref": "doc:1", "doc_id": 1, "entity": "E", "supplier": "BP",
         "invoice_ref": "INV-1", "filename": "held.pdf",
         "doc_date": "2005-01-01", "registered_date": "2005-02-01"}], raising=False)
    webret.place_hold("doc:1", "litigation", "alice")
    body = client.get("/retention/review").get_data(as_text=True)
    assert "held.pdf" not in body   # a held doc never appears in the worklist
    assert "review queue is empty" in body
