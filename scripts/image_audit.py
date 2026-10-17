#!/usr/bin/env python3
"""Audit application build/final images and every Compose/Kubernetes service image.

Requires Docker and the repository's pinned PyYAML. Never starts service images.
Every scanner invocation uses the inspected immutable image ID. Missing images,
unmapped templates, unpinned services, invalid evidence and tool errors fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import yaml

SCANNER = "aquasec/trivy@sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222"
SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
ROLES = ("backend", "frontend", "backend-build", "frontend-build")


class ComposeLoader(yaml.SafeLoader):
    pass


def compose_tag(loader, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


for tag in ("!reset", "!override"):
    ComposeLoader.add_constructor(tag, compose_tag)


def image_fields(value, location=""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{location}/{key}"
            if key == "image":
                if not isinstance(child, str) or not child:
                    raise ValueError(f"invalid image at {path}")
                yield path, child
            else:
                yield from image_fields(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from image_fields(child, f"{location}/{index}")


def inventory(root: Path, built: dict[str, str]) -> dict:
    if set(built) != set(ROLES) or len(set(built.values())) != len(ROLES):
        raise ValueError("four distinct built image references are required")
    targets = [
        {
            "id": role,
            "reference": built[role],
            "role": "build" if role.endswith("-build") else "final",
            "pull": False,
            "sources": [],
        }
        for role in ROLES
    ]
    bases, errors = [], []
    for app in ("backend", "frontend"):
        path = root / app / "Dockerfile"
        try:
            stages = []
            for line in path.read_text().splitlines():
                if re.match(r"^\s*FROM\s", line, re.IGNORECASE):
                    match = re.fullmatch(
                        r"FROM (\S+)(?: AS (\w+))?", line.strip(), re.IGNORECASE
                    )
                    if not match or not re.search(r"@sha256:[0-9a-f]{64}$", match[1]):
                        raise ValueError("unsupported/unpinned FROM")
                    stages.append((match[1], match[2]))
            if len(stages) != 2 or stages[0][1] != "build":
                raise ValueError("build stages changed; update audited build outputs")
            for index, (reference, stage) in enumerate(stages):
                bases.append(
                    {
                        "file": f"{app}/Dockerfile",
                        "reference": reference,
                        "stage": stage,
                        "covered_by": app + ("-build" if index == 0 else ""),
                    }
                )
        except (OSError, ValueError) as exc:
            errors.append(f"{app}/Dockerfile: {exc}")
    sources = sorted(root.glob("*compose*.y*ml")) + sorted(
        (root / "deploy/k8s").rglob("*.y*ml")
    )
    services = {}
    for path in sources:
        try:
            docs = yaml.load_all(path.read_text(), Loader=ComposeLoader)
            for doc in docs:
                for location, reference in image_fields(doc):
                    source = {"file": str(path.relative_to(root)), "location": location}
                    app = re.fullmatch(
                        r"ghcr\.io/(?:OWNER/REPO|(\$\{GHCR_REPOSITORY:\?[^}]+\}))/(backend|frontend):"
                        r"(?:VERSION|\$\{IMAGE_TAG:\?[^}]+\})",
                        reference,
                    )
                    if app:
                        targets[ROLES.index(app[2])]["sources"].append(
                            dict(source, template=reference)
                        )
                    else:
                        if not re.fullmatch(
                            r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]*", reference
                        ):
                            raise ValueError(f"unmapped image template at {location}")
                        services.setdefault(reference, []).append(source)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
    if not sources or not services:
        errors.append("no deployment service image inventory found")
    for reference, locations in sorted(services.items()):
        targets.append(
            {
                "id": "service-" + hashlib.sha256(reference.encode()).hexdigest()[:16],
                "reference": reference,
                "role": "service",
                "pull": True,
                "source_pinned": bool(re.search(r"@sha256:[0-9a-f]{64}$", reference)),
                "sources": locations,
            }
        )
    return {"targets": targets, "base_inputs": bases, "errors": errors}


def redact(value: str) -> str:
    value = re.sub(r"eyJ[A-Za-z0-9_.-]+", "<redacted-token>", value)
    return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1<redacted>@", value)


def command(args: list[str], log: Path) -> int:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=300, check=False
        )
        log.write_text(redact(result.stdout + result.stderr))
        return result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.write_text(
            redact(f"{type(exc).__name__}: image audit command could not complete")
        )
        return 2


def audit_target(target: dict, directory: Path) -> dict:
    result = dict(target, status="image_error")
    prefix = directory / target["id"]
    if target["pull"]:
        rc = command(
            ["docker", "pull", "--platform", "linux/amd64", target["reference"]],
            prefix.with_suffix(".pull.log"),
        )
        result["pull_exit"] = rc
        if rc:
            result["status"] = "unavailable"
            return result
    inspect = prefix.with_suffix(".inspect.json")
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", target["reference"]],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if inspected.returncode:
            raise ValueError("image inspection failed")
        # Full image config stays in memory; Env never enters the artifact.
        (metadata,) = json.loads(inspected.stdout)
        image_id = metadata["Id"]
        if not SHA.fullmatch(image_id) or (
            metadata["Os"],
            metadata["Architecture"],
        ) != ("linux", "amd64"):
            raise ValueError("invalid image identity/platform")
        result.update(
            image_id=image_id,
            repository_digests=metadata.get("RepoDigests", []),
            platform="linux/amd64",
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.TimeoutExpired,
    ):
        inspect.write_text('{"status":"image inspection failed"}\n')
        return result
    # Keep only identity metadata in the artifact, never a container's environment.
    inspect.write_text(
        json.dumps(
            {k: result[k] for k in ("image_id", "repository_digests", "platform")}
        )
    )
    args = [
        "docker",
        "run",
        "--rm",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
    ]
    for variable, mount in (
        ("TRIVY_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt:ro"),
        ("TRIVY_CACHE_DIR", "/root/.cache/trivy"),
    ):
        if os.environ.get(variable):
            args.extend(["-v", f"{Path(os.environ[variable]).resolve()}:{mount}"])
    sbom = prefix.with_suffix(".cdx.json")
    audit = prefix.with_suffix(".audit.json")
    result["status"] = "scanner_error"
    try:
        bom, data = scanner_reports(args, image_id, prefix, result)
        if (
            bom.get("bomFormat") != "CycloneDX"
            or not isinstance(bom.get("components"), list)
            or not bom["components"]
        ):
            raise ValueError("missing SBOM components")
        component = bom["metadata"]["component"]
        if component.get("type") != "container" or component.get("name") != image_id:
            raise ValueError("mismatched SBOM identity")
        if not any(
            p.get("name") == "aquasecurity:trivy:ImageID" and p.get("value") == image_id
            for p in component.get("properties", [])
        ):
            raise ValueError("missing SBOM image identity")
        if data["Metadata"]["ImageID"] != image_id or not data.get("Results"):
            raise ValueError("missing/mismatched audit identity")
        if not data["Metadata"].get("OS", {}).get("Family") or data["Metadata"][
            "OS"
        ].get("EOSL"):
            raise ValueError("missing/unsupported OS coverage")
        if not any(r.get("Class") == "os-pkgs" for r in data["Results"]):
            raise ValueError("missing OS package audit")
        rows = [v for r in data["Results"] for v in r.get("Vulnerabilities", [])]
        for row in rows:
            if not all(
                row.get(k) for k in ("VulnerabilityID", "PkgName", "InstalledVersion")
            ):
                raise ValueError("incomplete advisory evidence")
            if row.get("Severity") not in ("HIGH", "CRITICAL"):
                raise ValueError("unexpected severity evidence")
        expected = 1 if rows else 0
        if result["sbom_exit"] != 0 or result["audit_exit"] != expected:
            raise ValueError("scanner exit does not match evidence")
        result.update(
            status="vulnerable" if rows else "clean",
            advisory_rows=len(rows),
            advisory_ids=sorted({v["VulnerabilityID"] for v in rows}),
            evidence={
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (sbom, audit)
            },
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return result


def scanner_reports(
    args: list[str], image_id: str, prefix: Path, result: dict
) -> tuple:
    # ImageConfig includes Env/history. Raw scanner JSON never enters the public
    # artifact tree, even if an interrupted run cannot finish sanitizing it.
    with tempfile.TemporaryDirectory(prefix="bidit-image-audit-") as temporary:
        private = Path(temporary)
        common = [
            *args,
            "-v",
            f"{private}:/reports",
            SCANNER,
            "image",
            "--image-src",
            "docker",
            "--scanners",
            "vuln",
        ]
        for name, fmt, extra in (
            ("sbom", "cyclonedx", []),
            ("audit", "json", ["--severity", "HIGH,CRITICAL", "--exit-code", "1"]),
        ):
            filename = prefix.with_suffix(
                ".cdx.json" if name == "sbom" else ".audit.json"
            ).name
            result[name + "_exit"] = command(
                [
                    *common,
                    *extra,
                    "--format",
                    fmt,
                    "--output",
                    f"/reports/{filename}",
                    image_id,
                ],
                prefix.with_suffix(f".{name}.log"),
            )
        raw_sbom = private / prefix.with_suffix(".cdx.json").name
        raw_audit = private / prefix.with_suffix(".audit.json").name
        result["raw_report_sha256"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (raw_sbom, raw_audit)
            if p.exists()
        }
        bom, data = json.loads(raw_sbom.read_text()), json.loads(raw_audit.read_text())
    metadata = data["Metadata"]
    data["Metadata"] = {
        k: metadata[k]
        for k in (
            "ImageID",
            "OS",
            "Size",
            "RepoTags",
            "RepoDigests",
            "DiffIDs",
            "Reference",
        )
        if k in metadata
    }
    component = bom["metadata"]["component"]
    component["properties"] = [
        p
        for p in component.get("properties", [])
        if not p.get("name", "").startswith("aquasecurity:trivy:Labels:")
    ]
    prefix.with_suffix(".cdx.json").write_text(json.dumps(bom, indent=2) + "\n")
    prefix.with_suffix(".audit.json").write_text(json.dumps(data, indent=2) + "\n")
    result["evidence_sanitized"] = ["ImageConfig", "image labels"]
    return bom, data


def run(root: Path, output: Path, built: dict[str, str]) -> int:
    output.mkdir(parents=True, exist_ok=True)
    directory = output / uuid.uuid4().hex
    directory.mkdir()
    summary = {
        "scope": "application build/final and Compose/Kubernetes deployment images",
        "run": directory.name,
        "scanner": SCANNER,
        "inventory": {"targets": [], "errors": ["inventory not completed"]},
        "results": [],
        "passed": False,
    }
    # Write a non-passing marker before I/O so interrupted runs cannot retain green.
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    try:
        plan = inventory(root, built)
    except ValueError:
        return 1
    summary["inventory"] = plan
    for target in plan["targets"]:
        result = audit_target(target, directory)
        summary["results"].append(result)
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        sys.stdout.write(f"image-audit: {target['reference']}: {result['status']}\n")
        sys.stdout.flush()
    summary["passed"] = (
        bool(summary["results"])
        and not plan["errors"]
        and all(
            r["status"] == "clean" and r.get("source_pinned", True)
            for r in summary["results"]
        )
    )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, default=Path("image-security"))
    for role in ROLES:
        parser.add_argument("--" + role, default="bidit-ci-" + role + ":review")
    args = parser.parse_args()
    raise SystemExit(
        run(
            args.root,
            args.output,
            {r: getattr(args, r.replace("-", "_")) for r in ROLES},
        )
    )
