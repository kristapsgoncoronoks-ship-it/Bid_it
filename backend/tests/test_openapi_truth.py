"""QA-003 (audit 2026-09-05) — the API contract cannot change unseen.

`docs/api/openapi.json` is DERIVED from the FastAPI app (`python -m app.openapi
../docs/api/openapi.json`, or `make openapi`). This test regenerates the schema
in-process and compares byte-for-byte, so any route, parameter, status code or
Pydantic field that changes shows up as a diff in the review of the change that
caused it — the same discipline `test_erd_truth.py` applies to the schema
pictures and `test_docs_truth.py` to the README's numbers.

WHY THIS AND NOT A GENERATED CLIENT (yet): the SPA's `lib/types.ts` is a
hand-maintained mirror of the Pydantic models (ARCH-011/FE-017). Generating it
is P3 work; until then this snapshot is the one place where a contract change
is visible to the person editing the mirror, and the `x-` free diff is small
enough to read.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"


def _render() -> str:
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def test_the_checked_in_openapi_snapshot_matches_the_app():
    assert SNAPSHOT.exists(), (
        "docs/api/openapi.json is missing — generate it: "
        "cd backend && python -m app.openapi ../docs/api/openapi.json"
    )
    expected = _render()
    actual = SNAPSHOT.read_text(encoding="utf-8")
    if actual != expected:
        # Name the first differing path so the failure reads as a contract
        # change, not a formatting one.
        a = json.loads(actual)
        b = json.loads(expected)
        changed = sorted(
            p
            for p in set(a.get("paths", {})) | set(b.get("paths", {}))
            if a.get("paths", {}).get(p) != b.get("paths", {}).get(p)
        )
        schemas_a = a.get("components", {}).get("schemas", {})
        schemas_b = b.get("components", {}).get("schemas", {})
        changed_schemas = sorted(
            s for s in set(schemas_a) | set(schemas_b) if schemas_a.get(s) != schemas_b.get(s)
        )
        raise AssertionError(
            "docs/api/openapi.json has drifted from the app — regenerate it "
            "(cd backend && python -m app.openapi ../docs/api/openapi.json) and review "
            "the diff as an API contract change; keep frontend/src/lib/types.ts in step.\n"
            f"  changed paths ({len(changed)}): {changed[:12]}\n"
            f"  changed schemas ({len(changed_schemas)}): {changed_schemas[:12]}"
        )


def test_the_snapshot_is_the_app_not_a_stub():
    """A snapshot that is a stub would make the gate above pass vacuously."""
    doc = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert len(doc["paths"]) > 300, len(doc["paths"])
    assert "/api/v1/auth/login" in doc["paths"]
    assert "/api/v1/payment-runs/{run_id}/sepa" in doc["paths"]
