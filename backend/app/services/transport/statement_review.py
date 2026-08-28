"""The fuel-statement review queue (WO-Z) — persisting what a parse found.

WHAT THIS CLOSES
----------------
`statement_ingest`'s docstring has said, since the slice that wrote it, that
its returned `warnings` list "IS the review surface until a persisted one
exists". This is that persisted one. Two things change, and the second matters
more than the first:

1. A **registered** statement's warnings become rows, so a finding survives the
   response that reported it.
2. A **refused** statement records anything at all. Until now the capture
   gate's structured findings were folded into one message string and thrown
   with the transaction, so the outcome where an operator most needs to know
   which line failed was the outcome that kept the least.

WHY A RE-INGEST CLEARS THE OPEN SET FIRST
-------------------------------------------
Registering the same bytes twice is a no-op on the transactions (insert-or-
no-op on the natural key), so it must be a no-op on the queue too — otherwise
every re-upload would either duplicate the complaint or trip the partial unique
index. `_clear_open` deletes the OPEN findings for that statement before the
fresh set is written: the latest parse's verdict replaces the previous one
rather than accumulating alongside it.

Resolved and dismissed rows are never touched. They are what somebody decided,
and a re-parse is not entitled to rewrite that. If the same finding comes back
after being resolved, it opens a NEW row — the partial index only covers the
open ones, precisely so a recurrence cannot be silently swallowed.

WHY PROSE FINDINGS GET A SOURCE CODE, NOT AN INVENTED ONE
-----------------------------------------------------------
Some findings arrive already coded (`capture_review` rules). Others arrive as
sentences: the parser's own ambiguity notes, the post-capture checks, the
extraction-drift warning. Those are stored under a code naming WHERE they came
from rather than a code minted per message. A code derived from message text
would change the day someone rewords the sentence, and the queue's dedup key
would change with it — the same reason `capture_failures.code_for` classifies
on exception type and never by matching words.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.transport.statement_finding import (
    CLOSED_STATUSES,
    ERROR,
    OPEN,
    REFUSED,
    REGISTERED,
    WARN,
    VatStatementFinding,
)
from app.services.transport import capture_review

#: Where a prose finding came from. Stable identifiers for a source, not for a
#: sentence — see the module docstring.
CODE_PARSER = "parser_ambiguity"
CODE_CAPTURE_CHECK = "capture_check"
CODE_DRIFT = "extraction_drift"
CODE_TIE_OUT = "batch_tie_out"
#: A capture-review rule's own code is used verbatim, prefixed so a rule code
#: can never collide with one of the source codes above.
RULE_PREFIX = "rule:"

#: The prefixes `statement_ingest` puts on its warning strings, mapped to the
#: source they identify. Read in order; the first match wins, and anything
#: unmatched is a parser warning (the parser is the only producer that does not
#: prefix its own).
_WARNING_SOURCES = (
    ("capture check: ", CODE_CAPTURE_CHECK),
    ("extraction drift: ", CODE_DRIFT),
)


def _source_of(warning: str) -> tuple[str, str]:
    """Classify one warning string into (code, message-without-prefix)."""
    for prefix, code in _WARNING_SOURCES:
        if warning.startswith(prefix):
            return code, warning[len(prefix) :]
    return CODE_PARSER, warning


async def _clear_open(db: AsyncSession, org_id: str, statement_sha256: str) -> None:
    """Drop the OPEN findings for this statement — see the module docstring."""
    await db.execute(
        delete(VatStatementFinding).where(
            VatStatementFinding.org_id == org_id,
            VatStatementFinding.statement_sha256 == statement_sha256,
            VatStatementFinding.status == OPEN,
        )
    )


def _fingerprint(code: str, line_seq: int | None, message: str) -> str:
    """The identity of one COMPLAINT: what it says, about which line, under
    which rule. Not a hash of the source, because two checks from the same
    source saying different things are two findings — see the model docstring
    for the index this feeds."""
    raw = f"{code}\x1f{'' if line_seq is None else line_seq}\x1f{message}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _row(
    org_id: str,
    *,
    statement_sha256: str,
    filename: str,
    network: str | None,
    period: str,
    entity_id: str | None,
    outcome: str,
    severity: str,
    code: str,
    message: str,
    line_seq: int | None,
) -> VatStatementFinding:
    return VatStatementFinding(
        org_id=org_id,
        statement_sha256=statement_sha256,
        filename=filename[:255],
        network=network,
        period=period,
        entity_id=entity_id,
        outcome=outcome,
        severity=severity,
        code=code[:60],
        message=message,
        line_seq=line_seq,
        fingerprint=_fingerprint(code[:60], line_seq, message),
        status=OPEN,
    )


async def record_registered(
    db: AsyncSession,
    org_id: str,
    *,
    statement_sha256: str,
    filename: str,
    network: str | None,
    period: str,
    entity_id: str | None,
    review_findings: tuple[capture_review.Finding, ...] = (),
    warnings: list[str],
) -> list[VatStatementFinding]:
    """Persist the advisory findings of a statement that WAS registered.

    The two arguments are separate because the two producers are: a
    `capture_review` finding arrives already carrying its rule code and the
    LINE it fired on, while the parser, the post-capture checks and the drift
    detector produce sentences about the batch. Flattening the first kind into
    the second — they do both appear in the response's warning list — would
    have thrown away the line number to save an argument, and "line 3 is
    wrong" is most of what makes a finding actionable.

    `warnings` here must therefore EXCLUDE the capture-review ones, or the
    queue would carry each of those twice under two different codes. The caller
    passes the other three producers' lists; nothing infers that by parsing
    prefixes back off strings."""
    await _clear_open(db, org_id, statement_sha256)
    rows: list[VatStatementFinding] = [
        _row(
            org_id,
            statement_sha256=statement_sha256,
            filename=filename,
            network=network,
            period=period,
            entity_id=entity_id,
            outcome=REGISTERED,
            severity=WARN,
            code=f"{RULE_PREFIX}{f.code}",
            message=f.message,
            line_seq=f.line_seq,
        )
        for f in review_findings
        if f.severity == capture_review.WARN
    ]
    for warning in warnings:
        code, message = _source_of(warning)
        rows.append(
            _row(
                org_id,
                statement_sha256=statement_sha256,
                filename=filename,
                network=network,
                period=period,
                entity_id=entity_id,
                outcome=REGISTERED,
                severity=WARN,
                code=code,
                message=message,
                line_seq=None,
            )
        )
    db.add_all(rows)
    await db.flush()
    return rows


async def record_refusal(
    db: AsyncSession,
    org_id: str,
    *,
    statement_sha256: str,
    filename: str,
    network: str | None,
    period: str,
    entity_id: str | None,
    findings: tuple[capture_review.Finding, ...],
    tie: capture_review.BatchTie | None,
) -> list[VatStatementFinding]:
    """Persist why a statement was REFUSED registration.

    Only the errors and a failed tie-out: those are what blocked it. A warning
    on a refused statement is not the operator's problem yet — the statement is
    not in the system, and listing advice next to the reason for refusal would
    bury the one thing they have to fix."""
    await _clear_open(db, org_id, statement_sha256)
    rows = [
        _row(
            org_id,
            statement_sha256=statement_sha256,
            filename=filename,
            network=network,
            period=period,
            entity_id=entity_id,
            outcome=REFUSED,
            severity=ERROR,
            code=f"{RULE_PREFIX}{f.code}",
            message=f.message,
            line_seq=f.line_seq,
        )
        for f in findings
        if f.severity == capture_review.ERROR
    ]
    if tie is not None and not tie.ok:
        rows.append(
            _row(
                org_id,
                statement_sha256=statement_sha256,
                filename=filename,
                network=network,
                period=period,
                entity_id=entity_id,
                outcome=REFUSED,
                severity=ERROR,
                code=CODE_TIE_OUT,
                message=(
                    f"Batch tie-out failed: lines total {tie.computed_total} vs "
                    f"coversheet {tie.coversheet_total} (difference {tie.diff})."
                ),
                # A tie-out is about the batch, not a line.
                line_seq=None,
            )
        )
    db.add_all(rows)
    await db.flush()
    return rows


async def worklist(
    db: AsyncSession, org_id: str, *, status: str = OPEN, limit: int = 200
) -> list[VatStatementFinding]:
    """The queue an operator works from: findings for this tenant, newest
    first. Ordered by the statement's own arrival rather than by severity, so
    the list reads as "what came in" — the grouping a person recognises."""
    if status not in (OPEN, *CLOSED_STATUSES):
        raise ValidationError(f"Unknown finding status '{status}'", code="unknown_status")
    return list(
        await db.scalars(
            select(VatStatementFinding)
            .where(
                VatStatementFinding.org_id == org_id,
                VatStatementFinding.status == status,
            )
            .order_by(
                VatStatementFinding.created_at.desc(),
                VatStatementFinding.line_seq.asc(),
            )
            .limit(limit)
        )
    )


async def count(
    db: AsyncSession, org_id: str, *, status: str = OPEN, outcome: str | None = None
) -> int:
    """How many findings are in a given state — the number a screen shows so
    it can say what is left without re-counting a list it may have truncated.

    Counted in the database rather than by measuring `worklist`'s result: that
    list is capped, so counting it would quietly report the cap as the total
    the moment a workspace had more findings than the cap."""
    stmt = (
        select(func.count())
        .select_from(VatStatementFinding)
        .where(VatStatementFinding.org_id == org_id, VatStatementFinding.status == status)
    )
    if outcome is not None:
        stmt = stmt.where(VatStatementFinding.outcome == outcome)
    return int(await db.scalar(stmt) or 0)


async def close_finding(
    db: AsyncSession,
    org_id: str,
    finding_id: str,
    *,
    status: str,
    actor: str,
    note: str | None = None,
) -> VatStatementFinding:
    """Take one finding out of the queue as `resolved` or `dismissed`.

    The two verbs are not decoration. **Resolved** asserts the thing the finding
    described was dealt with; **dismissed** asserts it did not need dealing
    with. An operator reading the closed history later needs to know which was
    claimed, and a single "done" would have destroyed that distinction at the
    moment it was cheapest to record.

    Closing is single-shot: a finding already out of the queue is refused rather
    than silently re-stamped, so `resolved_by` always names the person who
    actually made the call."""
    if status not in CLOSED_STATUSES:
        raise ValidationError(
            f"A finding can be closed as {' or '.join(CLOSED_STATUSES)}, not '{status}'",
            code="unknown_resolution",
        )
    row = await db.scalar(
        select(VatStatementFinding).where(
            VatStatementFinding.id == finding_id,
            VatStatementFinding.org_id == org_id,
        )
    )
    if row is None:
        raise NotFoundError("Finding not found", code="finding_not_found")
    if row.status != OPEN:
        raise ConflictError(
            f"This finding was already {row.status}.", code="finding_already_closed"
        )
    row.status = status
    row.resolved_at = datetime.now(UTC)
    row.resolved_by = actor
    row.resolution_note = (note or "").strip() or None
    await db.flush()
    return row
