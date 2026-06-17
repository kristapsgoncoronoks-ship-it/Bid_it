"""
WORKFLOW ENGINE (Box Relay-style) — a configurable, ORDERED approval/routing engine over a
document / invoice. An admin DEFINES a workflow (a named, ordered list of steps, each with an
assignee + an action); a user STARTS a run over a subject (a `doc:<id>` / invoice ref); the run
walks its steps one at a time, minting a TASK per step for the assignee to act on. Assignees see
their pending tasks in a Tasks/Approvals inbox; acting (approve/reject) advances the run to the
next step (or completes/rejects it). MVP: manual start + the four core step actions.

ADVISORY — NEVER OVERRIDES THE VAT LEGAL GATES. A workflow approval is ADDITIVE process tracking.
This module is structurally separate from the refund engine: it NEVER imports a write path into
the claim lifecycle, a recovered figure, a workflow status code, an invoice lock, the checklist
or the period-end gate. Those legal gates remain the SOLE authority over what can actually be
filed. A run reaching `approved` changes NOTHING about a claim — it only records that a human
process step was approved. The `sign`/`notify`/`tag` step actions reuse the EXISTING advisory
seams (esign.create_request / notify.send_alert / metadata.assign_tag) and are themselves
best-effort: a side-effect failure never blocks the run.

APP-OWNED OVERLAY. Like every other app-owned module this owns its OWN SQLite file (workflow.db,
gitignored) via connect() + db_migrate, audit-installed, db_tuning-tuned. It writes NO engine
product DB and NO claims DB — it only keys rows by a free-text `subject_ref` string, exactly like
metadata.py / esign.py.

TENANCY. Every table carries a tenant_id (tenancy seam, inert today): stamped on INSERT via
tenancy.write_tenant() and filtered on every read via tenancy.scope_clause() — so a tenant can
never see another tenant's workflows / runs / tasks once the switch is ON.

BEST-EFFORT / NEVER-RAISE. Every public API is best-effort and returns AS A VALUE
((obj, "") / (None, err) / [] / {} / bool) — it never raises into the caller, mirroring
metadata.py / esign.py / notify.py. Failures are logged via applog.
"""
import os
import json
import sqlite3
import datetime

import applog
import audit
import db_tuning
import db_migrate
import tenancy

log = applog.get("workflow")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned workflow DB (gitignored). A module-level attr so tests can repoint it the
# same way they repoint metadata.DB / esign.DB.
DB = f"{WORKDIR}/workflow.db"

# A workflow TRIGGER. MVP ships `manual` only, but the column/validation allow the
# event triggers a later phase will add (a confirmed intake, a claim becoming ready) so
# nothing has to migrate the stored shape.
TRIGGERS = ("manual", "on_confirm", "on_claim_ready")

# A step ACTION. `approve` is a pure human gate; the other three reuse an EXISTING advisory
# seam as a best-effort side-effect when the step is entered (notify / sign / tag).
STEP_ACTIONS = ("approve", "sign", "notify", "tag")

# Run lifecycle. `running` walks the steps; the terminal states are reached when the last
# step is approved (`approved`), any step is rejected (`rejected`), an explicit finish
# (`done`) or an admin cancellation (`cancelled`).
RUN_STATUSES = ("running", "approved", "rejected", "done", "cancelled")
RUN_TERMINAL = ("approved", "rejected", "done", "cancelled")

# Task lifecycle. A pending task is the one the assignee acts on; acting flips it to
# done/rejected, and a cancelled run marks any open task skipped.
TASK_STATUSES = ("pending", "done", "rejected", "skipped")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    trigger    TEXT NOT NULL DEFAULT 'manual',
    steps_json TEXT NOT NULL DEFAULT '[]',   -- ordered [{name, assignee, action, params}]
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    tenant_id  TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    id          INTEGER PRIMARY KEY,
    workflow_id INTEGER NOT NULL,
    subject_ref TEXT NOT NULL,                -- doc:<id> / invoice ref
    current_step INTEGER NOT NULL DEFAULT 0,  -- index into the workflow's steps
    status      TEXT NOT NULL DEFAULT 'running',
    started_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    tenant_id   TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_subject ON workflow_runs(subject_ref);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_status ON workflow_runs(status, created_at);
