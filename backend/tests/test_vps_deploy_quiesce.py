"""Run the shipped deployment Bash against isolated command and writer fixtures.

Docker, Git, disk and clock operations are stubs; gzip/tar validate real fixture
backups. No daemon, registry, SSH target, customer volume or HOME override is used.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/vps-deploy.sh"

STUB = r"""
import argparse, io, json, os, sys, tarfile, time
from pathlib import Path

state_path = Path(os.environ["BIDIT_TEST_STATE"])
state = json.loads(state_path.read_text())
name = Path(sys.argv[0]).name
args = sys.argv[1:]

def record(event, **details):
    with open(os.environ["BIDIT_TEST_CALLS"], "a") as stream:
        stream.write(json.dumps({"event": event, "args": args,
                                 "running": dict(state["running"]), **details}) + "\n")

def save():
    state_path.write_text(json.dumps(state))

def fail(stage):
    code = int(os.environ.get("BIDIT_TEST_FAIL_" + stage.upper(), "0"))
    if code:
        print("synthetic " + stage + " failure", file=sys.stderr)
        sys.exit(code)

def hold(event):
    marker = os.environ.get("BIDIT_TEST_HOLD")
    if not marker or os.environ.get("BIDIT_TEST_HOLD_EVENT", "build") != event:
        return
    release = Path(marker)
    Path(marker + ".entered").touch()
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() > deadline:
            sys.exit(89)
        time.sleep(0.01)

if name == "df":
    record("disk")
    print("Filesystem 1048576-blocks Used Available Capacity Mounted on")
    print("fixture 10000 1000 9000 10% /")
elif name == "date":
    if args == ["+%s"]:
        state["clock"] = state.get("clock", 100000) + 600
        save()
        print(state["clock"])
    else:
        print("2026-10-20-1200")
elif name == "sleep":
    record("sleep")
elif name == "git":
    record("git")
    if args[:2] == ["rev-parse", "--abbrev-ref"]:
        print("main")
    elif args and args[0] == "rev-parse":
        print("1" * 40)
    elif args and args[0] == "log":
        print("synthetic candidate")
elif name == "pg_dump":
    # Exercise the shell's argv formation, rather than matching its source text.
    parser = argparse.ArgumentParser()
    parser.add_argument("-U", "--username")
    parser.add_argument("-d", "--dbname")
    parser.add_argument("database", nargs="?")
    parsed = parser.parse_args(args)
    actual_username = parsed.username if parsed.username is not None else os.environ.get("PGUSER")
    actual_database = parsed.dbname if parsed.dbname is not None else parsed.database
    if actual_database is None:
        actual_database = os.environ.get("PGDATABASE")
    record("dump_argv", username=actual_username, database=actual_database)
    server = json.loads(os.environ["BIDIT_TEST_DB_SERVER"])
    if actual_username != server["username"] or actual_database != server["database"]:
        print("synthetic database identity refused", file=sys.stderr)
        sys.exit(78)
    print("-- synthetic SQL", flush=True)
    fail("dump_stream")
    if not os.environ.get("BIDIT_TEST_TRUNCATED_DUMP"):
        print("-- PostgreSQL database dump complete\n--\n")
