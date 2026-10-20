"""Print generator diffs without rewriting tracked files or approving baselines."""

import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import gen_erd
from app.main import app

print("R010_GENERATED_DIFF_BEGIN", flush=True)
for path, generated in (
    ("docs/api/openapi.json", json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"),
    ("docs/architecture/data-model-erd.md", gen_erd.generate()),
):
    before = (ROOT / path).read_text()
    print("".join(difflib.unified_diff(
        before.splitlines(keepends=True), generated.splitlines(keepends=True),
        fromfile="a/" + path, tofile="b/" + path,
    )), end="", flush=True)
print("R010_GENERATED_DIFF_END", flush=True)
