"""
DOCUMENT VERSIONING (A4, versioning.py) — a logical document's ORDERED, append-only
chain of versions, in an APP-OWNED, separate versions.db (the engine product DBs stay
read-only; this module never opens one writable). These tests repoint versioning.DB at
a temp file and versioning.DOCDIR at a temp vault, and exercise:

  - record_initial seeds version 1 and is IDEMPOTENT (a second call is a no-op);
  - add_version increments the version_no, marks the prior current SUPERSEDED, and keeps
    the whole history;
  - versions_for ordering (newest first) + is_current flag; current() correctness;
  - the UPLOAD byte-ingestion path vaults new bytes via document_vault and get_version_bytes
    reads exactly those bytes back;
  - the LINK-EXISTING path records an already-vaulted locator as a new version;
  - revert_to records a NEW version pointing at an OLD version's bytes WITHOUT losing the
    intervening history;
  - never-raise posture on bad input;
  - versions.db is a SEPARATE app-owned DB and no product DB is opened writable;
  - the /doc/<id>/versions UI renders the chain, accepts an upload + a revert, serves a
    specific version's bytes, and ESCAPES a planted XSS note.
"""
import os
import re
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import versioning  # noqa: E402
import document_vault  # noqa: E402


@pytest.fixture()
def ver(tmp_path, monkeypatch):
    """Repoint versioning.DB at a temp DB and DOCDIR at a temp vault (so we never touch
    the live versions.db or the live document store)."""
    monkeypatch.setattr(versioning, "DB", str(tmp_path / "versions.db"), raising=True)
    monkeypatch.setattr(versioning, "_SCHEMA_READY", set(), raising=True)
    monkeypatch.setattr(versioning, "DOCDIR", str(tmp_path / "vault"), raising=True)
    return versioning


def _vault_a_file(tmp_path, name, data):
    """Vault `data` directly (the original-attach path) and return its locator."""
    docdir = str(tmp_path / "vault")
    loc, _ = document_vault.copy_to(name, data, docdir)
    return loc


# ----------------------------------------------------------------- record_initial
def test_record_initial_seeds_v1_and_is_idempotent(ver, tmp_path):
    loc = _vault_a_file(tmp_path, "orig/original.pdf", b"%PDF original")
    v1, err = ver.record_initial("doc:1", loc, sha256="abc", size=13, actor="alice")
    assert err == "" and v1 is not None
    assert v1["version_no"] == 1 and v1["superseded"] == 0
    assert v1["vault_locator"] == loc and v1["created_by"] == "alice"
    # a second call is a NO-OP (no duplicate v1), returns the current
    again, err = ver.record_initial("doc:1", loc, sha256="abc", size=13, actor="bob")
    assert err == "" and again["version_no"] == 1
    assert len(ver.versions_for("doc:1")) == 1
    # missing inputs are rejected gracefully (a value, never a raise)
    assert ver.record_initial("", loc)[0] is None
    assert ver.record_initial("doc:1", "")[0] is None


# ----------------------------------------------------------------- add_version + supersede
def test_add_version_increments_supersedes_and_keeps_history(ver, tmp_path):
    loc = _vault_a_file(tmp_path, "orig/o.pdf", b"%PDF v1 bytes")
    ver.record_initial("doc:7", loc, sha256="s1", size=12)
    v2, err = ver.add_version("doc:7", new_bytes=b"%PDF v2 bytes", note="fixed total",
                              actor="alice")
    assert err == "" and v2["version_no"] == 2 and v2["superseded"] == 0
    v3, err = ver.add_version("doc:7", new_bytes=b"%PDF v3 bytes", actor="alice")
    assert err == "" and v3["version_no"] == 3
    chain = ver.versions_for("doc:7")
    # newest first, all three present (history kept)
    assert [c["version_no"] for c in chain] == [3, 2, 1]
    # exactly one current (the newest); the rest superseded
    assert [c["is_current"] for c in chain] == [True, False, False]
    assert chain[0]["superseded"] == 0
    assert all(c["superseded"] == 1 for c in chain[1:])


def test_add_version_seeds_v1_when_no_chain(ver):
    # add_version with no prior chain lands version 1 from this call's bytes
    v1, err = ver.add_version("doc:new", new_bytes=b"hello", note="first")
    assert err == "" and v1["version_no"] == 1
    assert ver.current("doc:new")["version_no"] == 1


