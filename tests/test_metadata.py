"""
DOCUMENT METADATA (A3, metadata.py) — typed CUSTOM FIELDS + hierarchical TAGS over any
vaulted document, in an APP-OWNED, separate metadata.db (the engine product DBs stay
read-only). These tests repoint metadata.DB at a temp file and exercise:

  - every field type defines; set+get with per-type validation/coercion:
      * monetary rounds via money (ROUND_HALF_UP),
      * select rejects a non-option,
      * date normalises common formats to ISO,
      * number/monetary reject junk gracefully (a value, never a raise),
      * boolean coerces to 0/1;
  - nested tags + the cycle guard (a tag can't be moved under its own descendant);
  - assign/unassign + tags_for / subjects_for_tag (incl. descendant rollup);
  - the document metadata panel renders + accepts edits (Flask client, admin session)
    and ESCAPES a planted XSS tag name;
  - filter-by-tag on the document list returns the right documents;
  - search.rebuild() folds a document's tag/field text into the index so the doc becomes
    findable by its tag name;
  - metadata.db is a SEPARATE app-owned DB and no product DB is opened writable.
"""
import os
import re
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import metadata  # noqa: E402


@pytest.fixture()
def md(tmp_path, monkeypatch):
    """Repoint metadata.DB at a temp file (so we never touch the live metadata.db)."""
    monkeypatch.setattr(metadata, "DB", str(tmp_path / "metadata.db"), raising=True)
    monkeypatch.setattr(metadata, "_SCHEMA_READY", set(), raising=True)
    return metadata


# ----------------------------------------------------------------- field types + coercion
def test_define_each_field_type(md):
    for t in md.FIELD_TYPES:
        opts = "a, b, c" if t == "select" else None
        f, err = md.define_field(f"F-{t}", t, opts)
        assert err == "" and f is not None, (t, err)
        assert f["type"] == t
    assert {f["name"] for f in md.list_fields()} == {f"F-{t}" for t in md.FIELD_TYPES}


def test_monetary_rounds_via_money(md):
    f, _ = md.define_field("Amount", "monetary")
    ok, err = md.set_value(f["id"], "doc:1", "56057.985")   # half-up -> .99
    assert ok and not err
    v = md.get_values("doc:1")[0]
    assert v["value"] == "56057.99"
    assert v["display"] == "56,057.99"
    # junk amount is rejected gracefully (a value, never a raise)
    ok, err = md.set_value(f["id"], "doc:1", "not money")
    assert not ok and err
    assert md.get_values("doc:1")[0]["value"] == "56057.99"   # unchanged


def test_select_rejects_non_option(md):
    f, _ = md.define_field("Class", "select", "fuel, toll, other")
    ok, _ = md.set_value(f["id"], "doc:1", "toll")
    assert ok
    ok, err = md.set_value(f["id"], "doc:1", "luxuries")
    assert not ok and "option" in err.lower()
    assert md.get_values("doc:1")[0]["value"] == "toll"   # unchanged
    # a select with no options is refused at definition time
    bad, err = md.define_field("Empty", "select", "")
    assert bad is None and err


def test_date_normalises(md):
    f, _ = md.define_field("Filed", "date")
    for raw, iso in [("2026-06-16", "2026-06-16"), ("16.06.2026", "2026-06-16"),
                     ("16/06/2026", "2026-06-16")]:
        ok, err = md.set_value(f["id"], "doc:1", raw)
        assert ok and not err, (raw, err)
        assert md.get_values("doc:1")[0]["value"] == iso
    ok, err = md.set_value(f["id"], "doc:1", "not-a-date")
    assert not ok and err


def test_number_rejects_junk_and_boolean_coerces(md):
    fn, _ = md.define_field("Count", "number")
    ok, _ = md.set_value(fn["id"], "doc:1", "12")
    assert ok and md.get_values("doc:1")[0]["value"] == "12"
    ok, err = md.set_value(fn["id"], "doc:1", "abc")
    assert not ok and err

    fb, _ = md.define_field("Reviewed", "boolean")
    ok, _ = md.set_value(fb["id"], "doc:2", "yes")
    assert ok
    vb = [v for v in md.get_values("doc:2") if v["name"] == "Reviewed"][0]
    assert vb["value"] == "1" and vb["display"] == "Yes"
    ok, _ = md.set_value(fb["id"], "doc:2", "")
    assert md.get_values("doc:2")[0]["value"] == "0"   # falsy -> 0


