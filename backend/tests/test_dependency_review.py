"""Fail-closed evidence tooling and private, non-approving history inventory."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_dependency_delta_retains_transitive_changes_and_rejects_skips():
    spec = importlib.util.spec_from_file_location(
        "dependency_delta", ROOT / "scripts/dependency_delta.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = module.python_graph(
        {
            "dependencies": [
                {"name": "Transitive_Dep", "version": "1", "vulns": [{"id": "test-advisory"}]}
            ]
        }
    )
    new = module.python_graph(
        {"dependencies": [{"name": "transitive-dep", "version": "2", "vulns": []}]}
    )
    (change,) = module.delta(old, new)
    assert change["dependency"] == "transitive-dep"
    assert change["base"]["advisories"] == ["test-advisory"]
    assert change["head"]["version"] == "2"
    for report in (
        {},
        {"dependencies": []},
        {"dependencies": [{"name": "skipped", "skip_reason": "unsupported"}]},
    ):
        with pytest.raises(ValueError):
            module.python_graph(report)
