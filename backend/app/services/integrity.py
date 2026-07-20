"""Document-integrity verification (ADR-0008 / arch Phase 2.8).

The primary database is backed by managed-Postgres PITR and object storage is
versioned — those are platform-level backups. What the *app* owns is proving the
integrity of what it stored: every document reference in the DB (receipts, logos,
inbound email attachments) is content-addressed by sha256, so we can re-hash the
stored object and confirm it still exists and still matches. This catches silent
corruption, a lost object, or a storage/DB drift that a backup alone won't.

Tenant-scoped: `verify_documents(db, org_id)` checks one tenant. A full sweep runs
one job per org (see the scheduler / jobs). Never raises — a failure to read an
object is a finding, not an exception.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.models.email_intake import InboundInvoice
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.issuer import IssuerProfile
from app.services import documents


@dataclass
class DocIssue:
    kind: str        # receipt | logo | email_attachment
    entity_id: str
    problem: str     # missing | mismatch | error
    detail: str = ""


@dataclass
class IntegrityReport:
    checked: int = 0
    ok: int = 0
    issues: list[DocIssue] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.issues


async def _check(report: IntegrityReport, kind: str, entity_id: str, prefix: str,
                 org_id: str, sha256: str) -> None:
    report.checked += 1
    try:
        # legacy=None: verify the STORAGE object specifically (no DB-blob fallback).
        data = await documents.load(prefix, org_id, sha256, legacy=None)
    except storage.StorageError:
        report.issues.append(DocIssue(kind, entity_id, "missing", "object not found in storage"))
        return
    except Exception as exc:  # noqa: BLE001 — any backend error is a finding
        report.issues.append(DocIssue(kind, entity_id, "error", f"{type(exc).__name__}: {exc}"))
        return
    if data is None:
        report.issues.append(DocIssue(kind, entity_id, "missing", "no object bytes"))
        return
    actual = storage.sha256_hex(data)
    if actual != sha256:
        report.issues.append(DocIssue(kind, entity_id, "mismatch",
                                      f"expected {sha256[:12]}…, got {actual[:12]}…"))
    else:
        report.ok += 1


async def verify_documents(db: AsyncSession, org_id: str) -> IntegrityReport:
    """Re-hash every storage-backed document reference for one tenant."""
    report = IntegrityReport()

    # Receipts (ExpenseItem → ExpenseReport carries org_id).
    rows = await db.execute(
        select(ExpenseItem.id, ExpenseItem.receipt_sha256)
        .join(ExpenseReport, ExpenseReport.id == ExpenseItem.report_id)
        .where(ExpenseReport.org_id == org_id, ExpenseItem.receipt_sha256.is_not(None))
    )
    for item_id, sha in rows:
        await _check(report, "receipt", item_id, documents.RECEIPTS, org_id, sha)

    # Logos.
    for profile_id, sha in await db.execute(
        select(IssuerProfile.id, IssuerProfile.logo_sha256)
        .where(IssuerProfile.org_id == org_id, IssuerProfile.logo_sha256.is_not(None))
    ):
        await _check(report, "logo", profile_id, documents.LOGOS, org_id, sha)

    # Inbound email attachments (retained rows only — rejected ones store no bytes).
    for row_id, sha in await db.execute(
        select(InboundInvoice.id, InboundInvoice.sha256)
        .where(InboundInvoice.org_id == org_id,
               InboundInvoice.sha256.is_not(None),
               InboundInvoice.status != "rejected")
    ):
        await _check(report, "email_attachment", row_id, documents.EMAIL_ATTACHMENTS, org_id, sha)

    return report
