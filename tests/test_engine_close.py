"""
Tests for the D5 monthly-close orchestrator (engine_close.py) and the supporting
callable refactor / period-stamped pickle.

Covers:
  1. close(period) runs the stages in EXACT order and produces a DB load + receipt
     control + a backup.
  2. a mid-stage failure HALTS the chain (later steps not called), is logged, and a
     re-run completes.
  3. a pickle stamped for a DIFFERENT period raises the clear period-mismatch error in
     build_master.build / history.load.
  4. process_lock re-entrancy is blocked: a second concurrent close can't acquire.
  5. importing history / build_master is SIDE-EFFECT-FREE (no DB / file written).
"""
import os
import pickle
import subprocess
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import consolidate
import build_master
import history
import engine_close
import process_lock
import import_log


# ---- tiny canonical fixture: ONE diesel line in the consolidate schema ----
def _fixture_rows():
    # FIELDS order: entity,supplier,country,vehicle,date,time,station,product,
    # product_group,qty,currency,net_local,vat_local,gross_local,net_eur,vat_eur,
    # net_eur_eff,note
    return [
        ["ENT", "Q8", "BE", "CAR1", "2026-05-10", "08:00", "Stat A", "Diesel",
         "Diesel", 500.0, "EUR", 700.0, 147.0, 847.0, 700.0, 147.0, 690.0, ""],
    ]


def _point_load_rows(monkeypatch, pkl):
    """Rebind consolidate.load_rows so callers that pass no path read `pkl`. The default
    path is frozen at def time, so patching the module attr alone wouldn't take."""
    real = consolidate.load_rows
    monkeypatch.setattr(consolidate, "load_rows",
                        lambda period, path=pkl: real(period, path=path))


@pytest.fixture()
def temp_close_env(tmp_path, monkeypatch):
    """Point every engine-close artifact (pickle, history DB, control DB, lock DB,
    import_log DB, backup dir) at a temp area and write a fresh period-stamped pickle.
    Returns the chosen period."""
    period = "2099-01"
    pkl = str(tmp_path / "consolidated_rows.pkl")
    hist_db = str(tmp_path / "fuel_history.db")

    # period-stamped pickle for `period`
    rows = _fixture_rows()
    consolidate._dump_pickle(rows, period, path=pkl)

    # build_master.build / history.load call consolidate.load_rows(period) with no path,
    # so it would read the real default PICKLE. Re-point load_rows at the temp pickle by
    # rebinding its default-path argument (frozen at def time).
    _point_load_rows(monkeypatch, pkl)
    monkeypatch.setattr(history, "DB", hist_db, raising=True)

    # invoice_control writes into fuel_history.db; point it at the temp DB too
    import invoice_control
    monkeypatch.setattr(invoice_control, "FUEL_HISTORY_DB", hist_db, raising=True)

    # isolate the singleton lock and the import_log into temp DBs
    monkeypatch.setattr(process_lock, "DB", str(tmp_path / "locks.db"), raising=True)
    process_lock._READY.clear()
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"), raising=True)
    import_log._READY.clear()

    return {"period": period, "pkl": pkl, "hist_db": hist_db, "tmp": tmp_path}


def test_close_runs_stages_in_order(temp_close_env, monkeypatch):
    period = temp_close_env["period"]
    calls = []

    # Spy each stage on the engine_close module. consolidate.run is a no-op here
    # (the pickle is already written by the fixture); the real history.load /
    # run_control / backup.snapshot run against temp DBs.
    real_history = history.load
    real_control = engine_close.invoice_control.run_control
    real_backup = engine_close.backup.snapshot

    def spy_consolidate(p=None):
        calls.append(("consolidate", p)); return _fixture_rows()

    def spy_build(p=None):
        calls.append(("build_master", p)); return "master.xlsx"

    def spy_history(p=None):
        calls.append(("history", p)); return real_history(p)

    def spy_control(p, persist=True):
        calls.append(("invoice_control", p, persist)); return real_control(p, persist=persist)

    def spy_backup():
        calls.append(("backup",)); return ("backup.zip", 1)

    monkeypatch.setattr(engine_close.consolidate, "run", spy_consolidate)
    monkeypatch.setattr(engine_close.build_master, "build", spy_build)
    monkeypatch.setattr(engine_close.history, "load", spy_history)
    monkeypatch.setattr(engine_close.invoice_control, "run_control", spy_control)
    monkeypatch.setattr(engine_close.backup, "snapshot", spy_backup)

    results = engine_close.close(period=period, actor="tester")

    # exact order
    order = [c[0] for c in calls]
    assert order == ["consolidate", "build_master", "history",
                     "invoice_control", "backup"]
    # control persisted, backup produced
    assert ("invoice_control", period, True) in calls
    assert ("backup",) in calls
    assert results["backup"] == ("backup.zip", 1)

    # transactions for the period actually landed in the temp history DB
    import sqlite3
    con = sqlite3.connect(temp_close_env["hist_db"])
    n = con.execute("SELECT COUNT(*) FROM transactions WHERE period=?", (period,)).fetchone()[0]
    con.close()
    assert n == 1

    # per-step close events logged (started + ok for each stage)
    rows = import_log.recent(channel="close")
    msgs = [r["message"] for r in rows]
    assert any("consolidate: ok" in m for m in msgs)
    assert any("backup: ok" in m for m in msgs)


