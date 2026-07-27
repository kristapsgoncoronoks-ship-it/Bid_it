"""Audit-trail export for auditors (SOC 2 / ISO evidence).

Renders a tenant's hash-chained `audit_events` as CSV or JSON, in chronological
order, INCLUDING the `seq`/`prev_hash`/`hash` columns so an auditor can
independently re-verify the chain offline. CSV cells are formula-injection-safe.
Read-only; invents nothing.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime

from app.core.csv_safety import sanitize_cell as _safe
from app.models.audit import AuditEvent
from app.services.audit import ChainStatus

CSV_HEADER = [
    "seq",
    "timestamp_utc",
    "actor_email",
    "action",
    "target_type",
    "target_id",
    "meta",
    "prev_hash",
    "hash",
]

# `_safe` is `app.core.csv_safety.sanitize_cell` imported under its original
# local name (board R1/R9 — one shared implementation, no per-file copy).


def _iso(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, tz=UTC).isoformat()


def _meta_obj(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def to_csv(events: list[AuditEvent]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    for e in events:
        w.writerow(
            [
                _safe(c)
                for c in (
                    e.seq,
                    _iso(e.at_ms),
                    e.actor_email,
                    e.action,
                    e.target_type,
                    e.target_id,
                    e.meta or "",
                    e.prev_hash or "",
                    e.hash,
                )
            ]
        )
    return buf.getvalue()


def to_json(events: list[AuditEvent], chain: ChainStatus, *, org_id: str, generated_ms: int) -> str:
    doc = {
        "org_id": org_id,
        "generated_at_utc": _iso(generated_ms),
        "event_count": len(events),
        "chain": {
            "verified": chain.ok,
            "events": chain.events,
            "broken_at_seq": chain.broken_at_seq,
            "detail": chain.detail,
        },
        "events": [
            {
                "seq": e.seq,
                "timestamp_utc": _iso(e.at_ms),
                "actor_email": e.actor_email,
                "action": e.action,
                "target_type": e.target_type,
                "target_id": e.target_id,
                "meta": _meta_obj(e.meta),
                "prev_hash": e.prev_hash,
                "hash": e.hash,
            }
            for e in events
        ],
    }
    return json.dumps(doc, indent=2, sort_keys=False)


def render(
    fmt: str, events: list[AuditEvent], chain: ChainStatus, *, org_id: str
) -> tuple[str, str, str]:
    """Return (filename, text, media_type) for `fmt` in {"csv","json"}."""
    generated_ms = int(datetime.now(UTC).timestamp() * 1000)
    stamp = datetime.fromtimestamp(generated_ms / 1000, tz=UTC).strftime("%Y%m%d")
    if fmt == "csv":
        return f"audit-log-{stamp}.csv", to_csv(events), "text/csv"
    if fmt == "json":
        return (
            f"audit-log-{stamp}.json",
            to_json(events, chain, org_id=org_id, generated_ms=generated_ms),
            "application/json",
        )
    raise ValueError(f"unknown export format '{fmt}'")


FORMATS = ("csv", "json")
