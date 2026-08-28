"""The adjustable submission checklist (G2.10; `BA_fleet_fuel.md` §3.E,
R45). See `app/models/transport/checklist_rule.py`'s module docstring for
why rules are DATA, not code.

SLICE 2 (WO-AB) SEEDS THE REMAINING FOUR HARVESTED RULES
---------------------------------------------------------
WO-60 seeded two of the six (`customer_data`, `bank_account`) and said
exactly why: the other four are `check_type="document"` (needing a
document-requirements-with-EXPIRY concept that did not exist) or needed a
`nace_code` column that did not exist. Both now do —
`app.services.transport.claimant_documents` and `IssuerProfile.nace_code`
— so `contract`, `nace`, `trade_register` and `power_of_attorney` are
seeded, and §3.E's table is harvested WHOLE rather than left as a
four-line deferral that outlives its own condition.

Adding four rules makes claims that were `1B` read `1A` until the
documents are on file. That is the harvested behaviour, stated verbatim in
§3.E ("an expired PoA fails `_has_doc` and the claim drops back to 1A"),
and it changes no GATE: `derive_stage` is a read-only PREVIEW and
`lock.submit_claim` has never consulted this evaluator. An org that does
not want a rule deactivates it — which is the whole point of rules being
data (R45's own acceptance test).

`submission_checklist` is the ONE evaluator `status.derive_stage` (G2.7)
now consults, replacing WO-59's own two-check proxy exactly as that
order's docstring anticipated. It combines:

1. Every ACTIVE `scope="customer"` adjustable rule, evaluated against the
   claim's claimant entity (`entity_id` -> `issuer_profiles`, read via
   `app.services.issuer.get_by_id` — never a raw cross-domain model join,
   ADR-P3 rule 2).
2. The FOUR claim-level items `BA_fleet_fuel.md` §3.E names verbatim:
   receipt control, unresolved refs, documents attached, period ended —
   each reusing an EXISTING pure check (R3/R10/R15/R7), never re-derived.

WHY THE RECEIPT-CONTROL / UNRESOLVED-REFS SPLIT RE-QUERIES
`fuel_transactions` RATHER THAN READING `vat_claim_lines`
------------------------------------------------------------------------
A materialized claim LINE groups every unresolved transaction under the
single literal ref `"UNMATCHED"` (`claim_lines.build_claim_lines`) — it
does not retain WHICH supplier(s) produced it. Naming "the missing
supplier" (BA's own item-1 wording) therefore needs the underlying
`FuelTransaction` rows, not the collapsed line. `_unresolved_suppliers`
reuses `claim_lines.period_months` (already public, shared with
`close.run_close`) and `invoice_match.resolve_invoice_ref`/
`registered_invoices` (the SAME resolution + waivability functions
`build_claim_lines`/`waiver.set_waiver` already use) — a duplicated SELECT
of `fuel_transactions` is unavoidable given the schema, but there is still
only ONE resolution/waivability implementation.

WHY AN UNSUPPORTED RULE STILL FAILS CLOSED
--------------------------------------------
Both scopes (`customer`, `country`) and both check types (`data`,
`document`) are evaluable as of slice 2, so the two raises no longer guard
anything `seed_default_rules` can write. They stay anyway, and not as
dead code: `reference` is a free-text column, so a rule naming a
`DATA_VERIFIERS` key or a document kind that does not exist is still
reachable the moment anything but `seed_default_rules` inserts a row.
Silently treating such a rule as "passed" would be a forfeiture of a real
check — the same discipline `is_synthetic()`'s docstring states — so
`submission_checklist` raises (`unsupported_check_type` /
`unsupported_scope`) rather than guess.

WHY THE PoA ITEM NAMES ITS AUTHORITY
--------------------------------------
`tax_authority.authority_for` puts the national authority's own name in
the country-scope item's reason, because "Power of attorney: no document
on file" does not tell an operator who the instrument has to be addressed
to, and §3.F F5 already carries the mapping as data for exactly that
reason. An unknown country contributes NOTHING to the sentence rather
than a guess — F5's own rule.

WHAT SLICE 2 DELIBERATELY DOES NOT BUILD
------------------------------------------
§3.E's `_open_poa_request_note` enriches a PoA item's LABEL with an open
document REQUEST's status ("sent for signature"). There is no document-
request workflow in this codebase (§3.F F4's `DOC_REQUEST_STATES`, zero
occurrences) — so that note has no subject, and inventing a status string
with nothing behind it would be worse than its absence. It lands with F4
or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.checklist_rule import VatChecklistRule
from app.models.transport.claimant_document import DOC_KINDS
from app.models.transport.vat_claim import VatRefundClaim
from app.services import audit, issuer, modules
from app.services.transport import claimant_documents, queries, tax_authority
from app.services.transport.claim_lines import period_months
from app.services.transport.deadline import period_ended
from app.services.transport.document_gate import missing_document_invoice_ids
from app.services.transport.invoice_match import registered_invoices, resolve_invoice_ref
from app.services.transport.waiver import waived_suppliers

# (key, label, scope, check_type, reference, sort) — §3.E's table, all six
# rows, in its own order. `reference` is a `DATA_VERIFIERS` key for a data
# check and a `DOC_KINDS` value for a document one.
DEFAULT_RULES: tuple[tuple[str, str, str, str, str | None, int], ...] = (
    ("contract", "Contract", "customer", "document", "signed_contract", 5),
    ("customer_data", "Customer data", "customer", "data", "customer_data", 10),
    ("bank_account", "Bank account", "customer", "data", "bank_account", 20),
    ("nace", "NACE business activity", "customer", "data", "nace", 30),
    (
        "trade_register",
        "Trade register / company register form",
        "customer",
        "document",
        "trade_registry",
        40,
    ),
    ("power_of_attorney", "Power of attorney", "country", "document", "power_of_attorney", 50),
)


def _field_ok(value: str | None) -> bool:
    """This codebase's translation of Fleet Fuel's `_field_ok` "INPUT"
    placeholder convention (which does not exist here — onboarding leaves a
    field genuinely NULL/blank rather than writing a sentinel string): a
    field is "ok" iff it is not `None` and non-blank after stripping."""
    return bool(value and value.strip())


def _verify_customer_data(fields: dict[str, str | None]) -> tuple[bool, str | None]:
    missing = [
        label
        for label, key in (
            ("registration number", "registration_number"),
            ("VAT number", "vat_number"),
            ("address", "address_line1"),
        )
        if not _field_ok(fields.get(key))
    ]
    if missing:
        return False, f"Missing: {', '.join(missing)}"
    return True, None


def _verify_bank_account(fields: dict[str, str | None]) -> tuple[bool, str | None]:
    if not _field_ok(fields.get("iban")):
        return False, "No IBAN on file"
    return True, None


def _verify_nace(fields: dict[str, str | None]) -> tuple[bool, str | None]:
    """§3.E, verbatim: *"NACE is required because Art. 11 requires the
    business-activity description via harmonised NACE codes."* A PRESENCE
    check and nothing more — national derivatives of NACE differ in shape
    (`49.41`, `H49.41`, PKD `49.41.Z`), so a format gate here would refuse
    valid codes and call it a data-quality failure."""
    if not _field_ok(fields.get("nace_code")):
        return False, "No NACE business-activity code on file"
    return True, None


# key -> verifier(fields) -> (ok, reason). `fields` carries plain strings
# extracted from the claimant `IssuerProfile` — never the ORM row itself
# (mirrors `invoice_match.MatchedInvoice`'s own "never name a foreign
# domain's model" discipline, ADR-P3 rule 2 / the boundary test).
DATA_VERIFIERS = {
    "customer_data": _verify_customer_data,
    "bank_account": _verify_bank_account,
    "nace": _verify_nace,
}


def _with_authority(reason: str | None, country: str | None) -> str | None:
    """Name the authority a country-scope document is addressed to, when the
    map knows it. F5's rule holds: an unknown country adds NOTHING rather than
    a plausible-looking guess, so the sentence simply stays as it was."""
    name = tax_authority.authority_for(country)
    if not name or reason is None:
        return reason
    return f"{reason} — {country} files with {name}"


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    label: str
    scope: str  # "customer" | "country" (per refund country) | "claim"
    ok: bool
    reason: str | None = None


async def _get_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatRefundClaim:
    """Org-scoped lookup — a cross-tenant claim id is indistinguishable from
    an unknown one (master-context §4.4: opaque 404, never 403)."""
    claim = await db.scalar(
        select(VatRefundClaim).where(VatRefundClaim.id == claim_id, VatRefundClaim.org_id == org_id)
    )
    if claim is None:
        raise NotFoundError("Claim not found", code="claim_not_found")
    return claim


async def list_rules(
    db: AsyncSession, org_id: str, *, active_only: bool = False
) -> list[VatChecklistRule]:
    """Module-gated since WO-77, when it became ROUTE-FACING; before that
    its only callers (`seed_default_rules`/`submission_checklist`) gate
    first, so for them the check is a redundant no-op and the change is
    behaviour-preserving. The WO-49 convention: no transport service entry
    point trusts its caller to have checked the entitlement — fails CLOSED
    (ADR-P3 rule 3)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    stmt = select(VatChecklistRule).where(VatChecklistRule.org_id == org_id)
    if active_only:
        stmt = stmt.where(VatChecklistRule.active.is_(True))
    stmt = stmt.order_by(VatChecklistRule.sort, VatChecklistRule.key)
    return list(await db.scalars(stmt))


