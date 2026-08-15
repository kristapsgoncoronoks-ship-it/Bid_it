"""The consent gate for deleting a consequential invoice.

Owner decision (docs/design/deletion-and-archive.md): a client MAY delete an
invoice that is past draft or has been paid — it is their record and their call —
but the system must warn them every time that there can be legal consequences,
and must record that they accepted the warning.

Three properties this module exists to guarantee, none of which a browser dialog
can provide:

**The gate is on the server.** A confirm dialog is a UI convenience; the API
refusing without an acknowledgement is the actual control. Anything else means
the gate is absent for every caller that is not this SPA.

**The warning is VERSIONED, and consent is to a version.** The audit trail has to
answer "what exactly did they agree to?" years later, and it cannot do that if
the wording has since been edited. An acknowledgement quoting an older version is
refused: consent given to different words is not consent to these.

**It is never "don't show again".** The owner was explicit. A suppressed warning
is consent given once for decisions not yet made, about invoices that did not
exist at the time.

The consequences listed are only those the schema states with CERTAINTY. A
warning that fires on a guess trains people to dismiss warnings, and this one has
to be read every time.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.invoice import Invoice, WorkflowState

WARNING_VERSION = "2026-08-15"
"""Bump on ANY change to `WARNING_TEXT` or to the consequence sentences. Old
acknowledgements then stop being accepted, which is the point — the alternative
is an audit trail claiming someone agreed to words they never saw."""

WARNING_TEXT = (
    "Deleting this invoice removes a financial record from your books. "
    "Depending on where you are established, keeping source documents for a "
    "statutory period may be a legal obligation, and removing one can affect a "
    "tax return, a VAT refund claim or an audit that relies on it. "
    "Only you can decide whether to delete it. "
    "It will be recoverable from your deleted items for 30 days; after that it "
    "leaves your workspace. Your confirmation is recorded."
)
"""Deliberately does NOT say "permanently deleted": the platform retains the
record after the bin expires, and telling a client something is gone when it is
not is a larger exposure than the retention itself."""


@dataclass(frozen=True)
class Consequence:
    """One specific, schema-certain reason this deletion is consequential.

    `code` is stable and machine-readable so a client can branch on it; `message`
    is what the person reads.
    """

    code: str
    message: str


def consequences_for(inv: Invoice) -> list[Consequence]:
    """Why deleting THIS invoice is consequential — empty for a clean draft.

    The single source both paths read: the one-at-a-time delete turns a non-empty
    list into a warning to be accepted, and the bulk delete turns it into a
    refusal (see `invoices.deletion_refusal`). One function, two policies, so the
    two can state different rules without disagreeing about the facts.
    """
    out: list[Consequence] = []
    if inv.workflow_state is not WorkflowState.draft:
        out.append(
            Consequence(
                "not_draft",
                f"It is {inv.workflow_state.value}, not a draft — deleting it removes "
                "the record of a decision your organisation has already made.",
            )
        )
    if inv.amount_paid and inv.amount_paid > 0:
        out.append(
            Consequence(
                "payment_recorded",
                f"A payment of {inv.amount_paid} {inv.currency or ''}".strip()
                + " is recorded against it. The payment stays in your ledger with "
                "nothing left to explain it.",
            )
        )
    if inv.payment_run_id:
        out.append(
            Consequence(
                "in_payment_run",
                "It is included in a payment run, which will no longer reconcile "
                "against the invoices it was built from.",
            )
        )
    return out


def requires_consent(inv: Invoice) -> bool:
    return bool(consequences_for(inv))


def warning_for(inv: Invoice) -> dict:
    """The warning payload — version, text and this invoice's specific reasons.

    Returned to the client in the 409 that refuses an unacknowledged delete, so
    the words shown on screen are always the server's current ones. A client that
    composed its own warning would drift from what the audit trail claims was
    accepted.
    """
    return {
        "version": WARNING_VERSION,
        "text": WARNING_TEXT,
        "consequences": [{"code": c.code, "message": c.message} for c in consequences_for(inv)],
    }


def audit_record(inv: Invoice) -> dict:
    """What goes into the audit event when a warned deletion is confirmed.

    Stores the FULL text, not a hash or a version alone. "The client confirmed
    they read our warning" is only worth something if the record says what the
    warning said — and a deletion is rare enough that the bytes do not matter.
    """
    return {
        "warning_version": WARNING_VERSION,
        "warning_text": WARNING_TEXT,
        "consequences": [c.code for c in consequences_for(inv)],
    }
