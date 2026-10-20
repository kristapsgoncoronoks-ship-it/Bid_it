"""Independent mutations prove both browser pins and the actual lock graph fail closed."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_playwright_pair.py"
VERSION = "1.64.0"
IMAGE = f"mcr.microsoft.com/playwright:v{VERSION}-jammy"


@pytest.fixture
def inputs(tmp_path):
    lock = {
        "packages": {
            "node_modules/@playwright/test": {
                "version": VERSION,
                "dependencies": {"playwright": VERSION},
            },
            "node_modules/playwright": {
                "version": VERSION,
                "dependencies": {"playwright-core": VERSION},
            },
            "node_modules/playwright-core": {"version": VERSION},
        }
    }
    workflow = {
        "jobs": {name: {"container": {"image": IMAGE}} for name in ("frontend-e2e", "vr-baselines")}
    }
    (tmp_path / "frontend").mkdir()
    (tmp_path / ".github/workflows").mkdir(parents=True)
    return tmp_path, workflow, lock


def run(inputs, *, raw_workflow=None, raw_lock=None):
    root, workflow, lock = inputs
    (root / ".github/workflows/ci.yml").write_text(
        yaml.safe_dump(workflow) if raw_workflow is None else raw_workflow
    )
    (root / "frontend/package-lock.json").write_text(
        json.dumps(lock) if raw_lock is None else raw_lock
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        text=True,
        capture_output=True,
        cwd=root.parent,
        timeout=10,
        check=False,
    )


def rejected(result, fragment):
    assert result.returncode == 1, result.stdout + result.stderr
    assert fragment in result.stderr
    assert "Traceback" not in result.stderr


def test_actual_repository_pair_is_checked_from_another_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "both browser job container references" in result.stdout


@pytest.mark.parametrize("job", ["frontend-e2e", "vr-baselines"])
def test_each_own_pin_mismatch_is_rejected_independently(inputs, job):
    inputs[1]["jobs"][job]["container"]["image"] = IMAGE.replace(VERSION, "1.63.0")
    rejected(run(inputs), f"{job}: container version does not match")


def test_comments_and_unrelated_first_image_cannot_mask_second_pin(inputs):
    workflow = copy.deepcopy(inputs[1])
    workflow["jobs"] = {"unrelated": {"container": {"image": IMAGE}}, **workflow["jobs"]}
    workflow["jobs"]["vr-baselines"]["container"]["image"] = IMAGE.replace(VERSION, "1.63.0")
    raw = f"# {IMAGE}\n" + yaml.safe_dump(workflow, sort_keys=False)
    rejected(run(inputs, raw_workflow=raw), "vr-baselines: container version does not match")


@pytest.mark.parametrize("job", ["frontend-e2e", "vr-baselines"])
def test_missing_job_cannot_clear_pair(inputs, job):
    del inputs[1]["jobs"][job]
    rejected(run(inputs), f"{job}: an exact official")


@pytest.mark.parametrize(
    "container",
    [
        None,
        {},
        {"image": 164},
        "registry.invalid/playwright:v1.64.0-jammy",
        "mcr.microsoft.com/playwright:latest",
    ],
)
def test_missing_mutable_and_unrecognized_container_are_refused(inputs, container):
    inputs[1]["jobs"]["vr-baselines"]["container"] = container
    rejected(run(inputs), "vr-baselines: an exact official")


def test_scalar_container_and_versioned_digest_reference_are_parsed(inputs):
    inputs[1]["jobs"]["frontend-e2e"]["container"] = IMAGE
    inputs[1]["jobs"]["vr-baselines"]["container"]["image"] += "@sha256:" + "a" * 64
    assert run(inputs).returncode == 0


@pytest.mark.parametrize("package", ["@playwright/test", "playwright", "playwright-core"])
def test_each_resolved_package_version_must_exist(inputs, package):
    del inputs[2]["packages"][f"node_modules/{package}"]["version"]
    rejected(run(inputs), f"{package} needs an exact resolved version")


@pytest.mark.parametrize("package", ["playwright", "playwright-core"])
def test_divergent_resolved_package_versions_are_refused(inputs, package):
    inputs[2]["packages"][f"node_modules/{package}"]["version"] = "1.63.0"
    rejected(run(inputs), "Playwright package versions disagree")


@pytest.mark.parametrize(
    "parent,child", [("@playwright/test", "playwright"), ("playwright", "playwright-core")]
)
def test_dependency_edge_cannot_point_at_another_browser_build(inputs, parent, child):
    inputs[2]["packages"][f"node_modules/{parent}"]["dependencies"][child] = "1.63.0"
    rejected(run(inputs), "Playwright dependency edges disagree")


def test_nested_divergent_browser_package_is_not_hidden_by_matching_root(inputs):
    inputs[2]["packages"]["node_modules/@playwright/test/node_modules/playwright"] = {
        "version": "1.63.0"
    }
    rejected(run(inputs), "nested Playwright version disagrees")


@pytest.mark.parametrize(
    "raw",
    [
        "jobs: [",
        "jobs: []",
        "[]",
        "jobs:\n  frontend-e2e: {}\n  frontend-e2e: {}",
        "!!python/object/apply:os.system ['false']",
    ],
)
def test_malformed_ambiguous_or_unsafe_yaml_fails_with_sanitized_diagnostic(inputs, raw):
    result = run(inputs, raw_workflow=raw)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert raw not in result.stderr


@pytest.mark.parametrize("raw", ["{", "[]", "{}"])
def test_malformed_or_incomplete_lock_is_refused(inputs, raw):
    result = run(inputs, raw_lock=raw)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_missing_input_does_not_report_pass(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path)], capture_output=True, text=True
    )
    rejected(result, "frontend/package-lock.json: missing or malformed")


def test_early_backend_feedback_invokes_real_checker_and_mutation_tests():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    runs = [step.get("run", "") for step in workflow["jobs"]["backend"]["steps"]]
    checker = runs.index("python ../scripts/check_playwright_pair.py")
    assert (
        runs.index("pip install -r requirements.txt") < checker < runs.index("python -m pytest -q")
    )
    assert any(
        "tests/test_supply_chain_gates.py tests/test_playwright_pair_gate.py" in run
        for run in runs[: checker + 2]
    )