async def seed_default_rules(db: AsyncSession, org_id: str) -> list[VatChecklistRule]:
    """Idempotent per org: inserts any `DEFAULT_RULES` row not already
    present (matched on `key`), returns only the NEWLY created rows. A
    repeat call with nothing left to insert creates nothing and audits
    nothing (a true no-op, mirroring `waiver.set_waiver`'s own idempotency
    convention)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    existing = {r.key for r in await list_rules(db, org_id)}
    created: list[VatChecklistRule] = []
    for key, label, scope, check_type, reference, sort in DEFAULT_RULES:
        if key in existing:
            continue
        row = VatChecklistRule(
            org_id=org_id,
            key=key,
            label=label,
            scope=scope,
            check_type=check_type,
            reference=reference,
            active=True,
            sort=sort,
        )
        db.add(row)
        created.append(row)

    if created:
        await db.flush()
        for row in created:
            await audit.record(
                db,
                audit.A.TRANSPORT_CHECKLIST_RULE_SET_ACTIVE,
                target_type="vat_checklist_rule",
                target_id=row.id,
                meta={"key": row.key, "seeded": True, "active": True},
                org_id=org_id,  # explicit — callable outside an HTTP request context.
            )
    return created


async def set_active(db: AsyncSession, org_id: str, key: str, active: bool) -> VatChecklistRule:
    """The ONLY writer of `VatChecklistRule.active` — R45's own acceptance
    test: "deactivate a rule ⇒ it disappears from the gate." A no-op call
    (the value is already what was requested) audits nothing."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    rule = await db.scalar(
        select(VatChecklistRule).where(
            VatChecklistRule.org_id == org_id, VatChecklistRule.key == key
        )
    )
    if rule is None:
        raise NotFoundError(f"Checklist rule '{key}' not found", code="checklist_rule_not_found")

    old = rule.active
    rule.active = active
    await db.flush()
    if old != active:
        await audit.record(
            db,
            audit.A.TRANSPORT_CHECKLIST_RULE_SET_ACTIVE,
            target_type="vat_checklist_rule",
            target_id=rule.id,
            meta={"key": key, "old_active": old, "new_active": active},
            org_id=org_id,  # explicit — callable outside an HTTP request context.
        )
    return rule


