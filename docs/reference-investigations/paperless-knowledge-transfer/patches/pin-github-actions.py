#!/usr/bin/env python3
"""One-time helper: pin existing GitHub Actions major tags to immutable SHAs.

Run from repository root on base d30f90b... or inspect the diff manually if the
workflow files have changed since the reference analysis.
"""

from pathlib import Path
import re

FILES = (
    Path(".github/workflows/ci.yml"),
    Path(".github/workflows/pii-history.yml"),
    Path(".github/workflows/release.yml"),
)

REPLACEMENTS = {
    "actions/checkout@v7": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7",
    "actions/setup-python@v7": "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7",
    "actions/setup-node@v7": "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7",
    "actions/upload-artifact@v4": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
    "docker/setup-buildx-action@v4": "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e # v4",
    "docker/build-push-action@v7": "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a # v7",
    "docker/login-action@v4": "docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4",
    "docker/metadata-action@v6": "docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302 # v6",
}

for path in FILES:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"updated {path}")
    else:
        print(f"unchanged {path}")

pattern = re.compile(r"uses:\s+(?:actions|docker)/[^@\s]+@v\d+(?:\.\d+)*")
remaining = []
for path in FILES:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if pattern.search(line):
            remaining.append(f"{path}:{line_no}: {line.strip()}")

if remaining:
    raise SystemExit("Mutable action tags remain:\n" + "\n".join(remaining))

print("All covered actions are pinned to immutable SHAs.")
