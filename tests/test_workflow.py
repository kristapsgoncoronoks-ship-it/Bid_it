"""
WORKFLOW ENGINE (Box Relay-style, workflow.py) — a configurable, ORDERED approval/routing
engine over a document/invoice, in an APP-OWNED, separate workflow.db (the engine product DBs
stay read-only). These tests repoint workflow.DB at a temp file and exercise:

  - define a 3-step workflow; start_run creates the run + the FIRST step's task;
  - act_on_task(approve) advances to the next step's task; a reject ends the run rejected;
  - my_tasks returns the right pending tasks for a user/role and not others';
  - the notify / sign / tag step actions fire their side-effect (notify mocked; an esign
    request is created for `sign`; a metadata tag is assigned for `tag`);
  - the Tasks inbox + admin define pages render and ESCAPE a planted XSS in a workflow/step name;
  - admin-only gating on the define page (a processor is forbidden);
  - the ADVISORY no-mutation guard: workflow.py has no vat_refund / claims-figure mutation path;
  - app-owned: workflow.db is a separate writable DB and no product DB is opened writable;
  - tenant isolation: a tenant can't see another tenant's workflows / runs / tasks.
"""
import os
import re
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import workflow  # noqa: E402


@pytest.fixture()
def wf(tmp_path, monkeypatch):
    """Repoint workflow.DB at a temp file (so we never touch the live workflow.db)."""
    monkeypatch.setattr(workflow, "DB", str(tmp_path / "workflow.db"), raising=True)
    monkeypatch.setattr(workflow, "_SCHEMA_READY", set(), raising=True)
    return workflow


def _three_step(wf, assignee="admin"):
    """A canonical 3-step approve workflow."""
    w, err = wf.define_workflow("Refund pack review", [
        {"name": "Manager approval", "assignee": assignee, "action": "approve"},
        {"name": "Compliance approval", "assignee": assignee, "action": "approve"},
        {"name": "Final sign-off", "assignee": assignee, "action": "approve"},
    ])
    assert err == "" and w is not None, err
    return w


# ----------------------------------------------------------------- define + lifecycle
def test_define_three_step_and_start_run(wf):
    w = _three_step(wf)
    assert len(w["steps"]) == 3
    assert w["active"] is True
    run, err = wf.start_run(w["id"], "doc:1", "alice")
    assert err == "" and run is not None
    assert run["status"] == "running" and run["current_step"] == 0
    tasks = wf.tasks_for_run(run["id"])
    assert len(tasks) == 1                       # only the FIRST step's task exists
    assert tasks[0]["step_index"] == 0 and tasks[0]["status"] == "pending"


def test_approve_advances_through_steps_to_approved(wf):
    w = _three_step(wf)
    run, _ = wf.start_run(w["id"], "doc:1", "alice")
    # approve step 0 -> a step-1 task appears, run still running
    t0 = wf.tasks_for_run(run["id"])[0]
    r1, err = wf.act_on_task(t0["id"], "approve", "admin", "ok")
    assert err == "" and r1["status"] == "running" and r1["current_step"] == 1
    tasks = wf.tasks_for_run(run["id"])
    assert len(tasks) == 2 and tasks[1]["step_index"] == 1
    # approve step 1 -> step 2
    t1 = [t for t in tasks if t["step_index"] == 1][0]
    r2, _ = wf.act_on_task(t1["id"], "approve", "admin")
    assert r2["status"] == "running" and r2["current_step"] == 2
    # approve the LAST step -> the run is approved
    t2 = [t for t in wf.tasks_for_run(run["id"]) if t["step_index"] == 2][0]
    r3, _ = wf.act_on_task(t2["id"], "approve", "admin")
    assert r3["status"] == "approved"
    # no more pending tasks
    assert all(t["status"] != "pending" for t in wf.tasks_for_run(run["id"]))