async def _unresolved_suppliers(
    db: AsyncSession, org_id: str, claim: VatRefundClaim
) -> dict[str, bool]:
    """`{supplier: waivable}` for every DISTINCT, un-waived supplier with
    >=1 currently unresolved (synthetic) transaction in the claim's scope.
    A supplier is `waivable` iff it has NO registered invoice at all for
    the claim's refund country (R15's own definition, via `invoice_match.
    registered_invoices` — never re-derived). An ALREADY-waived supplier is
    excluded entirely here too (its transactions never became a claim line
    at all, WO-58) — this function only ever surfaces suppliers still
    genuinely blocking something."""
    months = period_months(claim.ref_period)
    txns = list(
        await db.scalars(
            queries.claim_scope_transactions(
                org_id,
                entity_id=claim.entity_id,
                refund_country=claim.refund_country,
                months=months,
            )
        )
    )
    waived = await waived_suppliers(db, org_id, claim.id)

    result: dict[str, bool] = {}
    for txn in txns:
        if txn.supplier in waived or txn.supplier in result:
            continue
        matched = await resolve_invoice_ref(
            db,
            org_id,
            entity_id=claim.entity_id,
            supplier=txn.supplier,
            refund_country=claim.refund_country,
            invoice_ref=txn.invoice_ref,
        )
        if matched is not None:
            continue
        candidates = await registered_invoices(
            db, org_id, supplier=txn.supplier, refund_country=claim.refund_country
        )
        result[txn.supplier] = not candidates
    return result


