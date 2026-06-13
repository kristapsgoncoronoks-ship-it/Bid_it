# AI review assistant — advisory validation & analytics

A post-extraction assistant that helps a reviewer sanity-check an invoice **without ever
sending the source document anywhere**. It is **off by default**, **advisory only**, and
**never touches a figure, a status, or the commit gate**. Module: `ai_review.py`; surfaced
on the draft-review screen.

## The principle
Extraction and reasoning are different jobs with different risk. Capture stays
**deterministic** (e-invoice XML, Factur-X/ZUGFeRD embedded XML, the parser registry — see
`extract.py`). AI moves entirely to the **post-extraction** side, where it reasons about
data that is already structured and checkable:

```
deterministic extraction → deterministic hard-validation → AI advisory flags + analytics → human sign-off
```

## What is sent to AI — and what is NOT
The payload is **minimized derived data**, built by `ai_review.build_payload()`:

**Sent** (a fixed, allow-listed field set):
- the extracted invoice fields and, per line, `{invoice_no, date, country, currency, net, vat}`;
- supplier context — expected VAT, name/aliases, and the **contract price terms**
  (`expected_discount_eur_l` / `price_ceiling_eur_l`, NET EUR/L, from `supplier_discounts`,
  keyed exactly as `contract_audit.py`);
- customer **identity only** (name);
- a recent **price range** from history;
- the expenditure codes and reconciling-transaction summaries;
- `deterministic_findings` — the output of `validate.validate_batch` (so the model is told
  what was already checked and must not redo it).

**Never sent:**
- ❌ the **PDF / scan / document image** — `_pdf_bytes` and **any** key starting `_` are
  dropped unconditionally (recursively);
- ❌ **bank / secret / personal** fields — every key in `REDACT_FIELDS`
  (`iban, swift, bic, bank, account, account_number, beneficiary, password, secret,
  api_key, contact_name, contact_email, phone`) is stripped recursively; a field is sent
  only when a caller passes an explicit `needs=(...)` allow-list for a specific check;
- ❌ the agency **service-fee** terms (`fee_pct`/`fee_min`) — irrelevant to invoice
  validation;
- ❌ anything from `vat_claims.db` on the draft screen.

The original PDF stays in the document vault, on the server.

## What AI returns
Structured JSON only: `{"flags": [{field, severity (info|warn|error), message, suggestion?}],
"note": str|null}`. Flags are the **fuzzy** layer the deterministic rules can't codify —
supplier alias/name mismatch, VAT-id plausibility, price-vs-history anomaly, "this looks
wrong." `note` is a short analytics narrative over the exact figures. The model is
instructed **not** to recompute VAT arithmetic, totals, thresholds, or expenditure codes.

## Hard checks stay deterministic (AI never owns them)
VAT arithmetic (`net × rate = vat`), totals/coversheet reconciliation, the 2008/9/EC
thresholds, Art. 9 expenditure codes, doc-presence, synthetic-line gates — all remain in
`validate.py` and the `vat_refund`/`invoice_control` gates. The AI result feeds **no** gate:
`/extract/confirm`, `validate_batch`, and `can_commit` are unchanged. AI flags are shown to
a human; accept/reject is the human's decision.

## Configuration & privacy
- **Setting `ai_review_backend`** (Admin panel) — `none` (default, OFF) / `claude` / `openai`
  / `azure`. When `none`, `review()` returns the deterministic block and makes **zero**
  network calls. Reuses the extractor's existing API keys/env; Azure stays in-tenant.
- **Audit/provenance** — each non-`none` run archives the **sent field-keys** (not values)
  plus the response via `data_lake.put(kind="ai_review")`; the panel shows the backend,
  model, and "advisory only — nothing was changed."

## Surfaces & scope
v1 is on the **draft-review screen** (`_review_form`, route `/extract/ai-review`,
capability `data_import`, module `intake`). Registered-invoice / VAT-claim review surfaces
are a deferred follow-up (those are admin-only VAT surfaces). Every rendered value is
`esc()`-escaped; any interactivity is CSP-clean from `/app.js`.
