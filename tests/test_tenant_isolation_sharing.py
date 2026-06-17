"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for sharing.py (share links + data rooms).

Behind the `multitenant` switch, a tenant's share links, views, NDA agreements, data
rooms, room documents, room links, page-engagement analytics and Q&A are isolated:

  * WRITES stamp the bound tenant (tenancy.write_tenant()), so a row created "as tenant A"
    carries tenant_id='A';
  * READS filter by tenancy.scope_clause(): as tenant A the link/room lists, by-id lookups,
    view counts, agreements and room engagement see ONLY A's rows — B is ABSENT (the
    GDPR/no-bleed proof). The platform OWNER sees BOTH (the audited analytics exception).

THE PUBLIC-ROUTE TOKEN BINDING. The unguessable token lookups get_by_token /
get_room_link_by_token are DELIBERATELY unscoped — they are the public viewer's
principal-resolution entry point (a public visitor has no session tenant). app.py's public
routes bind that link's tenant_id (via _bind_link_tenant) BEFORE any downstream scoped read,
so a public viewer sees exactly the link's own tenant's data and nothing else. We assert that
binding contract here at the module level: with tenant B bound, B cannot resolve A's link by
id / see A's views, but the token lookup (the entry point) still returns the row so the route
can bind A's tenant.

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads are
unscoped — byte-identical to today (the existing sharing suites are the standing OFF proof;
this file adds one explicit OFF assertion).
"""
import importlib

import pytest


@pytest.fixture()
def sh(tmp_path, monkeypatch):
    """A fresh sharing.db + security.db with the `multitenant` switch ON."""
    import auth
    import tenancy
    import sharing
    importlib.reload(sharing)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    sharing._SCHEMA_READY.clear()

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield sharing, tenancy
    finally:
        tenancy.reset_tenant()


def _seed_two_tenants(sharing, tenancy):
    """As tenant A and tenant B each create a link (with a view + NDA agreement) and a data
    room (with a document, a room link and a question) — all via the REAL write paths."""
    out = {}
    for t, suffix in (("A", "A"), ("B", "B")):
        tenancy.set_tenant(t)
        link, _ = sharing.create_link(f"/vault/{suffix}.pdf", f"Doc {suffix}", f"user{suffix}",
                                      nda_required=True, agreement_text="NDA")
        sharing.record_view(link, f"v{suffix}@x.com", "1.1.1.1", "agent")
        sharing.record_agreement(link, f"v{suffix}@x.com", "1.1.1.1", "agent")
        room, _ = sharing.create_room(f"Room{suffix}", f"Room {suffix}", f"user{suffix}")
        doc, _ = sharing.add_document(room["id"], f"/vault/room_{suffix}.pdf", f"RD {suffix}")
        rlink, _ = sharing.create_room_link(room["id"], f"user{suffix}")
        sharing.record_room_page_view(rlink, doc["id"], "sess", 1, 5000)
        q, _ = sharing.ask_question(room["id"], f"v{suffix}@x.com", f"Question {suffix}?")
        out[t] = {"link": link, "room": room, "doc": doc, "rlink": rlink, "q": q}
    tenancy.reset_tenant()
    return out


# ── WRITE stamping ──────────────────────────────────────────────────────────────
def test_writes_stamp_the_bound_tenant(sh):
    sharing, tenancy = sh
    _seed_two_tenants(sharing, tenancy)
    tenancy.set_owner_scope()
    con = sharing.connect()
    try:
        links = {r["title"]: r["tenant_id"]
                 for r in con.execute("SELECT title, tenant_id FROM share_links")}
        rooms = {r["name"]: r["tenant_id"]
                 for r in con.execute("SELECT name, tenant_id FROM datarooms")}
        views = {r["tenant_id"] for r in con.execute("SELECT tenant_id FROM share_views")}
    finally:
        con.close()
    assert links == {"Doc A": "A", "Doc B": "B"}
    assert rooms == {"RoomA": "A", "RoomB": "B"}
    assert views == {"A", "B"}


# ── READ isolation (the core GDPR proof) ────────────────────────────────────────
def test_tenant_a_sees_only_its_links_and_rooms(sh):
    sharing, tenancy = sh
    seeded = _seed_two_tenants(sharing, tenancy)
    tenancy.set_tenant("A")
    assert {l["title"] for l in sharing.list_links("userA")} == {"Doc A"}
    assert sharing.list_links("userB") == []          # B's creator yields nothing for A
    assert {r["name"] for r in sharing.list_rooms("userA")} == {"RoomA"}
    # by-id: A resolves its own link/room; B's is invisible (None).
    assert sharing.get_by_id(seeded["A"]["link"]["id"]) is not None
    assert sharing.get_by_id(seeded["B"]["link"]["id"]) is None
    assert sharing.get_room(seeded["B"]["room"]["id"]) is None
    # A's view count is its own; B's link id yields 0 (scoped out)
    assert sharing.view_count(seeded["A"]["link"]["id"]) == 1
    assert sharing.view_count(seeded["B"]["link"]["id"]) == 0
    assert {a["viewer_email"] for a in sharing.agreements_for(seeded["A"]["link"]["id"])} \
        == {"vA@x.com"}
    assert sharing.agreements_for(seeded["B"]["link"]["id"]) == []
    # room engagement + Q&A scoped
    assert {q["question"] for q in sharing.questions_for(seeded["A"]["room"]["id"])} \
        == {"Question A?"}
    assert sharing.questions_for(seeded["B"]["room"]["id"]) == []


def test_tenant_b_sees_only_its_links_and_rooms(sh):
    sharing, tenancy = sh
    seeded = _seed_two_tenants(sharing, tenancy)
    tenancy.set_tenant("B")
    assert {l["title"] for l in sharing.list_links("userB")} == {"Doc B"}
    assert {r["name"] for r in sharing.list_rooms("userB")} == {"RoomB"}
    assert sharing.get_by_id(seeded["A"]["link"]["id"]) is None
    assert sharing.get_room(seeded["A"]["room"]["id"]) is None
    assert {q["question"] for q in sharing.questions_for(seeded["B"]["room"]["id"])} \
        == {"Question B?"}


# ── PUBLIC token lookup is the unscoped entry point (route then binds) ───────────
def test_token_lookup_is_unscoped_entry_point(sh):
    """get_by_token / get_room_link_by_token resolve regardless of the bound tenant (the
    public viewer has no session tenant). The app route binds the link's tenant from the
    returned row before any scoped read. We prove the lookup returns A's link even as B,
    AND that binding A's tenant then makes A's downstream reads work while B's don't."""
    sharing, tenancy = sh
    seeded = _seed_two_tenants(sharing, tenancy)
    a_token = seeded["A"]["link"]["token"]
    # As tenant B, the token lookup STILL returns A's link (entry point, unscoped)...
    tenancy.set_tenant("B")
    link = sharing.get_by_token(a_token)
    assert link is not None and link["tenant_id"] == "A"
    # ...the route would now bind the link's tenant; simulate that and the scoped reads work.
    tenancy.set_tenant(link["tenant_id"])
    assert sharing.view_count(link["id"]) == 1
    assert sharing.get_by_id(link["id"]) is not None


# ── OWNER cross-tenant scope ────────────────────────────────────────────────────
def test_owner_scope_sees_both_tenants(sh):
    sharing, tenancy = sh
    _seed_two_tenants(sharing, tenancy)
    tenancy.set_owner_scope()
    assert {l["title"] for l in sharing.list_links("userA")} == {"Doc A"}
    assert {l["title"] for l in sharing.list_links("userB")} == {"Doc B"}
    # an owner-scoped per-id read sees either tenant's row
    con = sharing.connect()
    try:
        a_id = con.execute("SELECT id FROM share_links WHERE tenant_id='A'").fetchone()["id"]
        b_id = con.execute("SELECT id FROM share_links WHERE tenant_id='B'").fetchone()["id"]
    finally:
        con.close()
    assert sharing.get_by_id(a_id) is not None
    assert sharing.get_by_id(b_id) is not None


# ── OFF regression: byte-identical to today ─────────────────────────────────────
def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import sharing
    importlib.reload(sharing)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    sharing._SCHEMA_READY.clear()
    assert tenancy.multitenant_enabled() is False

    tenancy.set_tenant("A")          # inert while OFF
    link, _ = sharing.create_link("/vault/x.pdf", "Doc", "user")
    sharing.record_view(link, "v@x.com", "1.1.1.1", "agent")
    tenancy.reset_tenant()

    con = sharing.connect()
    try:
        assert con.execute("SELECT tenant_id FROM share_links WHERE id=?",
                           (link["id"],)).fetchone()["tenant_id"] == "default"
    finally:
        con.close()
    # reads are unscoped regardless of any stray thread tenant
    tenancy.set_tenant("ZZZ")
    try:
        assert sharing.get_by_id(link["id"]) is not None
        assert sharing.view_count(link["id"]) == 1
        assert {l["title"] for l in sharing.list_links("user")} == {"Doc"}
    finally:
        tenancy.reset_tenant()