def test_reject_ends_run_rejected(wf):
    w = _three_step(wf)
    run, _ = wf.start_run(w["id"], "doc:1", "alice")
    t0 = wf.tasks_for_run(run["id"])[0]
    r1, err = wf.act_on_task(t0["id"], "reject", "admin", "missing PoA")
    assert err == "" and r1["status"] == "rejected"
    # the rejected task carries the note + actor; no further task minted
    tasks = wf.tasks_for_run(run["id"])
    assert len(tasks) == 1 and tasks[0]["status"] == "rejected"
    assert tasks[0]["note"] == "missing PoA" and tasks[0]["acted_by"] == "admin"


def test_act_on_terminal_run_is_refused(wf):
    w = _three_step(wf)
    run, _ = wf.start_run(w["id"], "doc:1", "alice")
    t0 = wf.tasks_for_run(run["id"])[0]
    wf.act_on_task(t0["id"], "reject", "admin")
    # acting again on the same (now non-pending) task is refused with an error, never raises
    r, err = wf.act_on_task(t0["id"], "approve", "admin")
    assert r is None and err


# ----------------------------------------------------------------- my_tasks routing
def test_my_tasks_matches_user_role_and_anyone(wf):
    # one step assigned to a role, one to a username, one unassigned ("anyone")
    w, _ = wf.define_workflow("Routing", [
        {"name": "Role step", "assignee": "processor", "action": "approve"},
        {"name": "User step", "assignee": "bob", "action": "approve"},
        {"name": "Open step", "assignee": "", "action": "approve"},
    ])
    run, _ = wf.start_run(w["id"], "doc:7", "alice")
    # step 0 assigned to role 'processor': a processor sees it, an unrelated user does not
    assert [t["step_index"] for t in wf.my_tasks(user="carol", role="processor")] == [0]
    assert wf.my_tasks(user="carol", role="admin") == []
    # advance to step 1 (assigned to user 'bob')
    t0 = wf.tasks_for_run(run["id"])[0]
    wf.act_on_task(t0["id"], "approve", "alice")
    assert [t["step_index"] for t in wf.my_tasks(user="bob", role="processor")] == [1]
    assert wf.my_tasks(user="dave", role="admin") == []     # not bob, not the role
    # advance to step 2 (unassigned -> visible to anyone logged in)
    t1 = [t for t in wf.tasks_for_run(run["id"]) if t["step_index"] == 1][0]
    wf.act_on_task(t1["id"], "approve", "bob")
    assert [t["step_index"] for t in wf.my_tasks(user="random", role="processor")] == [2]


# ----------------------------------------------------------------- step-action side-effects
def test_notify_step_fires_send_alert(wf, monkeypatch):
    calls = []

    def fake_send_alert(subject, lines, transport=None, recipients=None):
        calls.append((subject, list(lines), recipients))
        return True

    import notify
    monkeypatch.setattr(notify, "send_alert", fake_send_alert, raising=True)
    w, _ = wf.define_workflow("Notify flow", [
        {"name": "Ping reviewer", "assignee": "", "action": "notify",
         "params": {"subject": "Please review", "message": "doc needs a look"}},
    ])
    run, err = wf.start_run(w["id"], "doc:9", "alice")
    assert err == "" and run is not None
    assert len(calls) == 1
    assert calls[0][0] == "Please review"
    assert calls[0][1] == ["doc needs a look"]


def test_sign_step_creates_esign_request(wf, tmp_path, monkeypatch):
    import esign
    monkeypatch.setattr(esign, "DB", str(tmp_path / "esign.db"), raising=True)
    monkeypatch.setattr(esign, "_SCHEMA_READY", set(), raising=True)
    w, _ = wf.define_workflow("Sign flow", [
        {"name": "Sign the PoA", "assignee": "admin", "action": "sign",
         "params": {"title": "Power of attorney"}},
    ])
    run, err = wf.start_run(w["id"], "doc:42", "alice")
    assert err == "" and run is not None
    reqs = [r for r in esign.list_requests() if r["subject_ref"] == "doc:42"]
    assert len(reqs) == 1 and reqs[0]["title"] == "Power of attorney"


