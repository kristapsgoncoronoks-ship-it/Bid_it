"""Required completion jobs must reject a skipped or unsuccessful dependency.

Run the actual workflow shell/Python bodies with synthetic GitHub ``needs``
objects. These are local execution contracts, not a GitHub scheduler emulator or
evidence that branch protection has been enabled.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SENTINEL = "synthetic-private-output::error::must-not-appear"

GATES = (
    (
        "ci.yml",
        "ci-required",
        (
            "pii-scan",
            "lint",
            "backend",
            "postgres",
            "frontend",
            "frontend-e2e",
            "docker-build",
        ),
    ),
    (
        "security-supply-chain.yml",
        "supply-chain-required",
        ("workflow-review", "secret-scan", "dependency-review", "security-controls"),
    ),
)
GATE_PARAMS = [pytest.param(*gate, id=gate[1]) for gate in GATES]
CHILD_PARAMS = [
    pytest.param(*gate, child, id=f"{gate[1]}-{child}") for gate in GATES for child in gate[2]
]


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _step(workflow: str, gate: str) -> dict:
    steps = _workflow(workflow)["jobs"][gate]["steps"]
    assert len(steps) == 1
    return steps[0]


def _success(required: tuple[str, ...]) -> dict:
    return {
        job: {"result": "success", "outputs": {"synthetic_private_value": SENTINEL}}
        for job in required
    }


def _run(step: dict, needs_json: str | None) -> subprocess.CompletedProcess:
    # Do not pass tokens, credentials or the test runner's other environment to
    # the subprocess. The workflow body only needs Python and its needs object.
    env = {"PATH": os.environ["PATH"]}
    if needs_json is not None:
        env["NEEDS_JSON"] = needs_json
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _assert_refused(result: subprocess.CompletedProcess) -> None:
    assert result.returncode != 0, result.stdout + result.stderr
    assert SENTINEL not in result.stdout + result.stderr


@pytest.mark.parametrize(("workflow", "gate", "required"), GATE_PARAMS)
def test_completion_gate_runs_on_pr_even_after_a_dependency_is_unsuccessful(
    workflow, gate, required
):
    document = _workflow(workflow)
    job = document["jobs"][gate]
    # always() prevents implicit success() from skipping the check after a failed
    # dependency. PR-only scope preserves push/dispatch's intentional skips.
    condition = re.sub(r"\s+", "", job["if"])
    condition = condition.removeprefix("${{").removesuffix("}}")
    assert condition == "always()&&github.event_name=='pull_request'"
    assert job["name"] == gate
    assert job["needs"] == list(required)
    assert job["permissions"] == {}
    assert job["runs-on"] == "ubuntu-latest"
    assert 0 < job["timeout-minutes"] <= 2
    assert not job.get("continue-on-error", False)
    step = _step(workflow, gate)
    assert "uses" not in step
    assert not step.get("continue-on-error", False)
    assert step["env"] == {"NEEDS_JSON": "${{ toJSON(needs) }}"}
    for child in required:
        assert child in document["jobs"]
        assert not document["jobs"][child].get("continue-on-error", False), (
            f"{workflow}:{child} can mask failure before completion reads its result"
        )


@pytest.mark.parametrize(("workflow", "gate", "required"), GATE_PARAMS)
def test_completion_accepts_all_successful_dependencies_without_printing_outputs(
    workflow, gate, required
):
    result = _run(_step(workflow, gate), json.dumps(_success(required)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert SENTINEL not in result.stdout + result.stderr


@pytest.mark.parametrize(("workflow", "gate", "required", "child"), CHILD_PARAMS)
@pytest.mark.parametrize(
    "outcome",
    ["failure", "cancelled", "skipped", "neutral", "SUCCESS", "", None, True, SENTINEL],
)
def test_each_dependency_requires_exact_success(workflow, gate, required, child, outcome):
    needs = _success(required)
    needs[child]["result"] = outcome
    _assert_refused(_run(_step(workflow, gate), json.dumps(needs)))


@pytest.mark.parametrize(("workflow", "gate", "required", "child"), CHILD_PARAMS)
def test_missing_dependency_cannot_look_like_all_success(workflow, gate, required, child):
    needs = _success(required)
    del needs[child]
    _assert_refused(_run(_step(workflow, gate), json.dumps(needs)))


@pytest.mark.parametrize(("workflow", "gate", "required"), GATE_PARAMS)
@pytest.mark.parametrize("dependency", [None, [], True, 1, SENTINEL, {}, {"outputs": {}}])
def test_malformed_dependency_never_counts_as_success(workflow, gate, required, dependency):
    needs = _success(required)
    needs[required[-1]] = dependency
    _assert_refused(_run(_step(workflow, gate), json.dumps(needs)))


@pytest.mark.parametrize(("workflow", "gate", "required"), GATE_PARAMS)
@pytest.mark.parametrize("inventory", [None, [], True, 1, SENTINEL, {}])
def test_wrong_or_empty_inventory_is_refused(workflow, gate, required, inventory):
    _assert_refused(_run(_step(workflow, gate), json.dumps(inventory)))


@pytest.mark.parametrize(("workflow", "gate", "required"), GATE_PARAMS)
def test_unexpected_inventory_entry_is_refused_without_echoing_it(workflow, gate, required):
    needs = _success(required)
    needs[SENTINEL] = {"result": "success"}
    _assert_refused(_run(_step(workflow, gate), json.dumps(needs)))


@pytest.mark.parametrize(("workflow", "gate", "required"), GATE_PARAMS)
@pytest.mark.parametrize("needs_json", [None, "", "{", "[] trailing-data"])
def test_missing_or_invalid_json_fails_closed(workflow, gate, required, needs_json):
    _assert_refused(_run(_step(workflow, gate), needs_json))


def test_completion_checks_preserve_original_required_contexts_and_production_boundaries():
    ci = _workflow("ci.yml")
    supply = _workflow("security-supply-chain.yml")
    assert ci["permissions"] == supply["permissions"] == {"contents": "read"}
    names = {
        job.get("name", key) for document in (ci, supply) for key, job in document["jobs"].items()
    }
    assert {
        "pii-scan",
        "lint",
        "backend",
        "postgres",
        "frontend",
        "frontend-e2e",
        "docker-build",
        "Workflow security",
        "Generic credential scan",
        "Dependency review",
        "Security control register",
    } <= names
    # The PR-only completion gate must not become a dependency of main's deploy
    # or the manual baseline writer, where it is intentionally skipped.
    assert ci["jobs"]["deploy"]["needs"] == list(GATES[0][2])
    deploy_condition = ci["jobs"]["deploy"]["if"]
    assert "github.event_name == 'push'" in deploy_condition
    assert "github.ref == 'refs/heads/main'" in deploy_condition
    assert "vars.DEPLOY_ENABLED == 'true'" in deploy_condition
    baseline = ci["jobs"]["vr-baselines"]
    assert "workflow_dispatch" in baseline["if"]
    assert "github.ref_name != github.event.repository.default_branch" in baseline["if"]
    assert baseline["permissions"] == {"contents": "write"}
    assert "ci-required" not in baseline.get("needs", [])