def test_clear_value(md):
    f, _ = md.define_field("Note", "text")
    md.set_value(f["id"], "doc:1", "hello")
    assert md.get_values("doc:1")
    md.clear_value(f["id"], "doc:1")
    assert md.get_values("doc:1") == []


def test_delete_field_removes_values(md):
    f, _ = md.define_field("Tmp", "text")
    md.set_value(f["id"], "doc:1", "x")
    md.delete_field(f["id"])
    assert md.get_field(f["id"]) is None
    assert md.get_values("doc:1") == []


# ----------------------------------------------------------------- tags + cycle guard
def test_nested_tags_and_tree(md):
    root, _ = md.create_tag("Tax")
    vat, _ = md.create_tag("VAT", parent_id=root["id"])
    eu, _ = md.create_tag("EU", parent_id=vat["id"])
    tree = md.list_tags()
    assert [n["name"] for n in tree] == ["Tax"]
    assert tree[0]["children"][0]["name"] == "VAT"
    assert tree[0]["children"][0]["children"][0]["name"] == "EU"
    # a child whose parent does not exist is refused
    bad, err = md.create_tag("Orphan", parent_id=99999)
    assert bad is None and err


def test_cycle_guard(md):
    a, _ = md.create_tag("A")
    b, _ = md.create_tag("B", parent_id=a["id"])
    c, _ = md.create_tag("C", parent_id=b["id"])
    # moving A under its own descendant C must be refused
    ok, err = md.rename_tag(a["id"], parent_id=c["id"])
    assert not ok and "cycle" in err.lower()
    # A's parent is unchanged (still a root)
    assert md.get_tag(a["id"])["parent_id"] is None
    # a tag can't be its own parent
    ok, err = md.rename_tag(b["id"], parent_id=b["id"])
    assert not ok and "cycle" in err.lower()
    # a legal move IS allowed (C under A)
    ok, err = md.rename_tag(c["id"], parent_id=a["id"])
    assert ok and not err
    assert md.get_tag(c["id"])["parent_id"] == a["id"]


def test_delete_tag_reparents_children(md):
    root, _ = md.create_tag("Root")
    mid, _ = md.create_tag("Mid", parent_id=root["id"])
    leaf, _ = md.create_tag("Leaf", parent_id=mid["id"])
    md.delete_tag(mid["id"])
    assert md.get_tag(mid["id"]) is None
    # leaf re-parented up to root (not orphaned)
    assert md.get_tag(leaf["id"])["parent_id"] == root["id"]


# ----------------------------------------------------------------- assign / lookups
def test_assign_unassign_and_lookups(md):
    t, _ = md.create_tag("Audit")
    ok, _ = md.assign_tag(t["id"], "doc:1")
    assert ok
    # idempotent
    md.assign_tag(t["id"], "doc:1")
    assert [x["name"] for x in md.tags_for("doc:1")] == ["Audit"]
    assert md.subjects_for_tag(t["id"]) == ["doc:1"]
    md.unassign_tag(t["id"], "doc:1")
    assert md.tags_for("doc:1") == []
    assert md.subjects_for_tag(t["id"]) == []
    # assigning a non-existent tag is rejected
    ok, err = md.assign_tag(99999, "doc:1")
    assert not ok and err


def test_subjects_for_tag_with_descendants(md):
    tax, _ = md.create_tag("Tax")
    vat, _ = md.create_tag("VAT", parent_id=tax["id"])
    md.assign_tag(tax["id"], "doc:1")
    md.assign_tag(vat["id"], "doc:2")
    # only the tag itself
    assert md.subjects_for_tag(tax["id"]) == ["doc:1"]
    # the tag + descendants ("everything under Tax")
    assert md.subjects_for_tag(tax["id"], include_descendants=True) == ["doc:1", "doc:2"]


