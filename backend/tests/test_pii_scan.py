"""WO-6 — Fleet Fuel PII quarantine: scanner + synthetic-factory tests.

These tests drive ``scripts/pii_scan.py`` as a subprocess (exactly how CI
runs it) against throw-away roots, and prove the synthetic fixture factories
produce values that are realistic in shape but verifiably fictional.

No VAT-shaped or IBAN-shaped literal may appear in this file — every
structural token is constructed at runtime, otherwise the tree scan would
(correctly) flag this very module.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from app.core.bank_id import is_valid_iban
from tests.factories.transport import (
    VAT_DIGITS,
    synthetic_company_name,
    synthetic_fuel_line,
    synthetic_iban,
    synthetic_invoice_number,
    synthetic_vat_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pii_scan.py"
DENYLIST = REPO_ROOT / "scripts" / "pii_denylist.json"
ALLOWLIST = REPO_ROOT / "scripts" / "pii_allowlist.json"


def _load_scanner():
    """Import scripts/pii_scan.py as a module (it is not on sys.path)."""
    spec = importlib.util.spec_from_file_location("pii_scan", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args: list[str], *, salt: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PII_SCAN_SALT"}
    if salt is not None:
        env["PII_SCAN_SALT"] = salt
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def _write_empty_lists(tmp_path: Path) -> tuple[Path, Path]:
    deny = tmp_path / "deny.json"
    allow = tmp_path / "allow.json"
    deny.write_text(json.dumps({"salt_env": "PII_SCAN_SALT", "entries": []}))
    allow.write_text(json.dumps({"entries": []}))
    return deny, allow


def _scan_dir(root: Path, deny: Path, allow: Path, *, salt: str | None = None):
    return _run(
        [
            "--tree",
            "--root",
            str(root),
            "--denylist",
            str(deny),
            "--allowlist",
            str(allow),
        ],
        salt=salt,
    )


def test_scan_is_clean_on_the_current_tree():
    """The committed repository passes its own quarantine gate (WO-6
    acceptance: `python scripts/pii_scan.py --tree` exits 0 today)."""
    res = _run(["--tree", "--root", str(REPO_ROOT)])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "clean" in res.stdout


def test_scan_detects_a_seeded_violation(tmp_path: Path):
    """A deny-listed literal in a scanned file fails the scan, reporting the
    LABEL — never the value (the scan must not republish what it protects)."""
    salt = "test-salt-wo6"
    token = "Quarantine Sentinel Haulage OÜ"  # synthetic, hashed below
    norm = "".join(token.lower().split())
    deny = tmp_path / "deny.json"
    deny.write_text(
        json.dumps(
            {
                "salt_env": "PII_SCAN_SALT",
                "entries": [
                    {
                        "label": "seed-company-1",
                        "len": len(norm),
                        "sha256": hashlib.sha256((salt + norm).encode()).hexdigest(),
                        "kind": "company_name",
                    }
                ],
            }
        )
    )
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps({"entries": []}))
    root = tmp_path / "root"
    root.mkdir()
    (root / "leak.md").write_text(f"the client is {token}, drafted 2026")

    res = _scan_dir(root, deny, allow, salt=salt)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "seed-company-1" in res.stderr  # the label IS reported
    assert token not in res.stdout + res.stderr  # the value is NOT
    assert norm not in (res.stdout + res.stderr).lower().replace(" ", "")


def test_scan_detects_a_structural_vat_id(tmp_path: Path):
    """A VAT-shaped literal needs no deny-list entry to be caught."""
    deny, allow = _write_empty_lists(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    vat = "LT" + "9" * 12  # constructed — never a literal in this file
    (root / "notes.txt").write_text(f"supplier vat {vat} registered")

    res = _scan_dir(root, deny, allow)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "structural-vat-id" in res.stderr
    assert vat not in res.stderr  # only a masked form is printed


def test_scan_detects_a_valid_iban_literal(tmp_path: Path):
    """An IBAN that passes MOD-97 is a finding, reported masked."""
    deny, allow = _write_empty_lists(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    iban = synthetic_iban("EE", seed=7)
    (root / "pay.txt").write_text(f"send to {iban} today")

    res = _scan_dir(root, deny, allow)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "structural-iban" in res.stderr
    assert iban not in res.stderr


def test_scan_ignores_an_invalid_iban_lookalike(tmp_path: Path):
    """Breaking one digit breaks MOD-97, so lookalikes do not fire — the
    check that keeps the structural IBAN pattern's false-positive rate low."""
    deny, allow = _write_empty_lists(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    iban = synthetic_iban("EE", seed=7)
    last = iban[-1]
    broken = iban[:-1] + ("0" if last != "0" else "1")
    assert not is_valid_iban(broken)
    (root / "pay.txt").write_text(f"ref {broken} looks like an account")

    res = _scan_dir(root, deny, allow)
    assert res.returncode == 0, res.stdout + res.stderr


def test_scan_ignores_masked_placeholder_tokens(tmp_path: Path):
    """The docs/plan specs deliberately use masked placeholders — guillemet
    names and hash-masked ids. Those forms must never be findings."""
    deny, allow = _write_empty_lists(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    (root / "spec.md").write_text(
        "customer_master.CUSTOMERS carries «Client-EE» AS / EE1########0\n"
        "and UAB «Client-LT-1» / LT1##########7 as module constants.\n"
    )
    res = _scan_dir(root, deny, allow)
    assert res.returncode == 0, res.stdout + res.stderr


def test_scan_fails_closed_when_the_denylist_is_missing(tmp_path: Path):
    """No deny-list must mean a red build, never a silent pass."""
    _, allow = _write_empty_lists(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    res = _scan_dir(root, tmp_path / "does-not-exist.json", allow)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "failing closed" in res.stderr


def test_scan_fails_closed_when_entries_exist_but_the_salt_is_unset(
    tmp_path: Path,
):
    """Hashed entries are unverifiable without the salt — unverifiable must
    not mean passed (WO-6 invariant: CI fails closed)."""
    deny = tmp_path / "deny.json"
    deny.write_text(
        json.dumps(
            {
                "salt_env": "PII_SCAN_SALT",
                "entries": [{"label": "x", "len": 5, "sha256": "0" * 64, "kind": "vat_id"}],
            }
        )
    )
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps({"entries": []}))
    root = tmp_path / "root"
    root.mkdir()
    res = _scan_dir(root, deny, allow, salt=None)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "PII_SCAN_SALT" in res.stderr


def test_synthetic_ibans_pass_mod97():
    """The factory's IBANs are accepted by app.core.bank_id.is_valid_iban —
    shape-realistic fixtures over a documented test-only bank code."""
    for country in ("EE", "DE", "LT", "FI", "LV", "BE"):
        for seed in (1, 2, 3):
            assert is_valid_iban(synthetic_iban(country, seed=seed))


def test_scanner_iban_check_agrees_with_bank_id():
    """The scanner re-implements MOD-97 to stay dependency-free; this pins
    it to app.core.bank_id so the two can never drift silently."""
    scanner = _load_scanner()
    samples: list[str] = []
    for country in ("EE", "DE", "LT", "FI", "LV", "BE"):
        good = synthetic_iban(country, seed=11)
        samples.append(good)
        samples.append(good[:-1] + ("0" if good[-1] != "0" else "1"))  # bad mod97
        samples.append(good + "9")  # wrong length
    samples.append("ZZ" + "12" + "3" * 12)  # unknown country — both reject
    for token in samples:
        assert scanner.is_valid_iban(token) == is_valid_iban(token), token


def test_synthetic_vat_ids_are_not_on_the_denylist():
    """Generated VAT ids must never collide with a quarantined real one.

    With the salt available the comparison is by salted hash; without it the
    deny-list must be empty (fail closed: populating the list without giving
    the test environment the salt fails this test rather than skipping)."""
    data = json.loads(DENYLIST.read_text())
    entries = data["entries"]
    salt = os.environ.get(data.get("salt_env", "PII_SCAN_SALT"), "")
    if not salt:
        assert entries == [], (
            "deny-list has hashed entries but the salt is not available to "
            "the test run — export PII_SCAN_SALT so collisions can be checked"
        )
        return
    for country in VAT_DIGITS:
        for seed in range(5):
            vat = synthetic_vat_id(country, seed=seed).lower()
            digest = hashlib.sha256((salt + vat).encode()).hexdigest()
            assert all(e["sha256"] != digest for e in entries), country


def test_factories_are_deterministic_and_plausible():
    """Same seed → same value; shapes match the structural expectations."""
    assert synthetic_vat_id("EE", seed=42) == synthetic_vat_id("EE", seed=42)
    for country, digits in VAT_DIGITS.items():
        vat = synthetic_vat_id(country, seed=1)
        assert vat.startswith(country) and len(vat) == len(country) + digits
        assert vat[len(country) :].isdigit()
    assert synthetic_iban("LT", seed=5) == synthetic_iban("LT", seed=5)
    name = synthetic_company_name(seed=3)
    assert name and "Client" not in name
    number = synthetic_invoice_number("fuelcard-a", seed=9)
    assert number.startswith("FCA-")
    line = synthetic_fuel_line(seed=4)
    assert line["product_code"] == "1"  # fuel — R11: never "9"
    assert 200 <= float(str(line["litres"])) <= 900
    assert str(line["net_eur"]).split(".")[1].__len__() == 2  # money is 2dp


def test_the_committed_denylist_contains_no_plaintext_identifier():
    """WO-6 acceptance: the deny-list holds hashes only. Every entry is a
    64-hex sha256 with a label/len/kind — no value field, no raw token."""
    data = json.loads(DENYLIST.read_text())
    for e in data["entries"]:
        assert set(e) == {"label", "len", "sha256", "kind"}
        assert len(e["sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in e["sha256"])
        assert "value" not in e
    allow = json.loads(ALLOWLIST.read_text())
    for e in allow["entries"]:
        assert e.get("justification") and e.get("verified_by")
