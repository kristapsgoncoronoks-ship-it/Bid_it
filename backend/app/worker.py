"""Background worker: drains the durable job queue.

Run one (or several) alongside the API:

    python -m app.worker

Each loop reclaims abandoned jobs (crashed workers), then processes ready jobs
until the queue is empty, then sleeps briefly. Multiple workers are safe — the
claim is atomic, so a job runs on exactly one worker at a time. The worker runs
UNSCOPED (no tenant context); each job is dispatched inside its own tenant scope.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket

from datetime import date

from app.core.database import SessionLocal
from app.services import job_handlers  # noqa: F401 — registers the handlers
from app.services import jobs, scheduler

log = logging.getLogger("invoiceiq.worker")

IDLE_SLEEP_SECONDS = 2.0
# How often (in loops) to run the stale-lease reclaim sweep.
_RECLAIM_EVERY = 10


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def run_forever(poll_interval: float = IDLE_SLEEP_SECONDS) -> None:
    worker_id = _worker_id()
    stop = asyncio.Event()

    def _request_stop(*_a):
        log.info("worker %s stopping…", worker_id)
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover — e.g. Windows
            pass

    log.info("worker %s started; handlers=%s", worker_id, jobs.registered_kinds())
    tick = 0
    last_scheduled: date | None = None
    while not stop.is_set():
        tick += 1
        try:
            async with SessionLocal() as db:
                if tick % _RECLAIM_EVERY == 1:
                    reclaimed = await jobs.reclaim_stale(db)
                    if reclaimed:
                        log.info("reclaimed %s stale job(s)", reclaimed)
                # Once per calendar day, enqueue the periodic jobs (idempotent).
                today = date.today()
                if last_scheduled != today:
                    created = await scheduler.enqueue_daily(db, today=today)
                    last_scheduled = today
                    if created:
                        log.info("scheduled %s daily job(s) for %s", created, today)
                # Drain everything currently ready before sleeping.
                processed = 0
                while not stop.is_set():
                    job = await jobs.run_once(db, worker_id)
                    if job is None:
                        break
                    processed += 1
                    log.info("job %s (%s) → %s", job.id, job.kind, job.status)
        except Exception:  # noqa: BLE001 — never let the loop die
            log.exception("worker loop error")
            processed = 0
        if processed == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass
    log.info("worker %s stopped", worker_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="InvoiceIQ background worker")
    parser.add_argument("--poll", type=float, default=IDLE_SLEEP_SECONDS,
                        help="seconds to sleep when the queue is empty")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_forever(args.poll))


if __name__ == "__main__":
    main()
