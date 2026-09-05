"""QA-001 (audit 2026-09-05) — every Postgres-only test file is named in the CI
step that runs Postgres.

The main `backend` job runs on SQLite, so every test gated on
`RLS_TEST_DATABASE_URL` skips there. The `postgres` job enumerated three files
by hand. Eight tests — the double-payment, double-credit, pooled-connection
RLS and concurrent-usage-counter guards, written precisely because SQLite
cannot express them — sat in files that list did not name and therefore ran in
NO job at all. A hand-kept list rots; this test is the gate `check-e2e.mjs`
already is for the Playwright list.

The perf gate (`PERF_TEST_DATABASE_URL`) has its own step and is asserted the
same way.
"""

from __future__ import annotations

import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
CI = REPO / ".github" / "workflows" / "ci.yml"

PG_MARKERS = ("RLS_TEST_DATABASE_URL", "PERF_TEST_DATABASE_URL")


def _pg_only_test_files() -> set[str]:
    out: set[str] = set()
    for path in (BACKEND / "tests").rglob("test_*.py"):
        text = path.read_text()
        if any(marker in text for marker in PG_MARKERS):
            out.add(path.relative_to(BACKEND).as_posix())
    return out


def _files_named_in_ci() -> set[str]:
    return set(re.findall(r"tests/(?:transport/)?test_[a-z0-9_]+\.py", CI.read_text()))


def test_qa001_every_pg_only_test_file_is_run_by_the_postgres_job():
    pg_files = _pg_only_test_files()
    assert len(pg_files) >= 10, f"pg-only test discovery looks broken: {sorted(pg_files)}"
    missing = sorted(pg_files - _files_named_in_ci())
    assert missing == [], (
        "these test files self-select on a Postgres URL and run in NO CI job — add them "
        f"to the postgres job's pytest step in .github/workflows/ci.yml: {missing}"
    )


def test_qa001_the_ci_list_names_no_file_that_does_not_exist():
    stale = sorted(f for f in _files_named_in_ci() if not (BACKEND / f).exists())
    assert stale == [], f"ci.yml names test files that no longer exist: {stale}"
