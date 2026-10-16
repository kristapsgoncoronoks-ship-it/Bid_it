"""Fail-closed evidence tooling and private, non-approving history inventory."""

import json
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_private_history_inventory_never_approves_or_exposes_identifiers(tmp_path):
    from tests.factories.transport import synthetic_vat_id

    root = tmp_path / "checkout"
    root.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init")
    tests = root / "tests"
    tests.mkdir()
    token = synthetic_vat_id(seed=987654)
    sensitive_path = tests / f"{token}.txt"
    sensitive_path.write_text(token)
    git("add", ".")
    git(
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "synthetic fixture",
    )
    sensitive_path.unlink()
    git("add", ".")
    git(
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "remove fixture",
    )
    output = tmp_path / "private.json"
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/private_pii_triage.py"),
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    serialized = output.read_text()
    assert token not in serialized + run.stdout + run.stderr
    result = json.loads(serialized)
    assert result["affected_blobs"] == 1
    assert result["finding_rows"] == 1
    assert result["blobs"][0]["classification"] == "unresolved"
    assert result["blobs"][0]["refs"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not sensitive_path.exists()
    forbidden = root / "triage.json"
    refusal = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/private_pii_triage.py"),
            "--root",
            str(root),
            "--output",
            str(forbidden),
        ],
        capture_output=True,
        text=True,
    )
    assert refusal.returncode != 0 and not forbidden.exists()
    assert subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"]) == b""