CREATE TABLE IF NOT EXISTS workflow_tasks (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    assignee   TEXT,                          -- a role or a username
    action     TEXT NOT NULL DEFAULT 'approve',
    status     TEXT NOT NULL DEFAULT 'pending',
    acted_by   TEXT,
    acted_at   TEXT,
    note       TEXT,
    tenant_id  TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_workflow_tasks_run ON workflow_tasks(run_id, step_index);
CREATE INDEX IF NOT EXISTS ix_workflow_tasks_assignee ON workflow_tasks(assignee, status);
"""

# Versioned migrations: APPEND new statements at the END (positions are stable).
_MIGRATIONS = []

_SCHEMA_READY = set()   # DB files whose schema is set up this process


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)      # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "workflow", _MIGRATIONS)
        audit.install_audit(con, ["workflows", "workflow_runs", "workflow_tasks"])
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


# ============================================================ step normalisation
def normalize_steps(steps):
    """Validate + normalise a list of step dicts into the stored shape. Returns
    (clean_steps, "") or (None, error). Never raises. Each clean step is
    {name, assignee, action, params}: name defaults to the action, assignee is a free-text
    role/username (may be empty = "anyone"), action must be one of STEP_ACTIONS, params is a
    dict (e.g. {"tag": "Reviewed"} for a tag step). A step list must be non-empty."""
    if steps is None:
        return None, "at least one step is required"
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception:
            return None, "steps must be a JSON list"
    if not isinstance(steps, (list, tuple)):
        return None, "steps must be a list"
    out = []
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            return None, f"step {i + 1} is not an object"
        action = str(raw.get("action") or "approve").strip().lower()
        if action not in STEP_ACTIONS:
            return None, f"step {i + 1}: unknown action {action!r}"
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            return None, f"step {i + 1}: params must be an object"
        name = str(raw.get("name") or "").strip() or action.capitalize()
        assignee = str(raw.get("assignee") or "").strip()
        out.append({"name": name, "assignee": assignee, "action": action,
                    "params": params})
    if not out:
        return None, "at least one step is required"
    return out, ""


def _row_steps(row):
    """Parse the stored steps_json back into a list (best-effort -> [])."""
    try:
        steps = json.loads(row["steps_json"] or "[]")
        return steps if isinstance(steps, list) else []
    except Exception as e:
        log.warning("could not parse steps_json for workflow %s: %s",
                    (dict(row).get("id") if row else "?"), e)
        return []


def _wf_dict(row):
    d = dict(row)
    d["steps"] = _row_steps(row)
    d["active"] = bool(d.get("active"))
    return d


# ============================================================ workflow definitions
def define_workflow(name, steps, trigger="manual"):
    """Define a workflow. Returns (workflow_dict, "") or (None, error). Never raises.
    `steps` is a list (or JSON string) of {name, assignee, action, params}; `trigger` is a
    TRIGGERS value (MVP: manual)."""
    name = (name or "").strip()
    if not name:
        return None, "a workflow name is required"
    trigger = (trigger or "manual").strip().lower()
    if trigger not in TRIGGERS:
        return None, f"unknown trigger {trigger!r}"
    clean, err = normalize_steps(steps)
    if err:
        return None, err
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO workflows (name, trigger, steps_json, active, tenant_id)
                   VALUES (?,?,?,1,?)""",
                (name, trigger, json.dumps(clean), tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM workflows WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (_wf_dict(row) if row else None), ""
    except Exception as e:
        log.exception("define_workflow failed for name=%r", name)
        return None, f"could not define workflow ({str(e)[:80]})"


def get_workflow(workflow_id):
    """Return a workflow dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM workflows WHERE id=?" + frag,
                              [workflow_id, *tp]).fetchone()
        finally:
            con.close()
        return _wf_dict(row) if row else None
    except Exception as e:
        log.warning("get_workflow failed for %s: %s", workflow_id, e)
        return None


def list_workflows(active_only=False):
    """All workflows (by name), each with `steps` parsed to a list. With `active_only`
    only active ones. Never raises -> []."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            q = "SELECT * FROM workflows WHERE 1=1"
            if active_only:
                q += " AND active=1"
            q += frag + " ORDER BY name, id"
            rows = con.execute(q, tp).fetchall()
        finally:
            con.close()
        return [_wf_dict(r) for r in rows]
    except Exception as e:
        log.warning("list_workflows failed: %s", e)
        return []


def update_workflow(workflow_id, name=None, steps=None, trigger=None):
    """Update a workflow's name / steps / trigger (each optional — leave None to keep).
    Returns (True, "") or (False, error). Never raises."""
    wf = get_workflow(workflow_id)
    if wf is None:
        return False, "no such workflow"
    new_name = wf["name"] if name is None else (name or "").strip()
    if not new_name:
        return False, "a workflow name is required"
    if trigger is None:
        new_trigger = wf["trigger"]
    else:
        new_trigger = (trigger or "manual").strip().lower()
        if new_trigger not in TRIGGERS:
            return False, f"unknown trigger {new_trigger!r}"
    if steps is None:
        new_steps_json = json.dumps(wf["steps"])
    else:
        clean, err = normalize_steps(steps)
        if err:
            return False, err
        new_steps_json = json.dumps(clean)
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute(
                "UPDATE workflows SET name=?, trigger=?, steps_json=? WHERE id=?" + frag,
                [new_name, new_trigger, new_steps_json, workflow_id, *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("update_workflow failed for %s", workflow_id)
        return False, f"could not update workflow ({str(e)[:80]})"


def deactivate_workflow(workflow_id, active=False):
    """Deactivate (or re-activate) a workflow so it no longer offers a manual start.
    Existing runs are untouched. Returns (True, "") or (False, error). Never raises."""
    if get_workflow(workflow_id) is None:
        return False, "no such workflow"
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute("UPDATE workflows SET active=? WHERE id=?" + frag,
                        [1 if active else 0, workflow_id, *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("deactivate_workflow failed for %s", workflow_id)
        return False, f"could not update workflow ({str(e)[:80]})"


# ============================================================ runs + tasks
def _run_dict(row):
    return dict(row)


def _create_task(con, run_id, step_index, step):
    """Insert the pending task for `step` of `run_id` and fire its (best-effort) side-effect.
    Caller commits. Returns the new task id."""
    cur = con.execute(
        """INSERT INTO workflow_tasks
           (run_id, step_index, assignee, action, status, tenant_id)
           VALUES (?,?,?,?, 'pending', ?)""",
        (run_id, step_index, (step.get("assignee") or "") or None,
         step.get("action") or "approve", tenancy.write_tenant()))
    return cur.lastrowid


def start_run(workflow_id, subject_ref, actor=None):
    """Start a run of `workflow_id` over `subject_ref` (a doc:<id> / invoice ref). Creates the
    run (status 'running', current_step 0) and the FIRST step's task, then fires that step's
    side-effect (best-effort). Returns (run_dict, "") or (None, error). Never raises.

    A workflow with no steps is rejected; the workflow must be ACTIVE to start."""
    subject_ref = (subject_ref or "").strip()
    if not subject_ref:
        return None, "a subject reference is required"
    wf = get_workflow(workflow_id)
    if wf is None:
        return None, "no such workflow"
    if not wf.get("active"):
        return None, "this workflow is not active"
    steps = wf.get("steps") or []
    if not steps:
        return None, "this workflow has no steps"
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO workflow_runs
                   (workflow_id, subject_ref, current_step, status, started_by, tenant_id)
                   VALUES (?,?,0,'running',?,?)""",
                (workflow_id, subject_ref, (actor or "") or None, tenancy.write_tenant()))
            run_id = cur.lastrowid
            _create_task(con, run_id, 0, steps[0])
            con.commit()
            row = con.execute("SELECT * FROM workflow_runs WHERE id=?",
                              (run_id,)).fetchone()
        finally:
            con.close()
        # fire the first step's side-effect OUTSIDE the txn (best-effort, never blocks).
        _fire_step_action(steps[0], subject_ref, actor)
        return (_run_dict(row) if row else None), ""
    except Exception as e:
        log.exception("start_run failed for workflow %s subject %r", workflow_id, subject_ref)
        return None, f"could not start run ({str(e)[:80]})"


def get_run(run_id):
    """Return a run row dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM workflow_runs WHERE id=?" + frag,
                              [run_id, *tp]).fetchone()
        finally:
            con.close()
        return _run_dict(row) if row else None
    except Exception as e:
        log.warning("get_run failed for %s: %s", run_id, e)
        return None


def get_task(task_id):
    """Return a task row dict by id, or None. Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute("SELECT * FROM workflow_tasks WHERE id=?" + frag,
                              [task_id, *tp]).fetchone()
        finally:
            con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("get_task failed for %s: %s", task_id, e)
        return None


def tasks_for_run(run_id):
    """All tasks of a run, in step order. Never raises -> []."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                "SELECT * FROM workflow_tasks WHERE run_id=?" + frag
                + " ORDER BY step_index, id", [run_id, *tp]).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("tasks_for_run failed for %s: %s", run_id, e)
        return []


def my_tasks(user=None, role=None):
    """The PENDING tasks assigned to `user` or their `role`. A task whose assignee equals the
    username OR the role is returned; a task with NO assignee ("anyone") is also returned (any
    logged-in user may act). Each task is joined to its run (subject_ref, workflow name) for
    display. Newest first. Never raises -> []."""
    user = (user or "").strip()
    role = (role or "").strip()
    targets = {t for t in (user, role) if t}
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("t.tenant_id")
            rows = con.execute(
                """SELECT t.*, r.subject_ref AS subject_ref, r.workflow_id AS workflow_id,
                          w.name AS workflow_name
                   FROM workflow_tasks t
                   JOIN workflow_runs r ON r.id=t.run_id
                   LEFT JOIN workflows w ON w.id=r.workflow_id
                   WHERE t.status='pending' AND r.status='running'""" + frag
                + " ORDER BY t.created_at DESC, t.id DESC", tp).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            d = dict(r)
            assignee = (d.get("assignee") or "").strip()
            # unassigned ("anyone") -> visible to all; otherwise must match user or role.
            if not assignee or assignee in targets:
                out.append(d)
        return out
    except Exception as e:
        log.warning("my_tasks failed for user=%r role=%r: %s", user, role, e)
        return []


def act_on_task(task_id, decision, actor=None, note=None):
    """Record a decision ('approve' or 'reject') on a pending task and ADVANCE the run.

      * approve -> mark the task done; if it was the last step, the run -> 'approved',
        otherwise advance current_step and create the NEXT step's task (firing its
        side-effect best-effort).
      * reject  -> mark the task rejected and the run -> 'rejected' (the run ends).

    Returns (run_dict, "") or (None, error). Never raises. A non-pending task, a non-running
    run or an unknown decision is rejected with an error string."""
    decision = (decision or "").strip().lower()
    if decision not in ("approve", "reject"):
        return None, "decision must be approve or reject"
    task = get_task(task_id)
    if task is None:
        return None, "no such task"
    if task.get("status") != "pending":
        return None, "this task has already been acted on"
    run = get_run(task["run_id"])
    if run is None:
        return None, "no such run"
    if run.get("status") != "running":
        return None, "this run is no longer running"
    wf = get_workflow(run["workflow_id"])
    steps = (wf or {}).get("steps") or []
    next_step = None
    next_index = None
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            new_status = "done" if decision == "approve" else "rejected"
            con.execute(
                "UPDATE workflow_tasks SET status=?, acted_by=?, acted_at=?, note=? "
                "WHERE id=?" + frag,
                [new_status, (actor or "") or None, now, (note or "") or None,
                 task_id, *tp])
            if decision == "reject":
                con.execute("UPDATE workflow_runs SET status='rejected' WHERE id=?" + frag,
                            [run["id"], *tp])
            else:
                step_index = task["step_index"]
                if step_index + 1 < len(steps):
                    next_index = step_index + 1
                    next_step = steps[next_index]
                    con.execute(
                        "UPDATE workflow_runs SET current_step=? WHERE id=?" + frag,
                        [next_index, run["id"], *tp])
                    _create_task(con, run["id"], next_index, next_step)
                else:
                    con.execute(
                        "UPDATE workflow_runs SET status='approved' WHERE id=?" + frag,
                        [run["id"], *tp])
            con.commit()
            row = con.execute("SELECT * FROM workflow_runs WHERE id=?",
                              (run["id"],)).fetchone()
        finally:
            con.close()
        # fire the NEXT step's side-effect outside the txn (best-effort, never blocks).
        if next_step is not None:
            _fire_step_action(next_step, run["subject_ref"], actor)
        return (_run_dict(row) if row else None), ""
    except Exception as e:
        log.exception("act_on_task failed for task %s", task_id)
        return None, f"could not act on task ({str(e)[:80]})"


def cancel_run(run_id):
    """Cancel a running run: mark it 'cancelled' and any open task 'skipped'. Returns
    (True, "") or (False, error). Idempotent on a terminal run. Never raises."""
    run = get_run(run_id)
    if run is None:
        return False, "no such run"
    if run.get("status") in RUN_TERMINAL:
        return True, ""   # idempotent
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            con.execute("UPDATE workflow_runs SET status='cancelled' WHERE id=?" + frag,
                        [run_id, *tp])
            con.execute("UPDATE workflow_tasks SET status='skipped' "
                        "WHERE run_id=? AND status='pending'" + frag, [run_id, *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("cancel_run failed for %s", run_id)
        return False, f"could not cancel run ({str(e)[:80]})"


def run_status(run_id):
    """A status snapshot of a run: the run row + its workflow name + the current step
    descriptor + the run's tasks. Returns a dict or None. Never raises."""
    run = get_run(run_id)
    if run is None:
        return None
    wf = get_workflow(run["workflow_id"])
    steps = (wf or {}).get("steps") or []
    cur = run.get("current_step") or 0
    current = steps[cur] if (run.get("status") == "running" and 0 <= cur < len(steps)) else None
    return {
        "run": run,
        "workflow_name": (wf or {}).get("name"),
        "status": run.get("status"),
        "current_step": cur,
        "current_step_name": (current or {}).get("name") if current else None,
        "n_steps": len(steps),
        "tasks": tasks_for_run(run["id"]),
    }


def runs_for(subject_ref):
    """All runs over `subject_ref`, newest first, each with its workflow name. Never raises -> []."""
    subject_ref = (subject_ref or "").strip()
    if not subject_ref:
        return []
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("r.tenant_id")
            rows = con.execute(
                """SELECT r.*, w.name AS workflow_name
                   FROM workflow_runs r LEFT JOIN workflows w ON w.id=r.workflow_id
                   WHERE r.subject_ref=?""" + frag
                + " ORDER BY r.created_at DESC, r.id DESC",
                [subject_ref, *tp]).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("runs_for failed for %r: %s", subject_ref, e)
        return []


# ============================================================ step-action side-effects
def _fire_step_action(step, subject_ref, actor=None):
    """Best-effort side-effect for a step when its task is created (the run ENTERS the step):

      notify -> notify.send_alert(subject, lines)
      sign   -> esign.create_request(subject_ref, title, actor)
      tag    -> metadata.assign_tag(tag_id, subject_ref)   (params.tag is a tag NAME or id)
      approve-> no side-effect (a pure human gate)

    These reuse the EXISTING advisory seams and NEVER mutate a VAT figure/status/lock. Any
    failure is logged and swallowed — a side-effect hiccup must never block the run. Never
    raises."""
    try:
        action = (step.get("action") or "approve").strip().lower()
        params = step.get("params") or {}
        if action == "notify":
            return _do_notify(step, subject_ref, params)
        if action == "sign":
            return _do_sign(step, subject_ref, actor, params)
        if action == "tag":
            return _do_tag(subject_ref, params)
        return None   # approve / unknown -> nothing
    except Exception as e:
        log.warning("_fire_step_action(%r) failed for %r: %s",
                    (step or {}).get("action"), subject_ref, e)
        return None


def _do_notify(step, subject_ref, params):
    """Fire the `notify` step via notify.send_alert (best-effort)."""
    try:
        import notify
        subject = str(params.get("subject")
                      or f"Workflow task: {step.get('name') or 'review'}")
        line = str(params.get("message")
                   or f"A workflow step needs your attention on {subject_ref}.")
        recipients = params.get("recipients")   # optional override; else the team relay
        return notify.send_alert(subject, [line], recipients=recipients)
    except Exception as e:
        log.warning("workflow notify step failed for %r: %s", subject_ref, e)
        return None


def _do_sign(step, subject_ref, actor, params):
    """Fire the `sign` step by minting an esign signature_request (best-effort)."""
    try:
        import esign
        title = str(params.get("title") or step.get("name") or "Workflow signature")
        req, err = esign.create_request(subject_ref, title, actor or "workflow")
        if err:
            log.warning("workflow sign step: create_request failed for %r: %s",
                        subject_ref, err)
        return req
    except Exception as e:
        log.warning("workflow sign step failed for %r: %s", subject_ref, e)
        return None


def _do_tag(subject_ref, params):
    """Fire the `tag` step by assigning a metadata tag to the subject (best-effort). `params.tag`
    is a tag NAME (created if missing) or a numeric tag id."""
    try:
        import metadata
        tag_ref = params.get("tag")
        if tag_ref in (None, ""):
            return None
        tag_id = None
        # numeric -> an existing tag id; otherwise treat as a name (find-or-create).
        try:
            tag_id = int(tag_ref)
        except (TypeError, ValueError):
            tag_id = None
        if tag_id is None:
            name = str(tag_ref).strip()
            existing = next((t for t in metadata.flat_tags()
                             if (t.get("name") or "").strip().lower() == name.lower()), None)
            if existing:
                tag_id = existing["id"]
            else:
                tag, err = metadata.create_tag(name)
                if err or not tag:
                    log.warning("workflow tag step: create_tag failed: %s", err)
                    return None
                tag_id = tag["id"]
        ok, err = metadata.assign_tag(tag_id, subject_ref)
        if err:
            log.warning("workflow tag step: assign_tag failed for %r: %s", subject_ref, err)
        return ok
    except Exception as e:
        log.warning("workflow tag step failed for %r: %s", subject_ref, e)
        return None


if __name__ == "__main__":
    # offline smoke (uses the live workflow.db): define -> start -> act through the steps.
    wf, err = define_workflow("Smoke approval", [
        {"name": "Manager approve", "assignee": "admin", "action": "approve"},
        {"name": "Tag reviewed", "assignee": "admin", "action": "tag",
         "params": {"tag": "Workflow-reviewed"}},
    ])
    if err:
        print("define error:", err)
    else:
        run, e2 = start_run(wf["id"], "doc:smoke", "system")
        print("run:", (run or {}).get("id"), "status:", (run or {}).get("status"))
        pend = my_tasks(role="admin")
        print("pending tasks:", [t["id"] for t in pend])
        if pend:
            r2, _ = act_on_task(pend[0]["id"], "approve", "system", "looks good")
            print("after step 1:", (r2 or {}).get("status"))
            pend2 = my_tasks(role="admin")
            if pend2:
                r3, _ = act_on_task(pend2[0]["id"], "approve", "system")
                print("after step 2:", (r3 or {}).get("status"))
        print("status:", run_status((run or {}).get("id")))
