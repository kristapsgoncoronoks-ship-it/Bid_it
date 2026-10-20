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
import io, json, os, sys, tarfile, time
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
    while options and options[0] in ("-f", "--file", "--env-file", "-p"):
        options = options[2:]
    if not options:
        sys.exit(91)
    command, rest = options[0], options[1:]
    services = [item for item in rest if item in ("backend", "worker")]
    if command == "build":
        record("build")
        hold("build")
        fail("build")
    elif command == "images":
        record("old_images")
        selected = [item for item in rest if item in ("backend", "worker", "frontend", "db")]
        for service in selected:
            print("sha256:" + {"backend": "1", "worker": "2", "frontend": "3", "db": "4"}[service] * 64)
    elif command == "ps":
        record("writers_query" if "--status" in rest else "status")
        if "--status" in rest:
            if state.get("stop_attempted"):
                fail("verify")
            statuses = [rest[index + 1] for index, value in enumerate(rest)
                        if value == "--status"]
            live = [item for item in (services or ["backend", "worker"])
                    if state["running"][item] and "running" in statuses]
            if state.get("stop_attempted") and os.environ.get("BIDIT_TEST_STILL_RUNNING"):
                live = ["worker"] if "running" in statuses else []
            print("\n".join(item if "--services" in rest else item + "-old"
                            for item in live))
            if (state.get("stop_attempted") and "--all" in rest
                    and os.environ.get("BIDIT_TEST_ONE_OFF_STATUS") in statuses):
                print("worker-one-off-old")
    elif command == "stop":
        record("stop", services=services)
        state["stop_attempted"] = True
        for service in services:
            state["running"][service] = False
        save()
        fail("stop")
    elif command == "start":
        record("resume", services=services)
        fail("resume")
        for service in services:
            state["running"][service] = True
        save()
    elif command == "exec" and "pg_dump" in rest:
        record("backup_db")
        hold("backup_db")
        fail("db_backup")
        print("-- synthetic SQL\n-- PostgreSQL database dump complete\n--\n")
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
        state["running"] = {"backend": True, "worker": True}
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
    tmp_path, *, running=("backend", "worker"), still_running=False, one_off=None, **failures
):
    # The script must explicitly support temporary backups. Never redirect HOME
    # merely to make an unsafe historical implementation executable in a test.
    assert "BIDIT_DEPLOY_BACKUP_DIR" in SCRIPT.read_text(), (
        "Deployment script lacks the dedicated safe backup-directory contract"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("git", "docker", "df", "date", "sleep"):
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
                "running": {service: service in running for service in ("backend", "worker")},
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
        **{f"BIDIT_TEST_FAIL_{key.upper()}": str(value) for key, value in failures.items()},
    }
    if still_running:
        env["BIDIT_TEST_STILL_RUNNING"] = "1"
    if one_off:
        env["BIDIT_TEST_ONE_OFF_STATUS"] = one_off
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
