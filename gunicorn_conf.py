"""
GUNICORN CONFIG — run the Fleet Fuel app as MULTIPLE worker processes (Linux).

    pip install gunicorn
    gunicorn -c gunicorn_conf.py app:app          # several processes, shared .db files

Why this file exists: gunicorn imports `app:app` directly and never calls our
serve.py main(), so the background workers (auto-backup scheduler, intake-queue
drainer) would never start. The post_fork hook below starts them in EVERY worker
process. That's safe because the cross-process locks in proclock.py make the
scheduled backup a singleton (leader election) and the intake claim hands each
queued job to exactly one worker — so more workers just means more throughput.

SQLite is tuned for this in dbtune.py (WAL + busy_timeout) so concurrent worker
processes read/write the shared .db files without "database is locked". For a
large number of concurrent WRITERS, migrate to Postgres (see db.py) — the app
logic, audit, and locks are unchanged.

HTTPS: terminate TLS at a proxy (nginx / Caddy) in front of gunicorn.
Windows (no fork/gunicorn): run several `python serve.py` instances behind a
proxy, or one waitress with more THREADS — the same locks and PRAGMAs apply.
"""
import os, multiprocessing

bind = os.environ.get("BIND", "127.0.0.1:8050")
workers = int(os.environ.get("WORKERS", (multiprocessing.cpu_count() * 2) + 1))
threads = int(os.environ.get("THREADS", "4"))
worker_class = "gthread"
timeout = 120
graceful_timeout = 30


def post_fork(server, worker):
    # start the background workers once per worker process; proclock dedupes the
    # singletons across processes.
    from app import start_backup_scheduler, start_intake_worker
    start_backup_scheduler()
    start_intake_worker()