elif name == "docker":
    if args and args[0] == "run":
        record("backup_docs")
        fail("docs_backup")
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            entry = tarfile.TarInfo("synthetic-document.txt")
            entry.size = 5
            archive.addfile(entry, io.BytesIO(b"bytes"))
        sys.stdout.buffer.write(data.getvalue())
        sys.exit(0)
    if not args or args[0] != "compose":
        record("forbidden_docker")
        sys.exit(90)
    options = args[1:]
    while options and options[0] in ("-f", "--file", "--env-file", "-p", "--profile"):
        options = options[2:]
    if not options:
        sys.exit(91)
    command, rest = options[0], options[1:]
    writer_names = ("backend", "worker", "calendar-worker")
    services = [item for item in rest if item in writer_names]
    if command == "build":
        record("build")
        hold("build")
        fail("build")
    elif command == "images":
        record("old_images")
        selected = [item for item in rest if item in (*writer_names, "frontend", "db")]
        for service in selected:
            if service == "calendar-worker" and service not in state["running"]:
                continue
            print("sha256:" + {"backend": "1", "worker": "2", "frontend": "3", "db": "4", "calendar-worker": "5"}[service] * 64)
    elif command == "ps":
        record("writers_query" if "--status" in rest else "status")
        if "--status" in rest:
            if state.get("stop_attempted"):
                fail("verify")
            statuses = [rest[index + 1] for index, value in enumerate(rest)
                        if value == "--status"]
            live = [item for item in (services or writer_names)
                    if state["running"].get(item, False) and "running" in statuses]
            if state.get("stop_attempted") and os.environ.get("BIDIT_TEST_STILL_RUNNING"):
                leftover = os.environ.get("BIDIT_TEST_STILL_RUNNING_SERVICE", "worker")
                live = [leftover] if leftover in services and "running" in statuses else []
            print("\n".join(item if "--services" in rest else item + "-old"
                            for item in live))
            if (state.get("stop_attempted") and "--all" in rest
                    and os.environ.get("BIDIT_TEST_ONE_OFF_STATUS") in statuses):
                leftover = os.environ.get("BIDIT_TEST_ONE_OFF_SERVICE", "worker")
                if leftover in services:
                    print(leftover + "-one-off-old")
    elif command == "stop":
        record("stop", services=services)
        state["stop_attempted"] = True
        for service in services:
            if service in state["running"]:
                state["running"][service] = False
        save()
        fail("stop")
    elif command == "start":
        record("resume", services=services)
        fail("resume")
        for service in services:
            state["running"][service] = True
        save()
    elif command == "exec" and "db" in rest:
        record("backup_db")
        hold("backup_db")
        fail("db_backup")
        container_args = rest[rest.index("db") + 1:]
        # A real Compose exec inherits the container environment, not arbitrary
        # host credentials or libpq defaults from the test runner.
        container_env = {key: value for key, value in os.environ.items()
                         if key.startswith("BIDIT_TEST_")
                         or key in ("PATH", "LANG", "LC_ALL", "PYTHONUTF8")}
        container_env.update(json.loads(os.environ["BIDIT_TEST_DB_ENV"]))
        os.execvpe(container_args[0], container_args, container_env)
    elif command == "exec" and "alembic" in rest:
        record("migration_status")
        print("synthetic-head (head)")
    elif command == "exec":
        record("health")
        fail("health")
    elif command == "up":
        record("promote")
        state["promotion_attempted"] = True
        save()
        fail("up")
        state["running"].update({"backend": True, "worker": True})
        if "calendar" in os.environ.get("COMPOSE_PROFILES", "").split(","):
            state["running"]["calendar-worker"] = True
        save()
    elif command == "logs":
        record("logs")
        print("synthetic logs")
    else:
        record("forbidden_compose")
        sys.exit(92)
else:
    sys.exit(93)