async def submission_checklist(
    db: AsyncSession, org_id: str, claim_id: str, *, today: date | None = None
) -> list[ChecklistItem]:
    """The evaluator: every active customer-scope rule + the four claim-
    level items, in one list. Auto-seeds `DEFAULT_RULES` first (idempotent,
    cheap) so a checklist always evaluates against at least the defaults
    without a separate setup step."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)
    await seed_default_rules(db, org_id)

    entity = await issuer.get_by_id(db, org_id, claim.entity_id)
    fields: dict[str, str | None] = {
        "registration_number": entity.registration_number if entity else None,
        "vat_number": entity.vat_number if entity else None,
        "address_line1": entity.address_line1 if entity else None,
        "iban": entity.iban if entity else None,
        "nace_code": entity.nace_code if entity else None,
    }

    items: list[ChecklistItem] = []
    for rule in await list_rules(db, org_id, active_only=True):
        if rule.scope not in ("customer", "country"):
            raise ValidationError(
                f"Rule '{rule.key}' has unsupported scope '{rule.scope}'", code="unsupported_scope"
            )
        # A country-scope rule is evaluated against THIS claim's refund
        # country — the claim is the only thing that names one, which is why
        # the rule row itself carries no country (a rule is a rule, not a fact
        # about one filing; `checklist_rule.py`'s own "rule, not fact" note).
        country = claim.refund_country if rule.scope == "country" else None

        if rule.check_type == "data":
            verifier = DATA_VERIFIERS.get(rule.reference or "")
            if verifier is None:
                raise ValidationError(
                    f"No verifier registered for rule '{rule.key}'", code="unsupported_check_type"
                )
            ok, reason = verifier(fields)
        elif rule.check_type == "document":
            if (rule.reference or "") not in DOC_KINDS:
                raise ValidationError(
                    f"Rule '{rule.key}' names no known document kind ('{rule.reference}')",
                    code="unsupported_check_type",
                )
            state = await claimant_documents.has_valid(
                db,
                org_id,
                claim.entity_id,
                kind=rule.reference or "",
                country=country,
                today=today,
            )
            ok, reason = state.ok, state.reason
            if not ok and rule.scope == "country":
                reason = _with_authority(reason, country)
        else:
            raise ValidationError(
                f"Rule '{rule.key}' has unsupported check_type '{rule.check_type}'",
                code="unsupported_check_type",
            )

        items.append(
            ChecklistItem(key=rule.key, label=rule.label, scope=rule.scope, ok=ok, reason=reason)
        )

    unresolved = await _unresolved_suppliers(db, org_id, claim)
    missing_suppliers = sorted(s for s, waivable in unresolved.items() if waivable)
    items.append(
        ChecklistItem(
            key="receipt_control",
            label="Receipt control: required invoices received",
            scope="claim",
            ok=not missing_suppliers,
            reason=f"Missing invoices from: {', '.join(missing_suppliers)}"
            if missing_suppliers
            else None,
        )
    )
    blocking_suppliers = sorted(s for s, waivable in unresolved.items() if not waivable)
    items.append(
        ChecklistItem(
            key="unresolved_refs",
            label="All invoice refs resolved (no INPUT/aggregate placeholders)",
            scope="claim",
            ok=not blocking_suppliers,
            reason=f"Note-matching needed for: {', '.join(blocking_suppliers)}"
            if blocking_suppliers
            else None,
        )
    )

    missing_docs = await missing_document_invoice_ids(db, org_id, claim_id)
    items.append(
        ChecklistItem(
            key="documents_attached",
            label="All invoice documents attached",
            scope="claim",
            ok=not missing_docs,
            reason=f"{len(missing_docs)} invoice(s) missing a document" if missing_docs else None,
        )
    )

    ended = period_ended(claim.ref_period, today=today)
    items.append(
        ChecklistItem(
            key="period_ended",
            label="Claim period ended",
            scope="claim",
            ok=ended,
            reason=None if ended else "The claim period has not ended yet",
        )
    )

    return items
