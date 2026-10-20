#!/usr/bin/env python3
"""Check each browser job's own image against the resolved Playwright lock graph.

Uses the already pinned PyYAML dependency. No npm execution, network request or
baseline refresh is needed. Run from any directory after backend requirements.
This validates version tags and lock records, not image availability/digest content.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
JOBS = ("frontend-e2e", "vr-baselines")
PACKAGES = ("@playwright/test", "playwright", "playwright-core")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
IMAGE = re.compile(
    r"mcr\.microsoft\.com/playwright:v([0-9]+\.[0-9]+\.[0-9]+)-[a-z][a-z0-9.-]*"
    r"(?:@sha256:[0-9a-f]{64})?"
)


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently validating the last value."""


def _mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError("duplicate YAML mapping key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def check(root: Path) -> list[str]:
    """Return bounded diagnostics; never echo YAML/lock contents or exceptions."""
    try:
        lock = json.loads(
            (root / "frontend/package-lock.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        return ["frontend/package-lock.json: missing or malformed input"]
    try:
        workflow = yaml.load(
            (root / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
            Loader=UniqueLoader,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        yaml.YAMLError,
        TypeError,
        RecursionError,
    ):
        return [
            ".github/workflows/ci.yml: missing, malformed, duplicate or unsafe YAML"
        ]

    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        return ["frontend/package-lock.json: packages mapping is required"]
    versions = {}
    for package in PACKAGES:
        record = packages.get(f"node_modules/{package}")
        version = record.get("version") if isinstance(record, dict) else None
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            return [
                f"frontend/package-lock.json: {package} needs an exact resolved version"
            ]
        versions[package] = version
    expected = versions["@playwright/test"]
    if any(version != expected for version in versions.values()):
        return ["frontend/package-lock.json: Playwright package versions disagree"]
    for parent, child in (
        ("@playwright/test", "playwright"),
        ("playwright", "playwright-core"),
    ):
        dependencies = packages[f"node_modules/{parent}"].get("dependencies")
        if not isinstance(dependencies, dict) or dependencies.get(child) != expected:
            return ["frontend/package-lock.json: Playwright dependency edges disagree"]
    for path, record in packages.items():
        if any(path.endswith(f"/node_modules/{name}") for name in PACKAGES) and (
            not isinstance(record, dict) or record.get("version") != expected
        ):
            return ["frontend/package-lock.json: nested Playwright version disagrees"]

    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    if not isinstance(jobs, dict):
        return [".github/workflows/ci.yml: jobs mapping is required"]
    errors = []
    for name in JOBS:
        job = jobs.get(name)
        container = job.get("container") if isinstance(job, dict) else None
        image = container.get("image") if isinstance(container, dict) else container
        match = IMAGE.fullmatch(image) if isinstance(image, str) else None
        if not match:
            errors.append(
                f"{name}: an exact official Playwright version-tagged reference is required"
            )
        elif match.group(1) != expected:
            errors.append(
                f"{name}: container version does not match resolved Playwright {expected}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = check(args.root)
    if errors:
        print("playwright-pair: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print(
        "playwright-pair: PASS — both browser job container references match the resolved lock graph"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
