# ADR-0025 — Vendor bank-detail control: protected-field changes are a workflow, not a write

**Status:** Accepted — implemented (WO-2, board A2.1–A2.3). Builds on ADR-0024.

## Context
`Vendor.iban`/`Vendor.bic` become the creditor account of a real ISO 20022
pain.001 credit transfer (`services/sepa.py::build_pain001`). Before this change
`POST /vendors` and `PATCH /vendors/{id}` had no permission check, no audit, no
version guard and no IBAN validation — any authenticated member of any tenant
could redirect a supplier payment (risk S-1, scored 9/9). WO-1 fixed the
*permission* class structurally; this ADR fixes the *bank-detail* class.

## Decision
1. **Protected fields are a workflow, not a write.** On an existing vendor,
   `iban` and `tax_id` are never mutated by PATCH. The change lands in
   `vendor_change_requests` (`pending → approved | rejected`) and only a
   **different** user holding `SETTINGS_MANAGE` may apply it
   (`maker_is_checker` → 403, invariant §4.8). Rationale: one compromised
   account must not be able to both plant and activate a new payee account.
   (Vendor has no registration-number column today; when one is added it joins
   `PROTECTED_FIELDS` in `services/vendors.py`.)
2. **First capture ≠ change.** Setting a protected field whose stored value is
   EMPTY applies directly (audited): there is no established payment route to
   redirect, and the existing capture flows depend on it. A vendor **created**
   already carrying an iban/tax_id lands `status="provisional"` instead — a
   payment run refuses it unless the maker passes `confirm_provisional`.
3. **Response shape: 200 with a `pending_changes` block, not 202.** A PATCH may
   mix protected and non-protected fields; the non-protected ones apply
   immediately, so the caller needs the resulting row. The response is always
   the vendor **as stored** — the pending value appears only inside
   `pending_changes` (each entry explicitly `status="pending"`), never in the
   vendor fields. A 202 with no body would hide the applied part of the patch
   and complicate every client; the chosen shape keeps `VendorOut` backward
   compatible (new fields have defaults). Documented on `schemas/vendor.py`.
4. **Validation is layered and fail-closed.** `core/bank_id.py` (pure: ISO
   13616 length table + ISO 7064 MOD-97, BIC ISO 9362; an unknown country
   prefix is rejected, never waved through) runs at every write path — vendor
   create/update, change-request creation AND approval, employee bank details,
   issuer profile — and AGAIN inside `build_pain001`, because write-time checks
   can be bypassed by a migration or a direct DB edit and the file is the last
   line of defence: an invalid creditor aborts the build; no XML is produced.
5. **Payment runs refuse contested vendors.** A run cannot be created OR paid
   while a linked vendor has a pending protected-field request (checked at both
   points — a request can be filed between create and pay); the refusal names
   the vendor. Provisional vendors are refused at creation unless explicitly
   confirmed.
6. **Audit privacy.** Every mutation audits old→new in the same transaction,
   but an IBAN enters audit meta only masked (`…last4 (len N)`), never full —
   audit rows are immutable (hash-chained) and broadly readable, so a leaked
   full IBAN could never be redacted. The change-request row itself keeps the
   full values (the approver must verify them against the source document);
   the SPA masks them for display, cosmetically.
7. **Optimistic concurrency.** `vendors.version` mirrors `Invoice.version`:
   client sends the version it read, mismatch → 409 `stale_version`, `None`
   opts out (same contract as `invoice_workflow.assert_version`).
8. **Tenancy.** `vendor_change_requests` is tenant-scoped: composite FK
   `(org_id, vendor_id) → vendors(org_id, id)`, registered in `TENANT_MODELS`,
   RLS policy shipped in the same migration (`c2d4f6a8b0e3`). At most one open
   request per `(vendor, field)` via a partial unique index + a service check.

## Rollback
Code reverts cleanly. The migration is additive; its downgrade is real and
tested — but it **refuses** while any request is `pending` (neither silently
applying nor dropping an in-flight approval decision is acceptable; an operator
must decide each one first).

## Revisit when
Employee/issuer IBANs get their own second-approver flow (today: format
validation only, per WO-2 scope); vendor dedup beyond exact name match (A2.4);
advisory VIES validation of `tax_id` (never inline).
