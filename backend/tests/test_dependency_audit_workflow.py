"""Execute dependency-review shell steps with failing audit/provider stubs.

This runner models the workflow's small status-condition vocabulary, not a
general Actions runner. Real Bash executes the committed commands and preserves
their exit statuses; no package registry or GitHub credentials are involved.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/security-supply-chain.yml"


def _condition(expression, outcomes, root, *, cancelled=False):
    if expression is None:
        return not cancelled and "failure" not in outcomes.values()
    expression = str(expression).removeprefix("${{").removesuffix("}}").strip()
    expression = expression.replace("!cancelled()", repr(not cancelled))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(
        r"steps\.([a-z_]+)\.outcome",
        lambda match: repr(outcomes.get(match[1], "skipped")),
        expression,
    )
    expression = re.sub(
        r"hashFiles\('([^']+)'\)",
        lambda match: repr("present" if (root / match[1]).is_file() else ""),
        expression,
    )
    tree = ast.parse(expression, mode="eval")
    assert all(
        isinstance(
            node,
            (
                ast.Expression,
                ast.BoolOp,
                ast.And,
                ast.Or,
                ast.Compare,
                ast.Eq,
                ast.NotEq,
                ast.Constant,
            ),
        )
        for node in ast.walk(tree)
    ), expression
    return eval(compile(tree, "workflow-condition", "eval"), {"__builtins__": {}}, {})


def _execute_job(tmp_path, *, cancelled=False, **failures):
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["dependency-review"]
    tools = tmp_path / "tools"
    tools.mkdir()
    for folder in ("backend", "frontend", "scripts"):
        (tmp_path / folder).mkdir()
    # Keep Python available even if the ambient test runner altered PATH.
    for executable in ("python", "python3"):
        (tools / executable).symlink_to(sys.executable)
    stub = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ['CALLS'], 'a') as log:
    log.write(name + ' ' + ' '.join(sys.argv[1:]) + '\\n')
if name == 'pip-audit':
    report = os.environ.get('FAIL_PYTHON_REPORT', '')
    if report != 'missing':
        Path('../dependency-head-python.json').write_text('invalid-json' if report else json.dumps({{
            'dependencies': [{{'name': 'fixture', 'version': '1', 'vulns':
                [{{'id': 'TEST-ADVISORY', 'fix_versions': ['2']}}]
                if os.environ.get('FAIL_PYTHON_AUDIT') else []}}]
        }}))
    sys.exit(int(os.environ.get('FAIL_PYTHON_AUDIT', '0')))
if name == 'pip':
    sys.exit(int(os.environ.get('FAIL_PYTHON_INSTALL', '0')))
if sys.argv[1] == 'ci':
    sys.exit(int(os.environ.get('FAIL_NPM_INSTALL', '0')))
print('invalid-json' if os.environ.get('FAIL_NPM_REPORT') else json.dumps({{
    'auditReportVersion': 2, 'vulnerabilities':
    {{'fixture': {{'severity': 'high'}}}} if os.environ.get('FAIL_NPM_AUDIT') else {{}},
    'metadata': {{}}
}}))
sys.exit(int(os.environ.get('FAIL_NPM_AUDIT', '0')))
"""
    for name in ("pip", "pip-audit", "npm"):
        path = tools / name
        path.write_text(stub)
        path.chmod(0o755)
    (tmp_path / "scripts/dependency_delta.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "status = int(os.environ.get('FAIL_DELTA', '0'))\n"
        "if not status:\n"
        "    Path('dependency-delta.json').write_text(json.dumps({'fixture': True}))\n"
        "sys.exit(status)\n"
    )
    calls_path = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
        "CALLS": str(calls_path),
        **{f"FAIL_{key.upper()}": str(value) for key, value in failures.items()},
    }
    outcomes, upload = {}, None
    for index, step in enumerate(job["steps"]):
        step_id = step.get("id", f"step_{index}")
        if not _condition(step.get("if"), outcomes, tmp_path, cancelled=cancelled):
            outcomes[step_id] = "skipped"
            continue
        if "uses" in step:
            action = step["uses"].split("@", 1)[0]
            failure = failures.get(
                {
                    "actions/setup-python": "python_runtime",
                    "actions/setup-node": "node_runtime",
                }.get(action, ""),
                0,
            )
            if action == "actions/upload-artifact":
                upload = step
            outcomes[step_id] = "failure" if failure else "success"
            continue
        step_env = dict(env)
        for key, value in (step.get("env") or {}).items():
            step_env[key] = re.sub(
                r"\$\{\{ steps\.([a-z_]+)\.outcome \}\}",
                lambda match: outcomes.get(match[1], "skipped"),
                str(value),
            )
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=tmp_path / step.get("working-directory", "."),
            env=step_env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        outcomes[step_id] = "success" if result.returncode == 0 else "failure"
    calls = calls_path.read_text() if calls_path.exists() else ""
    return outcomes, upload, calls


@pytest.mark.parametrize(
    ("failure", "missing"),
    [
        ({}, []),
        ({"python_audit": 1}, []),
        ({"python_install": 3}, ["dependency-head-python.json", "dependency-delta.json"]),
        ({"python_runtime": 1}, ["dependency-head-python.json", "dependency-delta.json"]),
        ({"delta": 1}, ["dependency-delta.json"]),
        ({"npm_audit": 1}, []),
        ({"npm_install": 2}, ["dependency-head-npm.json"]),
        ({"node_runtime": 1}, ["dependency-head-npm.json"]),
        ({"python_report": "missing"}, ["dependency-head-python.json", "dependency-delta.json"]),
        ({"python_report": "invalid"}, ["dependency-head-python.json"]),
        ({"npm_report": "invalid"}, ["dependency-head-npm.json"]),
    ],
)
def test_dependency_audits_retain_failures_and_available_evidence(tmp_path, failure, missing):
    outcomes, upload, calls = _execute_job(tmp_path, **failure)
    assert upload is not None, "failure must not suppress the diagnostic artifact"
    assert upload["with"].get("if-no-files-found") == "error"
    paths = upload["with"]["path"].split()
    assert "dependency-check-status.json" in paths
    assert "dependency-head-npm.json" in paths
    status = json.loads((tmp_path / "dependency-check-status.json").read_text())
    assert status["status"] == ("blocked" if failure else "passed")
    assert status["missing_or_invalid_reports"] == sorted(missing)
    assert ("failure" in outcomes.values()) == bool(failure)
    if not {"npm_install", "node_runtime"} & failure.keys():
        assert "npm audit --include=dev --audit-level=moderate --json" in calls
    for path in paths:
        if path in missing:
            if (tmp_path / path).exists():
                with pytest.raises(json.JSONDecodeError):
                    json.loads((tmp_path / path).read_text())
        else:
            assert (tmp_path / path).exists()
    assert not any(
        step.get("continue-on-error")
        for step in yaml.safe_load(WORKFLOW.read_text())["jobs"]["dependency-review"]["steps"]
    )


def test_cancelled_dependency_job_starts_no_additional_work_or_upload(tmp_path):
    outcomes, upload, calls = _execute_job(tmp_path, cancelled=True)
    assert set(outcomes.values()) == {"skipped"}
    assert upload is None
    assert calls == ""
