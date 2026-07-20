"""Domain metrics (Prometheus). Separate from the per-request HTTP metrics in
`observability.setup_metrics` so business counters live in one place and can be
incremented from anywhere (API or worker) without importing the web app.

Counters are registered in the DEFAULT prometheus registry, so the existing
`/metrics` endpoint (which dumps that registry) exposes them automatically. When
`prometheus-client` isn't installed the helpers are safe no-ops.

Deterministic-capture-rate signal (see docs/product/metrics.md): every parse is
counted by `method`, so the dashboard computes

    deterministic_capture_rate =
        sum(documents_parsed_total{method=~"e-invoice-xml|text-layer|csv|json"})
        / sum(documents_parsed_total)

i.e. the share of documents read WITHOUT AI or manual entry. `ocr` is no-AI but
lower-confidence; `ai` and `failed` are explicitly not deterministic.
"""
from __future__ import annotations

try:
    from prometheus_client import Counter

    _DOCUMENTS_PARSED: "Counter | None" = Counter(
        "invoiceiq_documents_parsed_total",
        "Documents parsed, labelled by extraction method",
        ["method"],
    )
except Exception:  # pragma: no cover — prometheus-client absent
    _DOCUMENTS_PARSED = None

# Methods that count as deterministic capture (no AI, no manual entry).
DETERMINISTIC_METHODS = frozenset({"e-invoice-xml", "text-layer", "csv", "json"})


def record_parse(method: str | None) -> None:
    """Count one parsed document by its extraction method (best-effort)."""
    if _DOCUMENTS_PARSED is not None:
        _DOCUMENTS_PARSED.labels(method or "unknown").inc()
