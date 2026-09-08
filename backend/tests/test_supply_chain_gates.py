"""Supply-chain and security-governance gates (reference integration R3, 2026-09-07;
Personal-Security-Checklist SEC-SC-001 / SEC-CTRL-001 / ENG-GOV-001, reinforced by
the Paperless, Stirling and Flask cycles).

These run in the ordinary backend job so the repository's own suite fails before a
workflow is ever executed:

  1. every external GitHub Action is pinned to a full commit SHA (a tag can be
     moved by whoever controls — or compromises — the upstream repository);
  2. every workflow declares its token permissions at the top level, read-only
     unless the workflow's job needs more (release.yml pushes images);
  3. every checkout uses `persist-credentials: false` and no job widens the
     read-only token — with ONE named exception, `ci.yml`'s `vr-baselines`, which
     publishes refreshed visual baselines to the dispatching branch (never main)
     and therefore keeps the token and `contents: write` for itself;
  4. the security-control register validates and its generated view is in sync;
  5. the PR template still asks for evidence, executed tests and AI disclosure.

Each gate is proven by a seeded violation on a fixture, not only by the live tree.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pins = _load("check_github_action_pins")
gate = _load("security_control_gate")


# --------------------------------------------------------------------- 1. pins


def test_every_external_action_is_pinned_to_a_full_commit_sha():
    assert pins.unpinned() == []


def test_pin_checker_catches_a_mutable_tag(tmp_path):
    wf = tmp_path / "x.yml"
    wf.write_text(
        "jobs:\n  a:\n    steps:\n"
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n"
        "      - uses: actions/setup-python@v7\n"
        "      - uses: ./local-action\n"
    )
    found = pins.unpinned(tmp_path)
    assert len(found) == 1 and "actions/setup-python@v7" in found[0]


def test_every_pin_keeps_the_version_comment_dependabot_reads():
    """`owner/repo@<sha> # vN` — without the comment Dependabot cannot propose the
    next pinned bump and the pins rot."""
    bad = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            m = re.search(r"uses:\s*([^\s#]+@[0-9a-f]{40})(.*)$", line)
            if m and not re.search(r"#\s*v\d", m.group(2)):
                bad.append(f"{path.name}:{line_no}")
    assert bad == []


# ------------------------------------------------------- 2/3. token permissions


def _workflows() -> dict[str, dict]:
    return {p.name: yaml.safe_load(p.read_text()) for p in sorted(WORKFLOWS.glob("*.y*ml"))}


def test_every_workflow_declares_read_only_token_permissions_at_the_top():
    allowed_writes = {"release.yml": {"packages"}}  # pushes images to GHCR
    for name, wf in _workflows().items():
        assert "permissions" in wf, f"{name}: no top-level permissions block"
        perms = wf["permissions"]
        assert isinstance(perms, dict) and perms.get("contents") == "read", name
        writes = {k for k, v in perms.items() if v == "write"}
        assert writes <= allowed_writes.get(name, set()), (
            f"{name}: unexpected write scopes {writes}"
        )


# The one job allowed to write to the repository: it commits refreshed visual
# baselines to the branch it was dispatched from and refuses to run on main.
# Any other writer is a finding, not a second entry here.
WRITING_JOBS = {("ci.yml", "vr-baselines")}


def test_every_checkout_does_not_persist_the_token():
    for name, wf in _workflows().items():
        for job_name, job in wf["jobs"].items():
            if (name, job_name) in WRITING_JOBS:
                continue
            for step in job.get("steps") or []:
                uses = step.get("uses", "")
                if uses.startswith("actions/checkout@"):
                    with_ = step.get("with") or {}
                    assert with_.get("persist-credentials") is False, (
                        f"{name}:{job_name}: checkout persists the token"
                    )


def test_no_job_level_permission_widens_the_default():
    for name, wf in _workflows().items():
        for job_name, job in wf["jobs"].items():
            perms = job.get("permissions")
            if perms is None:
                continue
            writes = {k for k, v in perms.items() if v == "write"}
            if (name, job_name) in WRITING_JOBS:
                assert writes == {"contents"}, f"{name}:{job_name} needs exactly contents:write"
                continue
            assert not writes, f"{name}:{job_name} widens permissions to {writes}"


def test_the_writing_job_refuses_the_default_branch_and_keeps_its_token():
    """The exception is safe only because of its `if:` — a job that could rewrite
    the gate's own baselines on main would let anyone bless a regression."""
    job = _workflows()["ci.yml"]["jobs"]["vr-baselines"]
    # It must ASK for the write it needs; the workflow default is read-only, so a
    # job that pushes without this block fails at push time with no credential.
    assert job.get("permissions") == {"contents": "write"}
    cond = " ".join(str(job.get("if", "")).split())
    assert "github.ref_name != github.event.repository.default_branch" in cond
    assert "workflow_dispatch" in cond
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts and all(
        (s.get("with") or {}).get("persist-credentials") is True for s in checkouts
    )
    assert any("git push" in str(s.get("run", "")) for s in job["steps"])


