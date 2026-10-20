"""Isolated CI browser server; resource/query evidence, no application dependency."""

import json
import math
import os
import platform
import re
import resource
import signal
import time
from collections import defaultdict
from contextvars import ContextVar

import uvicorn
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.main import app

queries = ContextVar("r010_query_count", default=None)
observations = []
started = time.perf_counter()


@event.listens_for(Engine, "after_cursor_execute")
def count_query(*args):
    counter = queries.get()
    if counter is not None:
        counter[0] += 1


@app.middleware("http")
async def measure(request, call_next):
    counter = [0]
    token = queries.set(counter)
    before = time.perf_counter()
    try:
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/expenses"):
            observations.append({
                "method": request.method,
                "path": re.sub(r"[0-9a-f-]{36}", ":id", request.url.path),
                "status": response.status_code,
                "latency_ms": (time.perf_counter() - before) * 1000,
                "queries": counter[0],
                "response_bytes": int(response.headers.get("content-length", "0")),
            })
        return response
    finally:
        queries.reset(token)


if __name__ == "__main__":
    # Uvicorn replays the signal to the prior handler after graceful shutdown.
    # Keep control here so the resource evidence is emitted after that replay.
    signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000, lifespan="off")
    finally:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        groups = defaultdict(list)
        for observation in observations:
            groups[observation["method"] + " " + observation["path"]].append(
                observation["latency_ms"]
            )
        latency = {}
        for path, values in groups.items():
            ordered = sorted(values)
            latency[path] = {
                "attempts": len(values),
                "cold_first_ms": values[0],
                "p50_ms": ordered[math.ceil(len(values) * 0.5) - 1],
                "p95_ms": ordered[math.ceil(len(values) * 0.95) - 1],
            }
        print("R010_RESOURCE " + json.dumps({
            "python": platform.python_version(), "platform": platform.platform(),
            "visible_cpu_count": os.cpu_count(),
            "wall_seconds": time.perf_counter() - started,
            "cpu_user_seconds": usage.ru_utime, "cpu_system_seconds": usage.ru_stime,
            "peak_rss_kib_linux": usage.ru_maxrss,
            "concurrency": "one browser worker; ordinary SPA reads may overlap",
            "fixture": "synthetic CSV, one USD debit per source; no OCR/PDF/provider",
            "queue_age": "not applicable: these commands enqueue no jobs",
            "latency": latency, "requests": observations,
        }), flush=True)
