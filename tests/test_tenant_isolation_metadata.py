"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for metadata.py (custom fields + tags).

Behind the `multitenant` switch a tenant's custom fields, field values, hierarchical tags
and tag links are isolated:
  * WRITES stamp the bound tenant (tenancy.write_tenant());
  * READS filter by tenancy.scope_clause(): as tenant A list_fields / get_field / get_values /
    subjects_for_field_value / get_tag / _all_tags / list_tags / tags_for / subjects_for_tag
    see ONLY A's rows — B is ABSENT. The platform OWNER sees BOTH.

Crucially a tenant cannot SET a value on (or assign) another tenant's field/tag: set_value
and assign_tag validate via the scoped get_field / get_tag, so a foreign field/tag id reads
as "no such field/tag".

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads are
unscoped — byte-identical to today.
"""
import importlib

import pytest


@pytest.fixture()
def md(tmp_path, monkeypatch):
    import auth
    import tenancy
    import metadata
    importlib.reload(metadata)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(metadata, "DB", str(tmp_path / "metadata.db"))
    metadata._SCHEMA_READY.clear()

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield metadata, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(metadata, tenancy):
    out = {}
    for t in ("A", "B"):
        tenancy.set_tenant(t)
        field, _ = metadata.define_field(f"Amount {t}", "monetary")
        metadata.set_value(field["id"], f"doc:{t}", "12.34")
        root, _ = metadata.create_tag(f"Tax {t}")
        child, _ = metadata.create_tag(f"VAT {t}", parent_id=root["id"])
        metadata.assign_tag(child["id"], f"doc:{t}")
        out[t] = {"field": field, "root": root, "child": child}
    tenancy.reset_tenant()
    return out


# ── WRITE stamping ──────────────────────────────────────────────────────────────
def test_writes_stamp_the_bound_tenant(md):
    metadata, tenancy = md
    _seed_two_tenants(metadata, tenancy)
    tenancy.set_owner_scope()
    con = metadata.connect()
    try:
        fields = {r["name"]: r["tenant_id"]
                  for r in con.execute("SELECT name, tenant_id FROM custom_fields")}
        tags = {r["name"]: r["tenant_id"]
                for r in con.execute("SELECT name, tenant_id FROM tags")}
        vals = {r["tenant_id"] for r in con.execute("SELECT tenant_id FROM field_values")}
        links = {r["tenant_id"] for r in con.execute("SELECT tenant_id FROM tag_links")}
    finally:
        con.close()
    assert fields == {"Amount A": "A", "Amount B": "B"}
    assert tags == {"Tax A": "A", "VAT A": "A", "Tax B": "B", "VAT B": "B"}
    assert vals == {"A", "B"}
    assert links == {"A", "B"}


# ── READ isolation ──────────────────────────────────────────────────────────────
def test_tenant_a_sees_only_its_metadata(md):
    metadata, tenancy = md
    seeded = _seed_two_tenants(metadata, tenancy)
    tenancy.set_tenant("A")
    assert {f["name"] for f in metadata.list_fields()} == {"Amount A"}
    assert metadata.get_field(seeded["A"]["field"]["id"]) is not None
    assert metadata.get_field(seeded["B"]["field"]["id"]) is None
    # A sees its own document's value; B's doc has none for A
    assert [v["name"] for v in metadata.get_values("doc:A")] == ["Amount A"]
    assert metadata.get_values("doc:B") == []
    # tag tree scoped
    assert {t["name"] for t in metadata.list_tags()} == {"Tax A"}     # roots only
    assert metadata.get_tag(seeded["B"]["root"]["id"]) is None
    assert {t["name"] for t in metadata.tags_for("doc:A")} == {"VAT A"}
    assert metadata.tags_for("doc:B") == []
    assert metadata.subjects_for_tag(seeded["A"]["root"]["id"],
                                     include_descendants=True) == ["doc:A"]
    # a tenant cannot SET a value on / assign a foreign field/tag
    ok, msg = metadata.set_value(seeded["B"]["field"]["id"], "doc:A", "9.99")
    assert ok is False and "no such field" in msg
    ok, msg = metadata.assign_tag(seeded["B"]["child"]["id"], "doc:A")
    assert ok is False and "no such tag" in msg


def test_tenant_b_sees_only_its_metadata(md):
    metadata, tenancy = md
    seeded = _seed_two_tenants(metadata, tenancy)
    tenancy.set_tenant("B")
    assert {f["name"] for f in metadata.list_fields()} == {"Amount B"}
    assert metadata.get_field(seeded["A"]["field"]["id"]) is None
    assert {t["name"] for t in metadata.tags_for("doc:B")} == {"VAT B"}
    assert metadata.tags_for("doc:A") == []


# ── OWNER cross-tenant scope ────────────────────────────────────────────────────
def test_owner_scope_sees_both_tenants(md):
    metadata, tenancy = md
    _seed_two_tenants(metadata, tenancy)
    tenancy.set_owner_scope()
    assert {f["name"] for f in metadata.list_fields()} == {"Amount A", "Amount B"}
    assert {t["name"] for t in metadata.list_tags()} == {"Tax A", "Tax B"}


# ── OFF regression ──────────────────────────────────────────────────────────────
def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import metadata
    importlib.reload(metadata)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(metadata, "DB", str(tmp_path / "metadata.db"))
    metadata._SCHEMA_READY.clear()
    assert tenancy.multitenant_enabled() is False

    tenancy.set_tenant("A")          # inert while OFF
    field, _ = metadata.define_field("Amount", "monetary")
    metadata.set_value(field["id"], "doc:1", "5.00")
    tag, _ = metadata.create_tag("Tax")
    metadata.assign_tag(tag["id"], "doc:1")
    tenancy.reset_tenant()

    con = metadata.connect()
    try:
        assert con.execute("SELECT tenant_id FROM custom_fields WHERE id=?",
                           (field["id"],)).fetchone()["tenant_id"] == "default"
    finally:
        con.close()
    tenancy.set_tenant("ZZZ")
    try:
        assert {f["name"] for f in metadata.list_fields()} == {"Amount"}
        assert [v["name"] for v in metadata.get_values("doc:1")] == ["Amount"]
        assert {t["name"] for t in metadata.tags_for("doc:1")} == {"Tax"}
    finally:
        tenancy.reset_tenant()
