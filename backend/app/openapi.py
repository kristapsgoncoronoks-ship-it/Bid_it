"""Emit the OpenAPI schema as JSON to stdout (or a file).

    python -m app.openapi > openapi.json
    python -m app.openapi openapi.json

WHY: FastAPI serves the live schema at /openapi.json, but a CHECKED-IN snapshot
has immediate use — it lets the frontend generate a typed client offline and
lets CI diff the contract to catch an unintended breaking API change in review.
The schema is derived from the app's routes + Pydantic models, so it never
drifts from the code.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    from app.main import app

    schema = app.openapi()
    text = json.dumps(schema, indent=2, sort_keys=True)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {sys.argv[1]} ({len(schema.get('paths', {}))} paths)", file=sys.stderr)
    else:
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
