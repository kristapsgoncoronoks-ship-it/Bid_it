"""Worker liveness probe: is THIS worker process's loop still turning?

    python -m app.worker_probe        # exit 0 = alive, 1 = wedged/absent

The worker (`app.worker`) touches `WORKER_LIVENESS_PATH` at the top of every
loop tick, after every job, and on every lease heartbeat while a long job runs,
so a live worker's file is never older than the lease heartbeat interval (60 s)
plus one poll. The probe reads that file's age and compares it with
`WORKER_LIVENESS_MAX_AGE_SECONDS` (180). Wired as the compose `healthcheck`
and the Kubernetes `livenessProbe` of the worker container.

What it deliberately is NOT: the queue SLO. `/health/queue` answers 503 when
the oldest ready job is older than 15 min or a dead-letter exists — that is a
FLEET condition an uptime check pages a human on. Restarting every worker
replica because the queue is backed up would lose the in-flight jobs (they are
reclaimed 300 s later and re-run) without adding capacity, so a backlog must
never be a liveness failure.

What it detects: a dead process; a blocked event loop (a synchronous call
without a timeout, native code on the loop); a hang in the loop body OUTSIDE a
job — the reclaim sweep, the daily scheduler, the health snapshot, the claim —
including a database that accepts connections and never answers (that restart
churns until the database does; in-flight jobs are reclaimed). What it does
NOT detect: a handler that awaits forever — the lease heartbeat keeps ticking
on its behalf, by design (a live slow job must not be reclaimed), and
`run_once` has no per-job deadline (BE-024).

Kept import-light on purpose: the probe runs every minute in a second process
inside the worker container, and importing the application (models, handlers,
the engine) for a stat() call would cost ~1 s and ~150 MB per probe.
"""

from __future__ import annotations

import os
import sys
import time

DEFAULT_PATH = "/tmp/invoiceiq-worker-liveness"  # noqa: S108 — mirrors Settings.worker_liveness_path
DEFAULT_MAX_AGE_SECONDS = 180


def touch(path: str) -> None:
    """Record 'the loop turned now'. Creating the file and then bumping its
    mtime keeps this to two cheap syscalls on the hot loop; failures are the
    caller's to swallow (liveness bookkeeping must never fail business work)."""
    with open(path, "a"):  # noqa: PTH123 — create if absent; never truncate
        pass
    os.utime(path, None)


def check(
    path: str = DEFAULT_PATH,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """(alive, reason). Alive when the heartbeat file exists and is at most
    `max_age_seconds` old. A clock that reads the file as being in the future
    (an NTP step) counts as age 0 rather than as a failure."""
    now = time.time() if now is None else now
    try:
        mtime = os.stat(path).st_mtime
    except FileNotFoundError:
        return False, f"no heartbeat file at {path} (worker never ticked, or a different path)"
    age = max(0.0, now - mtime)
    if age > max_age_seconds:
        return False, f"heartbeat is {age:.0f}s old (limit {max_age_seconds:.0f}s): loop wedged"
    return True, f"heartbeat {age:.0f}s old"


def main(argv: list[str] | None = None) -> int:
    path = os.environ.get("WORKER_LIVENESS_PATH", DEFAULT_PATH)
    try:
        max_age = float(os.environ.get("WORKER_LIVENESS_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS))
    except ValueError:
        sys.stderr.write("WORKER_LIVENESS_MAX_AGE_SECONDS is not a number\n")
        return 2
    alive, reason = check(path, max_age)
    (sys.stdout if alive else sys.stderr).write(reason + "\n")
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
