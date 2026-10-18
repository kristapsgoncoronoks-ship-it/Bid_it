"""Seeded image/evidence failures must block without hiding later targets."""

import importlib.util
import json
import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("image_audit", ROOT / "scripts/image_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

BUILT = {role: "bidit-ci-" + role + ":review" for role in audit.ROLES}


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    for app in ("backend", "frontend"):
        (root / app).mkdir(parents=True)
        (root / app / "Dockerfile").write_text(
            "FROM synthetic@sha256:" + "a" * 64 + " AS build\n"
            "FROM synthetic@sha256:" + "b" * 64 + " AS runtime\n"
        )
    (root / "docker-compose.yml").write_text(
        'services:\n  db:\n    image: "postgres:16@sha256:' + "c" * 64 + '"\n'
    )
    return root


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    path = tmp_path / "bin"
    path.mkdir()
    docker = path / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import hashlib,json,os,sys
from pathlib import Path
a=sys.argv[1:]; mode=os.environ.get('IMAGE_AUDIT_FAULT','')
if a[0]=='pull':
 sys.exit(1 if mode=='pull' else 0)
if a[:2]==['image','inspect']:
 ref=a[-1]; ident='sha256:'+hashlib.sha256(ref.encode()).hexdigest()
 m={'Id':ident,'Architecture':'amd64','Os':'linux','RepoDigests':[],
    'Config':{'Env':['SYNTHETIC=private-value-do-not-publish']}}
 if mode=='inspect' and ref=='bidit-ci-backend:review':m.pop('Id')
 print(json.dumps([m]));sys.exit(0)
base=next(x[:-9] for x in a if x.endswith(':/reports'))
filename=Path(a[a.index('--output')+1]).name
p=Path(base)/filename
ident=a[-1]; fmt=a[a.index('--format')+1]
first=filename.startswith('backend.')
if mode=='missing' and first:sys.exit(0)
if mode=='malformed' and first:p.write_text('not JSON');sys.exit(0)
if fmt=='cyclonedx':
 bom_id='sha256:'+'0'*64 if mode=='sbom_identity' and first else ident
 p.write_text(json.dumps({'bomFormat':'CycloneDX','components':[{'type':'library','name':'synthetic'}],
  'metadata':{'component':{'type':'container','name':bom_id,'properties':[{'name':'aquasecurity:trivy:ImageID','value':bom_id},{'name':'aquasecurity:trivy:Labels:synthetic','value':'private-value-do-not-publish'}]}}}))
 sys.exit(2 if mode=='sbom' and first else 0)
rows=[]
if mode in ('vulnerable','false_zero') and first:
 rows=[{'VulnerabilityID':'SYNTHETIC-CVE','PkgName':'synthetic','InstalledVersion':'1','Severity':'CRITICAL'}]
if mode=='identity' and first:ident='sha256:'+'0'*64
data={'Metadata':{'ImageID':ident,'OS':{'Family':'synthetic'},'ImageConfig':{'config':{'Env':['private-value-do-not-publish']}}},
      'Results':[{'Class':'os-pkgs','Vulnerabilities':rows}]}
if mode=='unsupported' and first:data['Metadata']['OS']['EOSL']=True
if mode=='bad_results' and first:data['Results']=[None]
p.write_text(json.dumps(data))
sys.exit(2 if mode=='scanner' and first else 1 if mode=='false_nonzero' and first else 0 if mode=='false_zero' else 1 if rows else 0)
"""
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(path) + ":" + os.environ["PATH"])


def test_current_inventory_covers_every_deployment_reference_and_build_output():
    plan = audit.inventory(ROOT, BUILT)
    assert plan["errors"] == []
    assert len(plan["targets"]) == 8
    assert len(plan["base_inputs"]) == 4
    assert {t["reference"] for t in plan["targets"] if t["role"] == "service"} == {
        "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea",
        "minio/minio:latest",
        "minio/mc:latest",
        "edoburu/pgbouncer:1.23.1",
    }
    assert sum(len(t["sources"]) for t in plan["targets"]) == 15


def test_ci_builds_and_loads_every_audited_stage():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["docker-build"]["steps"]
    built = {
        s["with"]["tags"]: s["with"] for s in steps if "build-push-action@" in s.get("uses", "")
    }
    for role, reference in BUILT.items():
        assert reference in built, f"missing audited image {reference}"
        assert built[reference]["load"] is True and built[reference]["push"] is False
        if role.endswith("-build"):
            assert built[reference]["target"] == "build"


def test_additional_compose_yaml_and_nested_init_container_are_discovered(source):
    (source / "compose.audit.yaml").write_text(
        "services:\n  helper:\n    image: registry.example/helper@sha256:" + "d" * 64 + "\n"
    )
    directory = source / "deploy/k8s/optional"
    directory.mkdir(parents=True)
    (directory / "job.yaml").write_text(
        "spec:\n  template:\n    spec:\n      initContainers:\n"
        "        - name: init\n          image: registry.example/init@sha256:" + "e" * 64 + "\n"
    )
    plan = audit.inventory(source, BUILT)
    assert plan["errors"] == []
    assert len(plan["targets"]) == 7
    assert any(
        t["sources"][0]["file"] == "deploy/k8s/optional/job.yaml"
        for t in plan["targets"]
        if t["role"] == "service"
    )


def test_ci_collects_the_full_inventory_after_another_check_fails():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["docker-build"]["steps"]
    scans = [s for s in steps if "image_audit.py" in s.get("run", "")]
    assert len(scans) == 1
    assert any(x in scans[0].get("if", "") for x in ("always()", "!cancelled()"))
    assert not scans[0].get("continue-on-error", False)


def test_clean_requires_all_targets_and_fresh_identity_evidence(source, fake_docker, tmp_path):
    out = tmp_path / "reports"
    assert audit.run(source, out, BUILT) == 0
    first = json.loads((out / "summary.json").read_text())["run"]
    assert audit.run(source, out, BUILT) == 0
    final = json.loads((out / "summary.json").read_text())
    assert final["passed"] and final["run"] != first
    assert len(final["results"]) == 5
    assert all(r["status"] == "clean" for r in final["results"])
    assert all("private-value-do-not-publish" not in p.read_text() for p in out.rglob("*.*"))


@pytest.mark.parametrize(
    "mode",
    [
        "vulnerable",
        "false_zero",
        "scanner",
        "sbom",
        "sbom_identity",
        "missing",
        "malformed",
        "identity",
        "unsupported",
        "bad_results",
        "false_nonzero",
        "inspect",
        "pull",
    ],
)
def test_failure_never_becomes_clean_or_hides_later_images(
    source, fake_docker, tmp_path, monkeypatch, mode
):
    monkeypatch.setenv("IMAGE_AUDIT_FAULT", mode)
    out = tmp_path / "reports"
    assert audit.run(source, out, BUILT) == 1
    final = json.loads((out / "summary.json").read_text())
    assert not final["passed"] and len(final["results"]) == 5
    assert any(r["status"] == "clean" for r in final["results"])
    assert any(r["status"] != "clean" for r in final["results"])
    assert all("private-value-do-not-publish" not in p.read_text() for p in out.rglob("*.*"))


def test_mutable_or_unmapped_service_configuration_blocks(source, fake_docker, tmp_path):
    compose = source / "docker-compose.yml"
    compose.write_text("services:\n  db:\n    image: postgres:16\n")
    assert audit.run(source, tmp_path / "mutable", BUILT) == 1
    compose.write_text("services:\n  db:\n    image: ${UNREVIEWED_IMAGE}\n")
    assert audit.run(source, tmp_path / "template", BUILT) == 1


def test_invalid_inventory_cannot_retain_a_previous_passing_summary(source, fake_docker, tmp_path):
    out = tmp_path / "reports"
    assert audit.run(source, out, BUILT) == 0
    duplicate = dict.fromkeys(BUILT, "same:review")
    assert audit.run(source, out, duplicate) == 1
    assert json.loads((out / "summary.json").read_text())["passed"] is False


def test_timeout_is_a_tool_error_without_echoing_submitted_secrets(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise audit.subprocess.TimeoutExpired("synthetic-secret", 300)

    monkeypatch.setattr(audit.subprocess, "run", timeout)
    log = tmp_path / "timeout.log"
    assert audit.command(["synthetic"], log) == 2
    assert "synthetic-secret" not in log.read_text()


@pytest.mark.parametrize("mode", ["vulnerable", "pull", "malformed"])
def test_failed_gate_publishes_readable_summary_without_changing_exit(
    source, fake_docker, tmp_path, monkeypatch, mode
):
    out = tmp_path / "reports"
    github_summary = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(github_summary))
    monkeypatch.setenv("IMAGE_AUDIT_FAULT", mode)
    assert audit.run(source, out, BUILT) == 1
    report = (out / "summary.md").read_text()
    assert "BLOCKED" in report
    assert "final-image-security" in report
    assert "private-value-do-not-publish" not in report
    assert github_summary.read_text() == report
    if mode == "vulnerable":
        assert "SYNTHETIC-CVE" in report and "CRITICAL" in report
    else:
        assert "not scanned" in report


def test_readable_summary_does_not_turn_bad_inventory_green(tmp_path, monkeypatch):
    out = tmp_path / "reports"
    github_summary = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(github_summary))
    monkeypatch.setattr(audit, "inventory", lambda *args: (_ for _ in ()).throw(ValueError()))
    assert audit.run(tmp_path, out, BUILT) == 1
    assert "BLOCKED" in github_summary.read_text()
    assert json.loads((out / "summary.json").read_text())["passed"] is False


def test_readable_summary_escapes_metadata_and_explicitly_counts_preview_rows():
    row = {
        "Severity": "HIGH",
        "VulnerabilityID": "SYNTHETIC-CVE",
        "PkgName": "[unsafe](https://example.com)|<img>",
        "InstalledVersion": "1",
        "FixedVersion": "",
    }
    summary = {
        "passed": False,
        "inventory": {"errors": []},
        "results": [
            {
                "reference": "https://synthetic-user:synthetic-secret@registry.example/image",
                "role": "build",
                "status": "vulnerable",
                "advisory_rows": 101,
                "advisories": [dict(row) for _ in range(101)],
            }
        ],
    }
    report = audit.readable_summary(summary)
    assert "synthetic-secret" not in report
    assert "<img>" not in report and "[unsafe](" not in report
    assert "&#124;" in report and "&lt;img&gt;" in report
    assert "Showing 100 of 101" in report
    assert "No fix listed" in report
    assert len(summary["results"][0]["advisories"]) == 101
