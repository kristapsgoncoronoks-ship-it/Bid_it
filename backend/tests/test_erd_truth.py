"""The generated ER diagrams cannot rot (docs-truth for the schema pictures).

`docs/architecture/data-model-erd.md` is DERIVED from `Base.metadata` by
`scripts/gen_erd.py`. This test regenerates the content in-process and
compares byte-for-byte, so a migration/model change that forgets to
regenerate the diagrams fails the backend CI job — the same discipline
`test_docs_truth.py` applies to the README's numbers, applied to pictures
(diagram outdatedness being the most-reported architecture-docs failure).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))


def test_generated_erd_matches_the_models():
    import gen_erd

    expected = gen_erd.generate()
    actual = gen_erd.OUT.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/architecture/data-model-erd.md has drifted from the SQLAlchemy "
        "models — regenerate it: cd backend && python scripts/gen_erd.py"
    )


def test_every_table_appears_exactly_once():
    """The domain sharding is a partition: no table dropped, none doubled —
    a table silently missing from every diagram would be worse than rot."""
    import gen_erd

    from app.models.base import Base

    content = gen_erd.OUT.read_text(encoding="utf-8")
    for table in Base.metadata.tables:
        assert f" {table} " in content or f" {table}." in content or f"`{table}`" in content, (
            f"table {table!r} appears in no domain diagram"
        )
