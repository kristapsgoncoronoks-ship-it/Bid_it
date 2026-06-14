"""
ENGINE CLOSE — the monthly-close ORCHESTRATOR (decoupling order D5).

The monthly close used to be a hand-run CLI chain that an operator typed step by step:

    python consolidate.py → python build_master.py → python history.py
        → python invoice_control.py <period> → python backup.py

That chain had two reliability defects this module fixes:
  • #3 (stale-pickle period mismatch): the steps each read month_config.PERIOD
    INDEPENDENTLY, so editing month_config between steps silently stamped the new
    period onto old rows. consolidate now writes a PERIOD-STAMPED pickle and every
    downstream stage ASSERTS the stamp matches the period it was asked to load.
  • #7 (non-atomic, import-time close): history.py ran its whole load at MODULE
    IMPORT, so any failure aborted with a half-built workbook and no restart point.
    Each stage is now a CALLABLE (consolidate.run / build_master.build / history.load),
    and importing those modules is SIDE-EFFECT-FREE.

This module runs the stages IN ORDER as ONE guarded unit of work:

    close(period) =
        consolidate.run(period)
        → build_master.build(period)
        → history.load(period)
        → metrics.rebuild(period)
        → invoice_control.run_control(period, persist=True)
        → backup.snapshot()

Guard / audit / restart semantics
---------------------------------
• SINGLETON: the whole run is held under a process_lock lease ("close-run"), so two
  operators (or worker processes) can't interleave a close. A second concurrent close
  fails fast with a clear message instead of corrupting a half-written period.
• SINGLE AUDIT TRAIL: audit.set_actor(None, actor) wraps the whole run (reset in a
  finally), so every audited write during the close is attributed to the same actor.
• PER-STEP EVENT LOG: each stage emits an import_log event on channel "close"
  (status started / success / failed) — a durable record of what ran and how far it got.
• HALT ON FIRST FAILURE: the chain stops at the first hard error, names the failed
  step, and reports that re-running from the start is safe.

RESTARTABILITY: every stage is idempotent, so close() is safe to re-run end-to-end
after a failure (no manual cleanup):
  • consolidate.run     — re-reads the supplier workbooks and OVERWRITES the pickle.
  • build_master.build  — regenerates Fleet_Fuel_Master_<period>.xlsx (overwrite).
  • history.load        — DELETE-by-period + INSERT into fuel_history.db (replace,
                          never duplicate), then regenerates the history report.
  • metrics.rebuild     — REPLACEs the period's rows in settled_metrics (the
                          materialized dashboard aggregates), recomputed via queries.py.
  • run_control(persist)— recomputes and UPSERTs invoice_receipt_control (manual
                          waived/note overrides survive).
  • backup.snapshot     — writes a fresh, timestamped backup zip.

SCOPE: this is the ENGINE-SIDE orchestrator only. There is intentionally NO app
`/close` route here (deferred); the existing standalone CLIs keep working unchanged.

Headless run:  python engine_close.py [period]      (defaults to month_config.PERIOD)
"""
import sys

import applog
import audit
import import_log
import month_config
import process_lock

# The stage callables. Importing each of these is side-effect-free (D5 #7).
import consolidate
import build_master
import history
import metrics
import invoice_control
import backup

_log = applog.get("close")

# A close can take a while (consolidate reads every supplier workbook, history rebuilds
# the report). Hold the lease comfortably longer than a run; it simply EXPIRES if this
# process dies, so a crashed close never blocks the next operator forever.
LOCK_NAME = "close-run"
LOCK_TTL = 3600  # seconds


def _step(name, period, actor, fn):
    """Run one close stage, bracketing it with import_log started/success/failed
    events on channel 'close'. Re-raises on failure so close() halts the chain."""
    import_log.log("close", name, "received", actor=actor, period=period,
                   message=f"{name}: started")
    try:
        result = fn()
    except SystemExit as e:
        # Stages are library calls now, not CLIs — ANY SystemExit means the stage bailed
        # (e.g. consolidate.run() raises SystemExit(1) on a validation failure). Never let
        # the close press on past a stage that exited, even on a 0/None code.
        code = 0 if e.code is None else e.code
        _log.error("close step %s FAILED for period %s (exit %s)", name, period, code)
        import_log.log("close", name, "failed", actor=actor, period=period,
                       message=f"{name}: validation/exit failure ({code})")
        raise RuntimeError(
            f"close halted at step '{name}' for period {period}: validation/exit "
            f"failure ({code}). Fix the inputs and re-run — the close is safe to "
            f"restart from the beginning.") from e
    except Exception as e:
        _log.exception("close step %s FAILED for period %s", name, period)
        import_log.log("close", name, "failed", actor=actor, period=period,
                       message=f"{name}: {type(e).__name__}: {e}")
        raise RuntimeError(
            f"close halted at step '{name}' for period {period}: {type(e).__name__}: {e}. "
            f"Re-running the close from the beginning is safe (every step is idempotent)."
        ) from e
    import_log.log("close", name, "success", actor=actor, period=period,
                   message=f"{name}: ok")
    return result


def close(period=None, actor="system"):
    """Run the monthly close for `period` (default month_config.PERIOD) as ONE guarded,
    audited, restartable unit of work. Returns a dict of stage results
    {rows, master, history, metrics, control, backup}. Raises RuntimeError if the singleton lock
    is already held, or naming the first stage that fails (the chain halts there).

    See the module docstring for the order, guard, audit and restart semantics.
    """
    period = period or month_config.PERIOD
    holder = process_lock.whoami()
    if not process_lock.acquire(LOCK_NAME, ttl=LOCK_TTL, holder=holder):
        owner = process_lock.held_by(LOCK_NAME)
        raise RuntimeError(
            f"another close is already running (lock '{LOCK_NAME}' held by {owner}); "
            f"refusing to interleave — try again once it finishes.")
    _log.info("close STARTED for period %s (actor=%s, holder=%s)", period, actor, holder)
    audit.set_actor(None, actor)
    results = {}
    try:
        results["rows"]    = _step("consolidate", period, actor, lambda: consolidate.run(period))
        results["master"]  = _step("build_master", period, actor, lambda: build_master.build(period))
        results["history"] = _step("history", period, actor, lambda: history.load(period))
        # Settle the per-period dashboard aggregates AFTER history (transactions must be
        # loaded first). Idempotent (REPLACE), so a restart re-settles cleanly.
        results["metrics"] = _step("metrics", period, actor, lambda: metrics.rebuild(period))
        results["control"] = _step("invoice_control", period, actor,
                                    lambda: invoice_control.run_control(period, persist=True))
        results["backup"]  = _step("backup", period, actor, lambda: backup.snapshot())
    finally:
        audit.reset_actor()
        process_lock.release(LOCK_NAME, holder)
    _log.info("close COMPLETE for period %s", period)
    print(f"close COMPLETE for period {period}")
    return results


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    period = argv[0] if argv else None
    close(period=period, actor="cli")


if __name__ == "__main__":
    main()