"""


def _prepare(
    tmp_path,
    *,
    running=("backend", "worker"),
    calendar_worker=False,
    calendar_profile=False,
    still_running=False,
    still_running_service="worker",
    one_off=None,
    one_off_service="worker",
    db_user="invoiceiq",
    db_name="invoiceiq",
    missing_db_env=(),
    empty_db_env=(),
    truncated_db_dump=False,
    **failures,
):
    # The script must explicitly support temporary backups. Never redirect HOME
    # merely to make an unsafe historical implementation executable in a test.
    assert "BIDIT_DEPLOY_BACKUP_DIR" in SCRIPT.read_text(), (
        "Deployment script lacks the dedicated safe backup-directory contract"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("git", "docker", "df", "date", "sleep", "pg_dump"):
        executable = tools / name
        executable.write_text(f"#!{sys.executable}\n" + STUB)
        executable.chmod(0o755)
    (tmp_path / ".env").write_text("SECRET_KEY=synthetic-only\n")
    (tmp_path / "docker-compose.hostinger.yml").write_text(
        "services:\n  backend:\n    environment:\n      SECRET_KEY: ${SECRET_KEY:?set it}\n"
    )
    backups = tmp_path / "backups"
    backups.mkdir()
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "running": {
                    service: service in running
                    for service in (
                        ("backend", "worker", "calendar-worker")
                        if calendar_worker or "calendar-worker" in running
                        else ("backend", "worker")
                    )
                },
            }
        )
    )
    calls_path = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
        "BIDIT_TEST_STATE": str(state_path),
        "BIDIT_TEST_CALLS": str(calls_path),
        "BIDIT_DEPLOY_BACKUP_DIR": str(backups),
        "BIDIT_TEST_DB_SERVER": json.dumps({"username": db_user, "database": db_name}),
        "BIDIT_TEST_DB_ENV": json.dumps(
            {
                key: "" if key in empty_db_env else value
                for key, value in {"POSTGRES_USER": db_user, "POSTGRES_DB": db_name}.items()
                if key not in missing_db_env
            }
        ),
        **{f"BIDIT_TEST_FAIL_{key.upper()}": str(value) for key, value in failures.items()},
    }
    if still_running:
        env["BIDIT_TEST_STILL_RUNNING"] = "1"
        env["BIDIT_TEST_STILL_RUNNING_SERVICE"] = still_running_service
    if one_off:
        env["BIDIT_TEST_ONE_OFF_STATUS"] = one_off
        env["BIDIT_TEST_ONE_OFF_SERVICE"] = one_off_service
    # The isolated fixture, not the developer's shell, owns this opt-in.
    env.pop("COMPOSE_PROFILES", None)
    if calendar_profile:
        env["COMPOSE_PROFILES"] = "calendar"
    if truncated_db_dump:
        env["BIDIT_TEST_TRUNCATED_DUMP"] = "1"
    return env, state_path, calls_path, backups


def _run(tmp_path, **options):
    env, state_path, calls_path, backups = _prepare(tmp_path, **options)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", str(SCRIPT), "main"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert not any(call["event"].startswith("forbidden_") for call in calls), calls
    return result, calls, json.loads(state_path.read_text()), backups


def _events(calls):
    return [call["event"] for call in calls]


def test_deploy_builds_then_quiesces_and_verifies_before_backup_and_migration(tmp_path):
    result, calls, state, backups = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    events = _events(calls)
    assert "build" in events, "Images must build before any writer is stopped"
    assert "stop" in events, "Old API/extract writers must stop before schema changes"
    assert events.index("build") < events.index("stop") < events.index("backup_db")
    assert events.index("backup_db") < events.index("backup_docs") < events.index("promote")
    verification = [
        i
        for i, call in enumerate(calls)
        if call["event"] == "writers_query" and i > events.index("stop")
    ]
    assert verification and verification[0] < events.index("backup_db")
    assert next(call for call in calls if call["event"] == "build")["running"] == {
        "backend": True,
        "worker": True,
    }
    for call in calls:
        if call["event"] in ("backup_db", "backup_docs", "promote"):
            assert not any(call["running"].values()), call
    promote = next(call for call in calls if call["event"] == "promote")
    assert "--no-build" in promote["args"] and "--build" not in promote["args"]
    assert "resume" not in events
    assert all(state["running"].values())
    assert list(backups.glob("*.sql.gz")) and list(backups.glob("*.tar.gz"))
    image_manifest = list(backups.glob("pre-deploy-images-*.txt"))
    assert image_manifest, "Record prior container images before rebuilding mutable image names"
    image_text = image_manifest[0].read_text()
    for digit in ("1", "2", "3", "4"):
        assert "sha256:" + digit * 64 in image_text
    assert all(
        index < events.index("build") for index, event in enumerate(events) if event == "old_images"
    )
    for artifact in backups.iterdir():
        assert artifact.stat().st_mode & 0o077 == 0, artifact


def test_failed_image_build_does_not_stop_or_back_up_live_writers(tmp_path):
    result, calls, state, _ = _run(tmp_path, build=41)
    assert result.returncode == 41, result.stdout + result.stderr
    assert "build" in _events(calls)
    assert not set(_events(calls)) & {"stop", "backup_db", "backup_docs", "promote", "resume"}
    assert all(state["running"].values())


@pytest.mark.parametrize(
    "db_user,db_name",
    [
        ("invoiceiq", "invoiceiq"),
        ("archive_operator", "archive_records"),
        ("archive_operator", "archive=records"),
        ("archive role;$(touch injected-role)", "archive db;$(touch injected-db) * ' \""),
    ],
)
def test_backup_uses_effective_container_identity_as_literal_arguments(tmp_path, db_user, db_name):
    result, calls, _, _ = _run(tmp_path, db_user=db_user, db_name=db_name)
    assert result.returncode == 0, result.stdout + result.stderr
    dump = next(call for call in calls if call["event"] == "dump_argv")
    assert (dump["username"], dump["database"]) == (db_user, db_name)
    assert not (tmp_path / "injected-role").exists()
    assert not (tmp_path / "injected-db").exists()
    assert "promote" in _events(calls)


@pytest.mark.parametrize("missing", ["POSTGRES_USER", "POSTGRES_DB"])
def test_missing_backup_identity_refuses_before_migration_and_resumes_previous_writers(
    tmp_path, missing
):
    result, calls, state, _ = _run(tmp_path, missing_db_env=(missing,), running=("worker",))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "backup_db" in _events(calls)
    assert not set(_events(calls)) & {"dump_argv", "backup_docs", "promote"}
    resumes = [call["services"] for call in calls if call["event"] == "resume"]
    assert resumes == [["worker"]]
    assert state["running"] == {"backend": False, "worker": True}


@pytest.mark.parametrize("empty", ["POSTGRES_USER", "POSTGRES_DB"])
def test_empty_backup_identity_refuses_before_client_and_resumes_previous_writers(tmp_path, empty):
    result, calls, state, _ = _run(tmp_path, empty_db_env=(empty,), running=("worker",))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "backup_db" in _events(calls)
    assert not set(_events(calls)) & {"dump_argv", "backup_docs", "promote"}
    resumes = [call["services"] for call in calls if call["event"] == "resume"]
    assert resumes == [["worker"]]
    assert state["running"] == {"backend": False, "worker": True}


def test_dump_stream_failure_preserves_exit_and_never_starts_migrations(tmp_path):
    result, calls, state, backups = _run(tmp_path, dump_stream=52)
    assert result.returncode == 52, result.stdout + result.stderr
    assert "dump_argv" in _events(calls)
    assert not set(_events(calls)) & {"backup_docs", "promote"}
    assert "resume" in _events(calls)
    assert all(state["running"].values())
    assert list(backups.glob("*.sql.gz")), "Partial dump evidence remains private for recovery"


def test_truncated_successful_dump_is_refused_before_document_backup_or_migration(tmp_path):
    result, calls, state, _ = _run(tmp_path, truncated_db_dump=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "dump_argv" in _events(calls)
    assert not set(_events(calls)) & {"backup_docs", "promote"}
    assert "resume" in _events(calls)
    assert all(state["running"].values())
    assert "truncated or empty" in result.stderr


@pytest.mark.parametrize(
    "stage,code", [("stop", 42), ("verify", 43), ("db_backup", 44), ("docs_backup", 45)]
)
def test_pre_promotion_failure_cannot_start_migrations_and_preserves_status(tmp_path, stage, code):
    result, calls, state, _ = _run(tmp_path, **{stage: code})
    assert result.returncode == code, result.stdout + result.stderr
    assert "promote" not in _events(calls)
    assert "resume" in _events(calls), "A failed preparation must restore the old stopped writers"
    assert all(state["running"].values())


def test_running_writer_after_stop_refuses_backups_and_migrations(tmp_path):
    result, calls, _, _ = _run(tmp_path, still_running=True)
    assert result.returncode != 0
    assert not set(_events(calls)) & {"backup_db", "backup_docs", "promote"}
    assert "resume" in _events(calls)


@pytest.mark.parametrize("status", ["running", "restarting", "paused"])
def test_leftover_one_off_writer_refuses_backups_and_migrations(tmp_path, status):
    result, calls, _, _ = _run(tmp_path, one_off=status)
    assert result.returncode != 0
    assert not set(_events(calls)) & {"backup_db", "backup_docs", "promote"}
    queries = [call for call in calls if call["event"] == "writers_query" and "-q" in call["args"]]
    assert queries and "--all" in queries[-1]["args"]
    assert "resume" in _events(calls)


def _start_held(tmp_path, *, event):
    env, state_path, calls_path, backups = _prepare(tmp_path)
    release = tmp_path / "release"
    env.update({"BIDIT_TEST_HOLD": str(release), "BIDIT_TEST_HOLD_EVENT": event})
    process = subprocess.Popen(
        ["bash", "--noprofile", "--norc", str(SCRIPT), "main"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    entered = tmp_path / "release.entered"
    deadline = time.monotonic() + 5
    while not entered.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    if not entered.exists():
        release.touch()
        output = process.communicate(timeout=5)
        pytest.fail(f"Script never reached held {event}: {output}")
    return process, env, release, state_path, calls_path, backups


def test_concurrent_invocation_is_refused_without_touching_first_maintenance(tmp_path):
    process, env, release, state_path, calls_path, _ = _start_held(tmp_path, event="build")
    try:
        before = calls_path.read_text()
        second = subprocess.run(
            ["bash", "--noprofile", "--norc", str(SCRIPT), "main"],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        assert second.returncode != 0, second.stdout + second.stderr
        assert calls_path.read_text() == before
        assert all(json.loads(state_path.read_text())["running"].values())
        assert "another" in (second.stdout + second.stderr).lower()
    finally:
        release.touch()
        output = process.communicate(timeout=10)
    assert process.returncode == 0, output
    events = _events([json.loads(line) for line in calls_path.read_text().splitlines()])
    assert events.count("build") == events.count("stop") == events.count("promote") == 1


@pytest.mark.parametrize(
    "signum,code", [(signal.SIGHUP, 129), (signal.SIGINT, 130), (signal.SIGTERM, 143)]
)
def test_signal_during_pre_promotion_backup_resumes_only_old_writers(tmp_path, signum, code):
    process, _, release, state_path, calls_path, _ = _start_held(tmp_path, event="backup_db")
    try:
        assert not any(json.loads(state_path.read_text())["running"].values())
        process.send_signal(signum)
    finally:
        release.touch()
        output = process.communicate(timeout=10)
    assert process.returncode == code, output
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert "resume" in _events(calls) and "promote" not in _events(calls)
    assert all(json.loads(state_path.read_text())["running"].values())


@pytest.mark.parametrize("initial", [("backend",), ("worker",), ()])
def test_pre_promotion_recovery_starts_only_previously_running_services(tmp_path, initial):
    result, calls, state, _ = _run(tmp_path, running=initial, db_backup=44)
    assert result.returncode == 44, result.stdout + result.stderr
    resumed = [
        service for call in calls if call["event"] == "resume" for service in call["services"]
    ]
    assert sorted(resumed) == sorted(initial)
    assert state["running"] == {service: service in initial for service in ("backend", "worker")}
    assert "promote" not in _events(calls)


def test_resume_failure_preserves_primary_failure_and_explains_manual_recovery(tmp_path):
    result, calls, _, _ = _run(tmp_path, db_backup=44, resume=71)
    assert result.returncode == 44, result.stdout + result.stderr
    assert "resume" in _events(calls) and "promote" not in _events(calls)
    output = (result.stdout + result.stderr).lower()
    assert any(word in output for word in ("manual", "manually")), output
    assert any(word in output for word in ("resume", "restart", "start")), output


@pytest.mark.parametrize("stage,code", [("up", 46), ("health", 47)])
def test_promotion_or_health_failure_never_restarts_old_writers(tmp_path, stage, code):
    result, calls, state, _ = _run(tmp_path, **{stage: code})
    assert result.returncode != 0
    if stage == "up":
        assert result.returncode == code, result.stdout + result.stderr
    assert state.get("promotion_attempted") is True
    assert "resume" not in _events(calls)
    output = (result.stdout + result.stderr).lower()
    assert "rollback" in output, output


def test_optional_calendar_writer_is_quiesced_even_after_profile_opt_out(tmp_path):
    result, calls, state, backups = _run(tmp_path, running=("backend", "worker", "calendar-worker"))
    assert result.returncode == 0, result.stdout + result.stderr
    stopped = next(call for call in calls if call["event"] == "stop")
    assert stopped["services"] == ["backend", "worker", "calendar-worker"]
    assert "--profile" in stopped["args"] and "calendar" in stopped["args"]
    for call in calls:
        if call["event"] in ("backup_db", "backup_docs", "promote"):
            assert call["running"]["calendar-worker"] is False, call
            assert not any(call["running"].values()), call
    # Inspecting/stopping an old optional service does not opt the next build
    # or start into a new consumer after the operator removed its profile.
    for event in ("build", "promote"):
        assert "--profile" not in next(call for call in calls if call["event"] == event)["args"]
    assert state["running"] == {"backend": True, "worker": True, "calendar-worker": False}
    image_text = next(backups.glob("pre-deploy-images-*.txt")).read_text()
    assert "calendar-worker sha256:" + "5" * 64 in image_text


def test_preparation_failure_resumes_only_previously_running_optional_worker(tmp_path):
    result, calls, state, _ = _run(tmp_path, running=("calendar-worker",), db_backup=44)
    assert result.returncode == 44, result.stdout + result.stderr
    resumes = [call for call in calls if call["event"] == "resume"]
    assert len(resumes) == 1 and resumes[0]["services"] == ["calendar-worker"]
    assert "--profile" in resumes[0]["args"] and "calendar" in resumes[0]["args"]
    assert state["running"] == {"backend": False, "worker": False, "calendar-worker": True}
    assert "promote" not in _events(calls)


def test_preparation_recovery_does_not_start_an_optional_worker_that_was_stopped(tmp_path):
    result, calls, state, _ = _run(
        tmp_path,
        running=("backend",),
        calendar_worker=True,
        calendar_profile=True,
        db_backup=44,
    )
    assert result.returncode == 44, result.stdout + result.stderr
    resumes = [call["services"] for call in calls if call["event"] == "resume"]
    assert resumes == [["backend"]]
    assert state["running"] == {"backend": True, "worker": False, "calendar-worker": False}


def test_optional_calendar_writer_left_running_refuses_backups_and_migrations(tmp_path):
    result, calls, _, _ = _run(
        tmp_path,
        running=("calendar-worker",),
        still_running=True,
        still_running_service="calendar-worker",
    )
    assert result.returncode != 0
    assert not set(_events(calls)) & {"backup_db", "backup_docs", "promote"}
    assert "resume" in _events(calls)


@pytest.mark.parametrize("status", ["running", "restarting", "paused"])
def test_optional_one_off_calendar_writer_refuses_migrations(tmp_path, status):
    result, calls, _, _ = _run(
        tmp_path,
        calendar_worker=True,
        one_off=status,
        one_off_service="calendar-worker",
    )
    assert result.returncode != 0
    assert not set(_events(calls)) & {"backup_db", "backup_docs", "promote"}
    queries = [call for call in calls if call["event"] == "writers_query" and "-q" in call["args"]]
    assert queries and "calendar-worker" in queries[-1]["args"]
    assert "resume" in _events(calls)


def test_startup_failure_never_revives_the_old_optional_calendar_consumer(tmp_path):
    result, calls, state, _ = _run(
        tmp_path, running=("backend", "worker", "calendar-worker"), up=46
    )
    assert result.returncode == 46, result.stdout + result.stderr
    assert state.get("promotion_attempted") is True
    assert not any(state["running"].values())
    assert "resume" not in _events(calls)
    assert "rollback" in (result.stdout + result.stderr).lower()