# ----------------------------------------------------------------- current() correctness
def test_current_is_the_newest(ver):
    ver.add_version("doc:9", new_bytes=b"a")
    ver.add_version("doc:9", new_bytes=b"b")
    cur = ver.current("doc:9")
    assert cur["version_no"] == 2
    assert ver.current("doc:absent") is None


# ----------------------------------------------------------------- byte ingestion (upload)
def test_upload_path_vaults_bytes_and_reads_back(ver):
    v, err = ver.add_version("doc:5", new_bytes=b"%PDF brand new", filename="scan.pdf")
    assert err == "" and v["vault_locator"]
    # the bytes are vaulted (a file exists at the locator) and read back exactly
    data, err = ver.get_version_bytes(v["id"])
    assert err == "" and data == b"%PDF brand new"
    # sha + size recorded from the vaulted bytes
    import hashlib
    assert v["sha256"] == hashlib.sha256(b"%PDF brand new").hexdigest()
    assert v["size"] == len(b"%PDF brand new")


# ----------------------------------------------------------------- link-existing path
def test_link_existing_locator_as_new_version(ver, tmp_path):
    loc = _vault_a_file(tmp_path, "orig/o.pdf", b"%PDF original")
    ver.record_initial("doc:3", loc)
    other = _vault_a_file(tmp_path, "other/already.pdf", b"%PDF already vaulted")
    v2, err = ver.add_version("doc:3", vault_locator=other, note="linked existing")
    assert err == "" and v2["version_no"] == 2 and v2["vault_locator"] == other
    data, err = ver.get_version_bytes(v2["id"])
    assert err == "" and data == b"%PDF already vaulted"


# ----------------------------------------------------------------- revert
def test_revert_records_new_version_without_losing_history(ver, tmp_path):
    loc = _vault_a_file(tmp_path, "orig/o.pdf", b"%PDF v1 original")
    v1, _ = ver.record_initial("doc:42", loc, sha256="s1", size=16)
    v2, _ = ver.add_version("doc:42", new_bytes=b"%PDF v2 changed")
    rv, err = ver.revert_to(v1["id"], actor="alice")
    assert err == "" and rv["version_no"] == 3
    # the new current points at v1's BYTES (same locator) and records the source
    assert rv["vault_locator"] == v1["vault_locator"]
    assert rv["reverted_from"] == v1["id"]
    # history intact: v1, v2, v3 all present; v3 current
    chain = ver.versions_for("doc:42")
    assert [c["version_no"] for c in chain] == [3, 2, 1]
    assert chain[0]["is_current"] is True
    # reverting reads back v1's original bytes
    data, _ = ver.get_version_bytes(rv["id"])
    assert data == b"%PDF v1 original"
    # reverting to a non-existent version is rejected gracefully
    assert ver.revert_to(999999)[0] is None


# ----------------------------------------------------------------- never-raise posture
def test_bad_input_never_raises(ver):
    assert ver.add_version("doc:1")[0] is None          # no bytes, no locator
    assert ver.add_version(None, new_bytes=b"x")[0] is None
    assert ver.versions_for("") == []
    assert ver.versions_for(None) == []
    assert ver.current(None) is None
    assert ver.get_version("not-an-int") is None
    assert ver.get_version_bytes(999999) == (None, "no such version")
    assert ver.has_chain("doc:none") is False


# ----------------------------------------------------------------- separate, app-owned DB
def test_versions_db_is_separate_and_not_a_product_db(ver, tmp_path):
    ver.add_version("doc:1", new_bytes=b"x")
    assert os.path.exists(ver.DB)
    assert ver.DB == str(tmp_path / "versions.db")
    # a normal app-owned writable sqlite DB (not a product read-only handle)
    con = sqlite3.connect(ver.DB)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "doc_versions" in tables