def test_tag_step_assigns_metadata_tag(wf, tmp_path, monkeypatch):
    import metadata
    monkeypatch.setattr(metadata, "DB", str(tmp_path / "metadata.db"), raising=True)
    monkeypatch.setattr(metadata, "_SCHEMA_READY", set(), raising=True)
    w, _ = wf.define_workflow("Tag flow", [
        {"name": "Tag reviewed", "assignee": "", "action": "tag",
         "params": {"tag": "Workflow-reviewed"}},
    ])
    run, err = wf.start_run(w["id"], "doc:55", "alice")
    assert err == "" and run is not None
    names = {t["name"] for t in metadata.tags_for("doc:55")}
    assert "Workflow-reviewed" in names


# ----------------------------------------------------------------- web surface (escaping)
def test_tasks_inbox_renders_and_escapes_xss(wf, client):
    xss = '<script>alert(1)</script>'
    w, _ = wf.define_workflow(xss, [
        {"name": xss, "assignee": "pytest_admin", "action": "approve"},
    ])
    wf.start_run(w["id"], "doc:1", "pytest_admin")
    body = client.get("/tasks").get_data(as_text=True)
    assert "My tasks &amp; approvals" in body
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_admin_define_page_renders_and_escapes_xss(wf, client):
    xss = '<script>alert(2)</script>'
    wf.define_workflow(xss, [{"name": xss, "assignee": "admin", "action": "approve"}])
    body = client.get("/workflows").get_data(as_text=True)
    assert "Define a workflow" in body
    assert "<script>alert(2)</script>" not in body
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in body


def test_define_via_web_form(wf, client):
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/workflows").get_data(as_text=True)).group(1)
    r = client.post("/workflows/define", data={
        "_csrf": tok, "name": "Web defined", "trigger": "manual",
        "steps": "Step one | admin | approve\nTag it | | tag | tag=Done"})
    assert "Workflow created" in r.get_data(as_text=True)
    ws = [x for x in wf.list_workflows() if x["name"] == "Web defined"]
    assert len(ws) == 1 and len(ws[0]["steps"]) == 2
    assert ws[0]["steps"][1]["action"] == "tag"
    assert ws[0]["steps"][1]["params"] == {"tag": "Done"}


def test_act_on_task_via_web_advances_run(wf, client):
    w = _three_step(wf, assignee="pytest_admin")
    run, _ = wf.start_run(w["id"], "doc:1", "pytest_admin")
    t0 = wf.tasks_for_run(run["id"])[0]
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/tasks").get_data(as_text=True)).group(1)
    r = client.post("/tasks/act", data={
        "_csrf": tok, "task_id": t0["id"], "decision": "approve", "note": "fine"})
    assert "Recorded" in r.get_data(as_text=True)
    assert wf.get_run(run["id"])["current_step"] == 1


# ----------------------------------------------------------------- admin-only gating
def test_define_page_is_admin_only(wf, monkeypatch):
    """A processor session is forbidden from the workflow define/manage page."""
    import app as A
    import auth
    auth.add_user("pytest_proc_wf", "Proc!Pw12345", role="processor")
    try:
        c = A.app.test_client()
        r = c.post("/login", data={"username": "pytest_proc_wf",
                                   "password": "Proc!Pw12345"})
        assert r.status_code == 302
        resp = c.get("/workflows")
        assert resp.status_code == 403
        assert "Insufficient permissions" in resp.get_data(as_text=True)
    finally:
        con = auth.connect()
        con.execute("DELETE FROM users WHERE username=?", ("pytest_proc_wf",))
        con.commit(); con.close()


