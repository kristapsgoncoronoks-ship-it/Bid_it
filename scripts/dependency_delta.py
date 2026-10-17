#!/usr/bin/env python3
"""Resolved Python + locked npm dependency delta for PR review (no hosted API).

Head vulnerability enforcement stays in pip-audit/npm audit. The base may have
findings; only a valid scanner JSON result with findings explains its exit 1.
Service/configuration failures are never treated as a clean graph. No license
allow/deny policy was configured in the former action; npm declarations are
reported as metadata, not legal certification. Python license review is manual.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def python_graph(report: dict) -> dict:
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("Audit did not return a resolved dependency graph")
    graph = {}
    for dependency in dependencies:
        if not all(key in dependency for key in ("name", "version", "vulns")):
            raise ValueError("Unresolved/skipped dependency in audit graph")
        name = re.sub(r"[-_.]+", "-", dependency["name"].lower())
        graph[name] = {
            "version": dependency["version"],
            "advisories": sorted(v["id"] for v in dependency["vulns"]),
        }
    return graph


def delta(base: dict, head: dict) -> list:
    return [
        {"dependency": key, "base": base.get(key), "head": head.get(key)}
        for key in sorted(base.keys() | head.keys())
        if base.get(key) != head.get(key)
    ]


def npm_graph(lock: dict) -> dict:
    if not isinstance(lock.get("packages"), dict) or lock.get("lockfileVersion") != 3:
        raise ValueError("Expected a complete npm v3 lock graph")
    return {
        key: {
            "version": value["version"],
            "dev": value.get("dev", False),
            "license": value.get("license", "UNDECLARED"),
        }
        for key, value in lock["packages"].items()
        if key and "version" in value
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.base):
        parser.error("base must be a full commit SHA")

    def base_file(path: str) -> bytes:
        return subprocess.check_output(["git", "show", f"{args.base}:{path}"], cwd=ROOT)

    with tempfile.TemporaryDirectory(prefix="dependency-base-") as folder:
        manifests = []
        for name in ("requirements.txt", "requirements-dev.txt"):
            path = Path(folder) / name
            path.write_bytes(base_file(f"backend/{name}"))
            manifests += ["-r", str(path)]
        audit = subprocess.run(
            [sys.executable, "-m", "pip_audit", *manifests, "--format", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if audit.returncode not in (0, 1):
            raise RuntimeError(
                "Base graph audit failed; dependency review is unavailable"
            )
        base = python_graph(json.loads(audit.stdout))
        if audit.returncode == 1 and not any(
            value["advisories"] for value in base.values()
        ):
            raise RuntimeError("Base scanner failed without advisory evidence")
    head = python_graph(json.loads(args.head_audit.read_text()))
    base_npm = npm_graph(json.loads(base_file("frontend/package-lock.json")))
    head_npm = npm_graph(json.loads((ROOT / "frontend/package-lock.json").read_text()))
    report = {
        "base_sha": args.base,
        "head_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "python_resolved_changes": delta(base, head),
        "npm_locked_changes": delta(base_npm, head_npm),
        "license_policy": "No allow/deny policy configured; npm declarations are metadata; Python licensing needs manual review.",
        "python_counts": {"base": len(base), "head": len(head)},
        "npm_counts": {"base": len(base_npm), "head": len(head_npm)},
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Dependency delta: {len(report['python_resolved_changes'])} Python / {len(report['npm_locked_changes'])} npm changes"
    )


if __name__ == "__main__":
    main()