# ----------------------------------------------------------------- web UI (Flask client)
def _seed_doc(monkeypatch, tmp_path, vat_db_name="vat_ver.db"):
    """Repoint vat_refund.DB at a temp claims DB carrying one invoice_documents row whose
    original bytes are vaulted in a temp vault, and versioning.DB/DOCDIR at temp paths.
    Returns (vat_module, versioning_module, doc_id, original_locator)."""
    import vat_refund as VR
    db_path = str(tmp_path / vat_db_name)
    empty_analytics = str(tmp_path / "empty_fh.db")
    sqlite3.connect(empty_analytics).close()   # exists but has no invoice_documents
    monkeypatch.setattr(VR, "DB", db_path, raising=True)
    monkeypatch.setattr(VR, "ANALYTICS_DB", empty_analytics, raising=True)
    monkeypatch.setattr(VR, "_SCHEMA_READY", set(), raising=True)

    # repoint the versioning overlay + vault
    vault = str(tmp_path / "vault")
    monkeypatch.setattr(versioning, "DB", str(tmp_path / "versions.db"), raising=True)
    monkeypatch.setattr(versioning, "_SCHEMA_READY", set(), raising=True)
    monkeypatch.setattr(versioning, "DOCDIR", vault, raising=True)

    # vault the original bytes, then register the invoice_documents row pointing at them
    loc, _ = document_vault.copy_to("orig/bp.pdf", b"%PDF the original invoice", vault)
    con = VR.connect()
    cur = con.execute(
        "INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename, "
        "stored_path, sha256, size, kind) VALUES "
        "('Jupiter Plus AS','BP','INV-7788','bp.pdf',?,?,?,'scan')",
        (loc, "deadbeef", 24))
    doc_id = cur.lastrowid
    con.commit()
    con.close()
    return VR, versioning, doc_id, loc


def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_versions_panel_renders_and_seeds_original(client, monkeypatch, tmp_path):
    VR, VER, did, loc = _seed_doc(monkeypatch, tmp_path)
    path = f"/doc/{did}/versions"
    r = client.get(path)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Document versions" in body and "INV-7788" in body
    # version 1 was lazily seeded from the original vaulted bytes
    chain = VER.versions_for(f"doc:{did}")
    assert len(chain) == 1 and chain[0]["version_no"] == 1
    assert chain[0]["vault_locator"] == loc
    assert "v1" in body and "current" in body


def test_versions_panel_upload_revert_and_download(client, monkeypatch, tmp_path):
    import io
    VR, VER, did, loc = _seed_doc(monkeypatch, tmp_path)
    path = f"/doc/{did}/versions"
    ref = f"doc:{did}"
    client.get(path)   # seed v1

    # upload a NEW version through the panel
    r = client.post(path, data={
        "_csrf": _tok(client, path), "__act": "upload_version",
        "note": "corrected totals",
        "file": (io.BytesIO(b"%PDF version two"), "bp_v2.pdf")},
        content_type="multipart/form-data")
    assert r.status_code == 200
    chain = VER.versions_for(ref)
    assert [c["version_no"] for c in chain] == [2, 1]
    assert chain[0]["is_current"] is True and chain[1]["superseded"] == 1

    # download v2's bytes through the version download route
    v2_id = chain[0]["id"]
    rd = client.get(f"/doc/{did}/version/{v2_id}?dl=1")
    assert rd.status_code == 200 and rd.data == b"%PDF version two"
    # view v1's bytes (the original)
    v1_id = chain[1]["id"]
    rv = client.get(f"/doc/{did}/version/{v1_id}")
    assert rv.status_code == 200 and rv.data == b"%PDF the original invoice"

    # MAKE CURRENT (revert) to v1 -> records v3 pointing at v1's bytes; history kept
    r = client.post(path, data={"_csrf": _tok(client, path), "__act": "revert",
                                "version_id": str(v1_id)})
    assert r.status_code == 200
    chain = VER.versions_for(ref)
    assert [c["version_no"] for c in chain] == [3, 2, 1]
    cur = chain[0]
    assert cur["is_current"] is True and cur["reverted_from"] == v1_id
    data, _ = VER.get_version_bytes(cur["id"])
    assert data == b"%PDF the original invoice"


def test_version_download_rejects_foreign_version(client, monkeypatch, tmp_path):
    """A version id that belongs to a DIFFERENT document's chain can't be read via this
    doc's URL (the subject_ref must match)."""
    VR, VER, did, loc = _seed_doc(monkeypatch, tmp_path)
    other, _ = VER.add_version("doc:99999", new_bytes=b"someone elses bytes")
    r = client.get(f"/doc/{did}/version/{other['id']}")
    assert r.status_code == 404


def test_versions_panel_escapes_xss_note(client, monkeypatch, tmp_path):
    VR, VER, did, loc = _seed_doc(monkeypatch, tmp_path)
    payload = "<script>alert(1)</script>"
    VER.add_version(f"doc:{did}", new_bytes=b"x", note=payload)
    body = client.get(f"/doc/{did}/versions").get_data(as_text=True)
    assert payload not in body
    assert "&lt;script&gt;" in body