# --------------------------------------------------------- 4. control register


def test_security_control_register_validates_and_generated_view_is_in_sync():
    data = gate.load_register()
    assert gate.validate(data) == []
    assert gate.render(data) == gate.GENERATED.read_text(), (
        "docs/security/SECURITY-CONTROLS.md is stale — run scripts/security_control_gate.py --render"
    )


def _register_fixture() -> dict:
    return {
        "priority_vocabulary": ["P0", "P1", "P2", "P3", "P4"],
        "status_vocabulary": ["verified", "open", "blocked", "deferred", "not_applicable"],
        "controls": [
            {
                "id": "SEC-TEN-001",
                "title": "t",
                "category": "c",
                "priority": "P0",
                "status": "verified",
                "owner": "o",
                "validation": "v",
                "evidence": ["backend/tests/test_rls.py"],
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda c: c.update(evidence=[]), "verified requires at least one evidence path"),
        (lambda c: c.update(evidence=["backend/tests/does_not_exist.py"]), "does not exist"),
        (lambda c: c.update(status="open"), "P0 cannot remain open"),
        (lambda c: c.update(status="blocked", priority="P1"), "blocked requires blocker"),
        (lambda c: c.update(status="not_applicable"), "not_applicable requires rationale"),
        (lambda c: c.update(id="Tenant isolation"), "invalid stable id"),
    ],
)
def test_register_gate_refuses_checkbox_security(mutate, needle):
    data = _register_fixture()
    mutate(data["controls"][0])
    errors = gate.validate(data, root=ROOT)
    assert any(needle in e for e in errors), errors


def test_register_gate_refuses_a_duplicate_id():
    data = _register_fixture()
    data["controls"].append(dict(data["controls"][0]))
    assert any("duplicate id" in e for e in gate.validate(data, root=ROOT))


def test_register_statuses_reflect_this_tree():
    """The register is a claim about THIS repository: the controls landed in
    reference batches R1–R3 read `verified`, the owner items stay open/blocked."""
    by_id = {c["id"]: c for c in json.loads(gate.REGISTER.read_text())["controls"]}
    assert by_id["SEC-SC-001"]["status"] == "verified"
    assert by_id["SEC-SSRF-001"]["status"] == "verified"
    assert by_id["SEC-JWT-001"]["status"] == "verified"
    assert by_id["SEC-GOV-001"]["status"] == "blocked"
    assert by_id["OPS-GOV-001"]["status"] == "open"
    assert "Use this section" in (ROOT / "SECURITY.md").read_text(), (
        "SECURITY.md was replaced while SEC-GOV-001 is still blocked on the owner"
    )


# ------------------------------------------------------------ 5. PR template


def test_pr_template_asks_for_evidence_executed_tests_and_ai_disclosure():
    text = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    for heading in (
        "## Objective",
        "## Risk / impact",
        "## Evidence",
        "## Tests actually executed",
        "## Failure / rollback",
        "## AI assistance disclosure",
        "## Owner decisions / unresolved items",
    ):
        assert heading in text, heading
    assert 'Do **not** write "all tests pass"' in text