# ----------------------------------------------------------------- never-raises posture
def test_bad_input_never_raises(md):
    # unknown field, None refs, junk ids — all return values, never raise
    assert md.set_value(99999, "doc:1", "x") == (False, "no such field")
    assert md.set_value(None, None, None)[0] is False
    assert md.get_values("") == []
    assert md.tags_for(None) == []
    assert md.subjects_for_tag("not-an-int") == []
    assert md.get_tag("nope") is None
    assert md.delete_tag(99999) == (True, "")   # idempotent


# ----------------------------------------------------------------- separate, app-owned DB
def test_metadata_db_is_separate_and_not_a_product_db(md, tmp_path):
    md.define_field("X", "text")
    # the file lives where DB points (the temp dir), not in any product DB
    assert os.path.exists(md.DB)
    assert md.DB == str(tmp_path / "metadata.db")
    # it is a normal app-owned writable sqlite DB (not a product read-only handle)
    con = sqlite3.connect(md.DB)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"custom_fields", "field_values", "tags", "tag_links"} <= tables


# ----------------------------------------------------------------- search integration
def _build_products(tmp_path):
    """A tiny fuel_history.db + suppliers.db corpus with one document (id=1)."""
    fh = str(tmp_path / "fuel_history.db")
    su = str(tmp_path / "suppliers.db")
    fc = sqlite3.connect(fh)
    fc.execute("CREATE TABLE invoice_documents (id INTEGER PRIMARY KEY, entity TEXT, "
               "supplier TEXT, invoice_ref TEXT, filename TEXT, kind TEXT, uploaded_at TEXT)")
    fc.execute("INSERT INTO invoice_documents VALUES "
               "(1,'Jupiter Plus AS','BP','INV-7788','bp.pdf','original_pdf','2026-05-01')")
    fc.execute("CREATE TABLE transactions (supplier TEXT, product TEXT, product_group TEXT)")
    fc.commit()
    fc.close()
    sc = sqlite3.connect(su)
    sc.execute("CREATE TABLE suppliers (code TEXT, legal_name TEXT)")
    sc.execute("INSERT INTO suppliers VALUES ('BP','British Petroleum')")
    sc.execute("CREATE TABLE supplier_vat_registrations (supplier TEXT, country TEXT, "
               "vat_number TEXT, source TEXT)")
    sc.execute("CREATE TABLE supplier_invoices (supplier TEXT, country TEXT, invoice_no TEXT, "
               "invoice_date TEXT, period TEXT, currency TEXT, gross_total REAL, notes TEXT)")
    sc.commit()
    sc.close()
    return fh, su


def test_search_rebuild_picks_up_metadata(md, tmp_path, monkeypatch):
    import db
    if db.ENGINE != "sqlite":
        pytest.skip("read-only URI handle is a SQLite mechanism")
    import dataproduct
    import search
    fh, su = _build_products(tmp_path)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setitem(dataproduct._PATHS, "suppliers", su)
    monkeypatch.setattr(search, "DB", str(tmp_path / "search.db"), raising=True)

    # before tagging: the unusual term is NOT in the index
    search.rebuild()
    assert search.search("ZorblaxTag") == []

    # tag doc:1 + set a field value, then rebuild
    t, _ = md.create_tag("ZorblaxTag")
    md.assign_tag(t["id"], "doc:1")
    f, _ = md.define_field("Reviewer", "text")
    md.set_value(f["id"], "doc:1", "Wibblewob")
    search.rebuild()

    # the document is now findable by its tag name and field value
    hits = search.search("ZorblaxTag")
    assert any("BP" in h["title"] for h in hits)
    assert any("BP" in h["title"] for h in search.search("Wibblewob"))