def test_mid_stage_failure_halts_and_is_restartable(temp_close_env, monkeypatch):
    period = temp_close_env["period"]
    calls = []
    real_history = history.load

    state = {"fail": True}

    def spy_consolidate(p=None):
        calls.append("consolidate"); return _fixture_rows()

    def spy_build(p=None):
        calls.append("build_master"); return "master.xlsx"

    def spy_history(p=None):
        calls.append("history")
        if state["fail"]:
            raise RuntimeError("boom in history")
        return real_history(p)

    def spy_control(p, persist=True):
        calls.append("invoice_control")

    def spy_backup():
        calls.append("backup"); return ("backup.zip", 1)

    monkeypatch.setattr(engine_close.consolidate, "run", spy_consolidate)
    monkeypatch.setattr(engine_close.build_master, "build", spy_build)
    monkeypatch.setattr(engine_close.history, "load", spy_history)
    monkeypatch.setattr(engine_close.invoice_control, "run_control", spy_control)
    monkeypatch.setattr(engine_close.backup, "snapshot", spy_backup)

    with pytest.raises(RuntimeError) as ei:
        engine_close.close(period=period, actor="tester")
    assert "history" in str(ei.value)
    # later steps NOT reached
    assert "invoice_control" not in calls
    assert "backup" not in calls
    # failure logged
    failed = [r for r in import_log.recent(channel="close") if r["status"] == "failed"]
    assert any(r["source_name"] == "history" for r in failed)

    # the lock must have been released in the finally — re-run can proceed
    assert process_lock.held_by(engine_close.LOCK_NAME) is None

    # re-run now succeeds end-to-end
    state["fail"] = False
    calls.clear()
    results = engine_close.close(period=period, actor="tester")
    assert calls == ["consolidate", "build_master", "history", "invoice_control", "backup"]
    assert results["backup"] == ("backup.zip", 1)


def test_pickle_period_mismatch_raises(tmp_path, monkeypatch):
    pkl = str(tmp_path / "consolidated_rows.pkl")
    consolidate._dump_pickle(_fixture_rows(), "2026-05", path=pkl)
    _point_load_rows(monkeypatch, pkl)

    # build_master.build reads the pickle for the REQUESTED period and asserts the stamp
    monkeypatch.setattr(history, "DB", str(tmp_path / "fuel_history.db"), raising=True)

    with pytest.raises(RuntimeError) as ei:
        build_master.build("2026-06")
    assert "period" in str(ei.value) and "re-run consolidate" in str(ei.value)

    with pytest.raises(RuntimeError) as ei2:
        history.load("2026-06")
    assert "period" in str(ei2.value)


def test_concurrent_close_blocked_by_lock(temp_close_env, monkeypatch):
    period = temp_close_env["period"]
    # another holder already owns the close lease
    assert process_lock.acquire(engine_close.LOCK_NAME, ttl=60, holder="other-operator")

    with pytest.raises(RuntimeError) as ei:
        engine_close.close(period=period, actor="tester")
    assert "already running" in str(ei.value)

    process_lock.release(engine_close.LOCK_NAME, "other-operator")


def test_importing_engine_modules_is_side_effect_free(tmp_path):
    """Importing history / build_master must NOT touch a DB or write a file. Run in a
    fresh interpreter in an EMPTY cwd so any stray write would show up as a new file."""
    code = (
        "import os, sys;"
        f"sys.path.insert(0, {WORKDIR!r});"
        "before=set(os.listdir('.'));"
        "import history, build_master, consolidate, engine_close;"
        "after=set(os.listdir('.'));"
        "assert before==after, ('files created on import: %s' % (after-before));"
        "print('clean')"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "clean" in proc.stdout
