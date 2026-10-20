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
same way. Coverage means a direct, unfiltered pytest invocation in the postgres
job with the appropriate PostgreSQL URL and backend working directory. This is
a deliberately small static contract, not an interpreter for arbitrary shell.
"""

from __future__ import annotations

import ast
import pathlib
import re
import shlex
from urllib.parse import urlsplit

import pytest
import yaml

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
CI = REPO / ".github" / "workflows" / "ci.yml"

PG_MARKERS = ("RLS_TEST_DATABASE_URL", "PERF_TEST_DATABASE_URL")


def _environment_reads(source: str) -> set[str]:
    """Find actual os environment reads, excluding comments/documentation.

    Recognize the repository's getenv/environ API, including import aliases;
    dynamically constructed variable names or custom environment wrappers need
    an explicit extension to this discovery contract.
    """
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "os":
                    aliases[name.asname or "os"] = "os"
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for name in node.names:
                if name.name in {"environ", "getenv"}:
                    aliases[name.asname or name.name] = f"os.{name.name}"

    def dotted(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{dotted(node.value)}.{node.attr}"
        return ""

    reads = set()
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Call) and dotted(node.func) in {"os.getenv", "os.environ.get"}:
            key = (
                node.args[0]
                if node.args
                else next((kw.value for kw in node.keywords if kw.arg == "key"), None)
            )
        elif isinstance(node, ast.Subscript) and dotted(node.value) == "os.environ":
            key = node.slice
        if isinstance(key, ast.Constant) and key.value in PG_MARKERS:
            reads.add(key.value)
    return reads


def _pg_only_test_files(marker: str) -> set[str]:
    out: set[str] = set()
    for path in (BACKEND / "tests").rglob("test_*.py"):
        source = path.read_text()
        if marker in source and marker in _environment_reads(source):
            out.add(path.relative_to(BACKEND).as_posix())
    return out


def _pytest_files(script: str) -> set[str]:
    """Recognize direct commands only, without shell control flow or filters.

    YAML folding and backslash continuations work; separate direct pytest
    commands work too. Shell wrappers, substitutions, redirects and selection
    filters are refused rather than credited as full-file execution.
    """
    files = set()
    for line in script.replace("\\\n", "").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            return set()
        if not tokens:
            continue
        if any(any(char in token for char in ";&|()<>$`\\") for token in tokens):
            return set()
        executable = pathlib.PurePosixPath(tokens[0]).name
        if re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", executable):
            if tokens[1:3] != ["-m", "pytest"]:
                return set()
            args = tokens[3:]
        elif executable == "pytest":
            args = tokens[1:]
        else:
            return set()
        paths = []
        for arg in args:
            if arg.startswith("-"):
                if arg not in {
                    "-ra",
                    "-rA",
                    "--disable-warnings",
                    "--strict-markers",
                } and not re.fullmatch(r"-[qvs]+", arg):
                    return set()
            else:
                if "::" in arg or any(char in arg for char in "*?[]{}"):
                    return set()
                paths.append(arg)
        for selection in paths or ["tests"]:
            path = (BACKEND / selection).resolve()
            if not path.is_relative_to(BACKEND / "tests"):
                return set()
            if path.is_dir():
                files.update(p.relative_to(BACKEND).as_posix() for p in path.rglob("test_*.py"))
            else:
                # Retain nonexistent selections so the stale-path gate fails.
                files.add(path.relative_to(BACKEND).as_posix())
    return files


def _postgres_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"postgres", "postgresql", "postgresql+asyncpg"} and bool(parsed.netloc)


def _files_named_in_ci(marker: str | None = None) -> set[str]:
    workflow = yaml.safe_load(CI.read_text())
    job = workflow.get("jobs", {}).get("postgres", {})
    if "if" in job or job.get("continue-on-error"):
        return set()
    files = set()
    for step in job.get("steps", []):
        if "run" not in step or "if" in step or step.get("continue-on-error"):
            continue
        environment = workflow.get("env", {}) | job.get("env", {}) | step.get("env", {})
        if environment.get("PYTEST_ADDOPTS"):
            # Hidden selection/collect-only flags invalidate direct-command
            # evidence too; keep options in the checked invocation itself.
            continue
        if marker is not None and not _postgres_url(environment.get(marker)):
            continue
        defaults = workflow.get("defaults", {}).get("run", {}) | job.get("defaults", {}).get(
            "run", {}
        )
        directory = step.get("working-directory", defaults.get("working-directory", "."))
        shell = step.get("shell", defaults.get("shell"))
        if (REPO / directory).resolve() != BACKEND or shell not in {None, "bash", "sh"}:
            continue
        files.update(_pytest_files(step["run"]))
    return files


def test_qa001_every_pg_only_test_file_is_run_by_the_postgres_job():
    required = {marker: _pg_only_test_files(marker) for marker in PG_MARKERS}
    pg_files = set().union(*required.values())
    assert len(pg_files) >= 10, f"pg-only test discovery looks broken: {sorted(pg_files)}"
    missing = {
        marker: sorted(files - _files_named_in_ci(marker))
        for marker, files in required.items()
        if files - _files_named_in_ci(marker)
    }
    assert missing == {}, (
        "these test files lack a direct unfiltered Postgres pytest invocation with their "
        f"required URL in the postgres job of .github/workflows/ci.yml: {missing}"
    )


def test_qa001_the_ci_list_names_no_file_that_does_not_exist():
    stale = sorted(f for f in _files_named_in_ci() if not (BACKEND / f).exists())
    assert stale == [], f"ci.yml names test files that no longer exist: {stale}"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            "          tests/test_verification_consume_pg.py\n",
            "          # tests/test_verification_consume_pg.py\n",
            id="comment-is-not-execution",
        ),
        pytest.param("  postgres:\n", "  unrelated_job:\n", id="wrong-job"),
        pytest.param(
            "RLS_TEST_DATABASE_URL: postgresql+asyncpg://",
            "UNRELATED_DATABASE_URL: postgresql+asyncpg://",
            id="wrong-rls-environment",
        ),
        pytest.param(
            "PERF_TEST_DATABASE_URL: postgresql+asyncpg://",
            "UNRELATED_DATABASE_URL: postgresql+asyncpg://",
            id="wrong-performance-environment",
        ),
        pytest.param(
            "RLS_TEST_DATABASE_URL: postgresql+asyncpg://appuser:apppw@localhost:5432/invoiceiq",
            'RLS_TEST_DATABASE_URL: ""',
            id="empty-environment",
        ),
        pytest.param(
            "RLS_TEST_DATABASE_URL: postgresql+asyncpg://appuser:apppw@localhost:5432/invoiceiq",
            "RLS_TEST_DATABASE_URL: sqlite+aiosqlite:///./test.db",
            id="sqlite-is-not-postgres",
        ),
        pytest.param(
            "          python -m pytest -q\n",
            "          echo python -m pytest -q\n",
            id="echo-is-not-pytest",
        ),
        pytest.param(
            "          python -m pytest -q\n",
            "          false && python -m pytest -q\n",
            id="skipped-shell-branch",
        ),
        pytest.param(
            "          python -m pytest -q\n",
            "          python -m pytest --collect-only -q\n",
            id="collection-is-not-execution",
        ),
        pytest.param(
            "          python -m pytest -q\n",
            "          python -m pytest -k never_match_a_test -q\n",
            id="filtered-selection-is-not-full-file-coverage",
        ),
        pytest.param(
            "          tests/test_verification_consume_pg.py\n",
            "          tests/test_verification_consume_pg.py::test_one_case\n",
            id="single-node-is-not-full-file-coverage",
        ),
        pytest.param(
            "  postgres:\n    runs-on:",
            "  postgres:\n    if: false\n    runs-on:",
            id="disabled-job",
        ),
        pytest.param(
            "      - name: Postgres-only tests (RLS enforcement + every concurrency guard)\n",
            "      - name: Postgres-only tests (RLS enforcement + every concurrency guard)\n        if: false\n",
            id="disabled-step",
        ),
    ],
)
def test_qa001_refuses_nonexecuting_postgres_coverage(tmp_path, monkeypatch, old, new):
    source = CI.read_text()
    assert old in source, "fixture must mutate the current executable registration"
    variant = tmp_path / "ci.yml"
    variant.write_text(source.replace(old, new))
    monkeypatch.setitem(globals(), "CI", variant)
    with pytest.raises(AssertionError, match="Postgres"):
        test_qa001_every_pg_only_test_file_is_run_by_the_postgres_job()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('"""RLS_TEST_DATABASE_URL"""\n# PERF_TEST_DATABASE_URL\n', set()),
        ('names = ("RLS_TEST_DATABASE_URL", "PERF_TEST_DATABASE_URL")', set()),
        ('import os\nos.environ.get("RLS_TEST_DATABASE_URL")', {PG_MARKERS[0]}),
        ('import os as operating\noperating.getenv("PERF_TEST_DATABASE_URL")', {PG_MARKERS[1]}),
        ('from os import environ as env\nenv["RLS_TEST_DATABASE_URL"]', {PG_MARKERS[0]}),
        ('from os import getenv as read\nread(key="PERF_TEST_DATABASE_URL")', {PG_MARKERS[1]}),
    ],
)
def test_qa001_discovery_tracks_environment_reads_not_explanatory_text(source, expected):
    assert _environment_reads(source) == expected


@pytest.fixture
def selected_tests(tmp_path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("test_first.py", "test_second.py"):
        (tests / name).write_text("def test_example(): pass\n")
    monkeypatch.setitem(globals(), "BACKEND", tmp_path)
    return {"tests/test_first.py", "tests/test_second.py"}


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q tests/test_first.py tests/test_second.py",
        "pytest -q tests/test_first.py\npython3 -m pytest tests/test_second.py -s",
        "python -m pytest \\\n  -q \\\n  tests/test_first.py \\\n  tests/test_second.py",
        "python -m pytest tests -q",
        "python -m pytest -q",
    ],
)
def test_qa001_direct_pytest_supports_folded_continued_and_directory_selection(
    command, selected_tests
):
    assert _pytest_files(command) == selected_tests


@pytest.mark.parametrize(
    "command",
    [
        "exit 0\npython -m pytest tests",
        "if false; then\npython -m pytest tests\nfi",
        "sh -c 'python -m pytest tests'",
        "printf '%s' 'python -m pytest tests'",
        "python -m pytest tests > /tmp/results",
        "python -m pytest tests || true",
    ],
)
def test_qa001_shell_wrappers_cannot_claim_executed_test_coverage(command, selected_tests):
    assert _pytest_files(command) == set()


def test_qa001_step_environment_overrides_inherited_url(tmp_path, monkeypatch):
    step = {"run": "python -m pytest tests/test_example.py", "env": {PG_MARKERS[0]: ""}}
    workflow = {
        "env": {PG_MARKERS[0]: "postgresql+asyncpg://example:example@localhost/test"},
        "jobs": {
            "postgres": {
                "defaults": {"run": {"working-directory": "backend"}},
                "steps": [step],
            }
        },
    }
    variant = tmp_path / "ci.yml"
    monkeypatch.setitem(globals(), "CI", variant)
    variant.write_text(yaml.safe_dump(workflow))
    assert _files_named_in_ci(PG_MARKERS[0]) == set()
    step.pop("env")
    variant.write_text(yaml.safe_dump(workflow))
    assert _files_named_in_ci(PG_MARKERS[0]) == {"tests/test_example.py"}
    step["working-directory"] = "frontend"
    variant.write_text(yaml.safe_dump(workflow))
    assert _files_named_in_ci(PG_MARKERS[0]) == set()


def test_qa001_executable_missing_selection_still_fails_stale_path_gate(tmp_path, monkeypatch):
    workflow = {
        "jobs": {
            "postgres": {
                "defaults": {"run": {"working-directory": "backend"}},
                "steps": [{"run": "python -m pytest tests/test_removed_qa001_fixture.py"}],
            }
        }
    }
    variant = tmp_path / "ci.yml"
    variant.write_text(yaml.safe_dump(workflow))
    monkeypatch.setitem(globals(), "CI", variant)
    with pytest.raises(AssertionError, match="test_removed_qa001_fixture.py"):
        test_qa001_the_ci_list_names_no_file_that_does_not_exist()


@pytest.mark.parametrize("scope", ["workflow", "job", "step"])
@pytest.mark.parametrize("options", ["--collect-only", "-k never_match_a_test"])
def test_qa001_inherited_pytest_options_cannot_suppress_execution(
    tmp_path, monkeypatch, scope, options
):
    source = yaml.safe_load(CI.read_text())
    job = source["jobs"]["postgres"]
    step = next(s for s in job["steps"] if "python -m pytest -q" in s.get("run", ""))
    target = {"workflow": source, "job": job, "step": step}[scope]
    target.setdefault("env", {})["PYTEST_ADDOPTS"] = options
    variant = tmp_path / "ci.yml"
    variant.write_text(yaml.safe_dump(source))
    monkeypatch.setitem(globals(), "CI", variant)
    with pytest.raises(AssertionError, match="Postgres"):
        test_qa001_every_pg_only_test_file_is_run_by_the_postgres_job()