# ----------------------------------------------------------------- advisory no-mutation guard
def test_workflow_module_has_no_vat_or_figure_mutation_path():
    """STRUCTURAL guarantee: workflow.py is advisory. It must not import vat_refund / a claims
    DB / a money path, nor reference any claim-status / lock / fee mutation verb. The legal
    gates remain the sole authority over filing; a workflow approval is process tracking only."""
    src = open(os.path.join(WORKDIR, "workflow.py")).read()
    forbidden = ["import vat_refund", "vat_refund.", "vat_claims", "import money", "money.",
                 "set_status", "update_status", "place_lock", "release_lock",
                 "set_fee", "ENGINE_OF", "STATUS_LABELS", "build_workbook"]
    hits = [tok for tok in forbidden if tok in src]
    assert hits == [], f"workflow.py references a VAT/figure mutation path: {hits}"


# ----------------------------------------------------------------- app-owned (no product DB)
def test_workflow_db_is_separate_and_not_a_product_db(wf, tmp_path):
    w = _three_step(wf)
    wf.start_run(w["id"], "doc:1", "alice")
    # the file lives where DB points (the temp dir), not in any product DB
    assert os.path.exists(wf.DB)
    assert os.path.basename(wf.DB) == "workflow.db"
    assert "fuel_history" not in wf.DB and "suppliers" not in wf.DB
    # it is a normal app-owned WRITABLE sqlite DB (not a product read-only handle)
    con = sqlite3.connect(wf.DB)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"workflows", "workflow_runs", "workflow_tasks"} <= names


def test_no_writable_product_db_handle(wf, monkeypatch):
    """workflow.py must never open a product DB writable: poison dataproduct so any product
    open would raise, then run the full lifecycle — it must complete untouched."""
    import dataproduct

    def _poison(*a, **k):
        raise AssertionError("workflow opened a product DB")

    monkeypatch.setattr(dataproduct, "connect", _poison, raising=True)
    w = _three_step(wf)
    run, err = wf.start_run(w["id"], "doc:1", "alice")
    assert err == "" and run is not None
    t0 = wf.tasks_for_run(run["id"])[0]
    r, err2 = wf.act_on_task(t0["id"], "approve", "admin")
    assert err2 == "" and r["status"] == "running"


# ----------------------------------------------------------------- tenant isolation
def test_tenant_isolation_runs_and_tasks(wf, monkeypatch):
    """Under the multitenant switch ON, a tenant sees only its OWN workflows / runs / tasks."""
    import auth
    import tenancy
    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True
    try:
        # tenant A defines a workflow + starts a run
        tenancy.set_tenant("A")
        wa, _ = wf.define_workflow("A-flow", [
            {"name": "A step", "assignee": "admin", "action": "approve"}])
        run_a, _ = wf.start_run(wa["id"], "doc:A", "alice")
        ta = wf.my_tasks(role="admin")
        tenancy.reset_tenant()

        # tenant B defines its own workflow + run
        tenancy.set_tenant("B")
        wb, _ = wf.define_workflow("B-flow", [
            {"name": "B step", "assignee": "admin", "action": "approve"}])
        run_b, _ = wf.start_run(wb["id"], "doc:B", "bob")
        # B sees ONLY its own workflow / run / tasks
        assert {x["name"] for x in wf.list_workflows()} == {"B-flow"}
        assert [r["subject_ref"] for r in wf.runs_for("doc:A")] == []   # A's run invisible to B
        assert {t["subject_ref"] for t in wf.my_tasks(role="admin")} == {"doc:B"}
        # B cannot read A's run/task across the boundary
        assert wf.get_run(run_a["id"]) is None
        assert wf.get_workflow(wa["id"]) is None
        tenancy.reset_tenant()

        # back as A: only A's data
        tenancy.set_tenant("A")
        assert {x["name"] for x in wf.list_workflows()} == {"A-flow"}
        assert {t["subject_ref"] for t in wf.my_tasks(role="admin")} == {"doc:A"}
        assert wf.get_run(run_b["id"]) is None
        tenancy.reset_tenant()
    finally:
        tenancy.reset_tenant()
        auth.set_setting("multitenant", "0")