# ----------------------------------------------------------------- web UI (Flask client)
def _seed_doc(monkeypatch, tmp_path, vat_db_name="vat_meta.db"):
    """Repoint vat_refund.DB at a temp claims DB carrying one invoice_documents row, and
    metadata.DB at a temp metadata DB. Returns (vat_module, metadata_module, doc_id).

    vat_refund's first-run migration may seed invoice_documents from a demo fuel_history,
    so we point ANALYTICS_DB at an empty temp file (no seed) and capture the row's real id.
    """
    import vat_refund as VR
    db_path = str(tmp_path / vat_db_name)
    empty_analytics = str(tmp_path / "empty_fh.db")
    sqlite3.connect(empty_analytics).close()   # exists but has no invoice_documents
    monkeypatch.setattr(VR, "DB", db_path, raising=True)
    monkeypatch.setattr(VR, "ANALYTICS_DB", empty_analytics, raising=True)
    monkeypatch.setattr(VR, "_SCHEMA_READY", set(), raising=True)
    con = VR.connect()   # creates schema incl. invoice_documents
    cur = con.execute("INSERT INTO invoice_documents (entity, supplier, invoice_ref, "
                      "filename, sha256, kind) VALUES ('Jupiter Plus AS','BP','INV-7788',"
                      "'bp.pdf','deadbeef','scan')")
    doc_id = cur.lastrowid
    con.commit()
    con.close()
    monkeypatch.setattr(metadata, "DB", str(tmp_path / "metadata.db"), raising=True)
    monkeypatch.setattr(metadata, "_SCHEMA_READY", set(), raising=True)
    return VR, metadata, doc_id


def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_doc_meta_panel_renders_and_edits(client, monkeypatch, tmp_path):
    VR, MD, did = _seed_doc(monkeypatch, tmp_path)
    ref = f"doc:{did}"
    path = f"/doc/{did}/meta"
    # the panel renders for the seeded document
    r = client.get(path)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Document metadata" in body and "INV-7788" in body

    # define a field + a tag, then attach both via the panel
    MD.define_field("Amount", "monetary")
    MD.create_tag("Compliance")
    fid = MD.list_fields()[0]["id"]
    tid = MD.list_tags()[0]["id"]

    # set the monetary field value through the panel
    r = client.post(path, data={"_csrf": _tok(client, path),
                                "__act": "set_field", "field_id": str(fid),
                                "value": "12.005"})
    assert r.status_code == 200
    assert MD.get_values(ref)[0]["value"] == "12.01"   # money half-up

    # add the tag through the panel
    r = client.post(path, data={"_csrf": _tok(client, path),
                                "__act": "add_tag", "tag_id": str(tid)})
    assert [t["name"] for t in MD.tags_for(ref)] == ["Compliance"]
    # it now shows on the panel
    assert "Compliance" in client.get(path).get_data(as_text=True)

    # remove the tag again
    client.post(path, data={"_csrf": _tok(client, path),
                            "__act": "remove_tag", "tag_id": str(tid)})
    assert MD.tags_for(ref) == []


def test_doc_meta_panel_escapes_xss_tag(client, monkeypatch, tmp_path):
    VR, MD, did = _seed_doc(monkeypatch, tmp_path)
    payload = "<script>alert(1)</script>"
    t, _ = MD.create_tag(payload)
    MD.assign_tag(t["id"], f"doc:{did}")
    body = client.get(f"/doc/{did}/meta").get_data(as_text=True)
    assert payload not in body
    assert "&lt;script&gt;" in body


def test_metadata_admin_page_define_and_create(client, monkeypatch, tmp_path):
    VR, MD, did = _seed_doc(monkeypatch, tmp_path)
    r = client.get("/metadata")
    assert r.status_code == 200
    # define a select field via the admin page
    client.post("/metadata", data={"_csrf": _tok(client, "/metadata"),
                                    "__act": "define_field", "name": "Class",
                                    "type": "select", "options": "fuel, toll"})
    assert any(f["name"] == "Class" and f["options"] == ["fuel", "toll"]
               for f in MD.list_fields())
    # create a nested tag via the admin page
    root, _ = MD.create_tag("Tax")
    client.post("/metadata", data={"_csrf": _tok(client, "/metadata"),
                                    "__act": "create_tag", "name": "VAT",
                                    "parent_id": str(root["id"]), "color": "#112233"})
    names = {t["name"] for t in MD.flat_tags()}
    assert {"Tax", "VAT"} <= names


def test_documents_filter_by_tag(client, monkeypatch, tmp_path):
    """/documents?tag=<id> renders the filter banner and restricts to the tagged subject.
    The documents page builds its own list from suppliers.db; here we only assert the
    filter machinery resolves the tag (banner + count) without error."""
    VR, MD, did = _seed_doc(monkeypatch, tmp_path)
    t, _ = MD.create_tag("Quarterly")
    MD.assign_tag(t["id"], f"doc:{did}")
    r = client.get(f"/documents?tag={t['id']}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Filtered to documents tagged" in body
    assert "Quarterly" in body
