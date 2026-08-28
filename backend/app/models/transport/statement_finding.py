"""The fuel-statement review queue (WO-Z) — a finding that outlives its request.

WHY THIS TABLE EXISTS
---------------------
`statement_ingest`'s module docstring has admitted, since the slice that wrote
it, that "that list IS the review surface until a persisted one exists". The
list is `StatementIngestResult.warnings`: strings, returned in one HTTP
response, gone the moment the tab is closed. Nothing enumerated what a
statement had been flagged for, so the only way to see a finding twice was to
upload the file again.

The refused case was worse than ephemeral. When the capture gate blocks
registration, `ingest_statement` raises with the reasons folded into a message
string — the structured `Finding`s (severity, code, line) are discarded at the
raise, and the transaction that would have carried them is rolled back. So the
one outcome where an operator most needs to know WHICH lines failed and WHY is
the outcome that kept the least.

WHAT ONE ROW IS
---------------
One row is one finding about one statement: the rule that fired, on which line,
at what severity, and how it was resolved. The statement's identity is carried
here rather than referenced, because there is no statement ENTITY to reference
— a fuel-card statement is bytes that were ingested, identified everywhere else
in this codebase (audit included) by the SHA-256 of those bytes. Denormalising
filename/network/period alongside it is what lets the worklist name the file an
operator uploaded without joining to a table that does not exist.

WHY `outcome` IS ON THE ROW
---------------------------
"The statement was registered and this needs a look" and "the statement was
REFUSED because of this" are different sentences to the person working the
queue, and the finding's severity does not distinguish them: an `error` on a
refused statement blocked it, while a `warn` on a registered one did not. The
outcome is what the row was about, and a queue that could not say which is
which would be asking an operator to guess whether their data is in the system.

AT MOST ONE OPEN ROW PER (STATEMENT, COMPLAINT)
------------------------------------------------
Re-uploading the same bytes must not pile up duplicates of the same complaint,
so `uq_vat_statement_findings_open` is unique over
(org_id, statement_sha256, fingerprint) WHERE status = 'open'.

`fingerprint` identifies the COMPLAINT — code, line and message digested
together — rather than its source. The obvious key, (code, line_seq), is wrong
in a way worth recording: two post-capture checks flagging different things
about the same batch share a code and carry no line, so the second would have
been refused as a duplicate of the first and an operator would have lost a
finding to an index. What makes two rows the same row is that they say the same
thing about the same statement.

The PARTIAL predicate is what makes the index right rather than merely tidy: a
finding that was resolved and then RECURS on a later parse gets a NEW open row,
because the old one is no longer `open` and therefore no longer in the index. A
total unique constraint would have silently swallowed the recurrence — the same
failure `capture_failures.failure_seq` exists to prevent, where an
acknowledgement kept covering a fault that had happened again.

NEW TENANT table — RLS ships in the same migration (`a3c5e7f9b1d4`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# Severities, mirroring `capture_review`'s lattice. `ok` is not stored: a rule
# that found nothing has nothing to say to a queue.
WARN = "warn"
ERROR = "error"
SEVERITIES = (WARN, ERROR)

# What happened to the statement this finding is about.
REGISTERED = "registered"
REFUSED = "refused"
OUTCOMES = (REGISTERED, REFUSED)

# Where the finding is in the operator's hands.
OPEN = "open"
RESOLVED = "resolved"
DISMISSED = "dismissed"
STATUSES = (OPEN, RESOLVED, DISMISSED)
#: The two ways a finding leaves the queue. Both are deliberate acts by a named
#: person and both are audited; they differ in what the person is asserting.
CLOSED_STATUSES = (RESOLVED, DISMISSED)


class VatStatementFinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One persisted finding about one ingested fuel-card statement."""

    __tablename__ = "vat_statement_findings"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_vat_statement_findings_org_id_id"),
        Index("ix_vat_statement_findings_org_status", "org_id", "status"),
        Index("ix_vat_statement_findings_org_sha", "org_id", "statement_sha256"),
        # See the module docstring: PARTIAL, so a resolved finding that recurs
        # opens a fresh row instead of being swallowed as a duplicate.
        Index(
            "uq_vat_statement_findings_open",
            "org_id",
            "statement_sha256",
            "fingerprint",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
        CheckConstraint("severity IN ('warn', 'error')", name="ck_vat_statement_findings_severity"),
        CheckConstraint(
            "outcome IN ('registered', 'refused')", name="ck_vat_statement_findings_outcome"
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_vat_statement_findings_status",
        ),
        # A closed finding names who closed it and when; an open one names
        # neither. Half a resolution is not a state this table can hold.
        CheckConstraint(
            "(status = 'open' AND resolved_at IS NULL AND resolved_by IS NULL) "
            "OR (status <> 'open' AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
            name="ck_vat_statement_findings_resolution_complete",
        ),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- which statement (carried, not referenced — see the module docstring) ---
    #: SHA-256 of the uploaded bytes: the same identity the statement-level
    #: audit event uses as its target_id, so a finding and its audit trail can
    #: be lined up without a join through anything that could disagree.
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    network: Mapped[str | None] = mapped_column(String(40), nullable=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    entity_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)

    # --- what was found ---
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    #: The rule's stable code where one exists. Findings that arrive as prose
    #: (the parser's own warnings) are stored under a code naming their source
    #: rather than invented per message — see `statement_review.CODE_*`.
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: 1-based source line, or NULL for a finding about the batch as a whole
    #: (a tie-out mismatch belongs to the statement, not to any one line).
    line_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Digest of (code, line_seq, message) — the identity of the COMPLAINT, and
    #: the dedup key. Stored rather than computed on read so the uniqueness is
    #: the database's to enforce, not a convention every writer must remember.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- how it left the queue ---
    status: Mapped[str] = mapped_column(String(12), default=OPEN, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
