"""Multi-tenancy P2 — CROSS-TENANT ISOLATION for ai_assistant.py (chat-with-document).

ai_assistant.py was ALREADY tenant-scoped (it wires tenancy.scope_clause() into start_chat /
get_chat / messages_for and stamps tenancy.queue_tenant() on the chat/message INSERTs). This
file is the isolation HARNESS that proves it behind the `multitenant` switch:
  * WRITES stamp the bound tenant (queue_tenant());
  * READS filter by scope_clause(): as tenant A get_chat / messages_for see ONLY A's chat for a
    subject_ref — B's chat on the SAME subject is ABSENT. The platform OWNER sees BOTH.

The chat-history helpers are exercised directly (no AI backend needed — ask()/enabled() are
the network seam and stay OFF here).

CARDINAL invariant: with the switch OFF (default) writes stamp 'default' and reads are
unscoped — byte-identical to today.
"""
import importlib

import pytest


@pytest.fixture()
def aa(tmp_path, monkeypatch):
    import auth
    import tenancy
    import ai_assistant
    importlib.reload(ai_assistant)

    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(ai_assistant, "DB", str(tmp_path / "ai_chat.db"))

    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        yield ai_assistant, tenancy
    finally:
        tenancy.reset_tenant()


REF = "doc:shared"   # both tenants chat ABOUT the same subject_ref


def _seed_two_tenants(ai_assistant, tenancy):
    out = {}
    for t in ("A", "B"):
        tenancy.set_tenant(t)
        cid = ai_assistant.start_chat(REF, created_by=f"user{t}")
        ai_assistant.record_message(cid, "user", f"Hello from {t}")
        ai_assistant.record_message(cid, "assistant", f"Reply to {t}")
        out[t] = cid
    tenancy.reset_tenant()
    return out


# ── WRITE stamping ──────────────────────────────────────────────────────────────
def test_writes_stamp_the_bound_tenant(aa):
    ai_assistant, tenancy = aa
    _seed_two_tenants(ai_assistant, tenancy)
    tenancy.set_owner_scope()
    con = ai_assistant.connect()
    try:
        chats = {r["created_by"]: r["tenant_id"]
                 for r in con.execute("SELECT created_by, tenant_id FROM ai_chats")}
        msgs = {r["tenant_id"] for r in con.execute("SELECT tenant_id FROM ai_chat_messages")}
    finally:
        con.close()
    assert chats == {"userA": "A", "userB": "B"}
    assert msgs == {"A", "B"}


# ── READ isolation ──────────────────────────────────────────────────────────────
def test_tenant_a_sees_only_its_chat(aa):
    ai_assistant, tenancy = aa
    seeded = _seed_two_tenants(ai_assistant, tenancy)
    tenancy.set_tenant("A")
    assert ai_assistant.get_chat(REF) == seeded["A"]          # A's own chat id
    msgs = ai_assistant.messages_for(REF)
    assert [m["content"] for m in msgs] == ["Hello from A", "Reply to A"]


def test_tenant_b_sees_only_its_chat(aa):
    ai_assistant, tenancy = aa
    seeded = _seed_two_tenants(ai_assistant, tenancy)
    tenancy.set_tenant("B")
    assert ai_assistant.get_chat(REF) == seeded["B"]
    assert [m["content"] for m in ai_assistant.messages_for(REF)] \
        == ["Hello from B", "Reply to B"]


# ── OWNER cross-tenant scope ────────────────────────────────────────────────────
def test_owner_scope_sees_all_chats(aa):
    ai_assistant, tenancy = aa
    _seed_two_tenants(ai_assistant, tenancy)
    tenancy.set_owner_scope()
    con = ai_assistant.connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM ai_chats").fetchone()[0]
    finally:
        con.close()
    assert n == 2


# ── OFF regression ──────────────────────────────────────────────────────────────
def test_switch_off_stamps_default_and_reads_unscoped(tmp_path, monkeypatch):
    import auth
    import tenancy
    import ai_assistant
    importlib.reload(ai_assistant)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())
    monkeypatch.setattr(ai_assistant, "DB", str(tmp_path / "ai_chat.db"))
    assert tenancy.multitenant_enabled() is False

    tenancy.set_tenant("A")          # inert while OFF
    cid = ai_assistant.start_chat(REF, created_by="user")
    ai_assistant.record_message(cid, "user", "Hi")
    tenancy.reset_tenant()

    con = ai_assistant.connect()
    try:
        assert con.execute("SELECT tenant_id FROM ai_chats WHERE id=?",
                           (cid,)).fetchone()["tenant_id"] == "default"
    finally:
        con.close()
    tenancy.set_tenant("ZZZ")
    try:
        assert ai_assistant.get_chat(REF) == cid
        assert [m["content"] for m in ai_assistant.messages_for(REF)] == ["Hi"]
    finally:
        tenancy.reset_tenant()
