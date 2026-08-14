# Architecture review

Judged against this repository's own stated rules (`CLAUDE.md`, `docs/architecture/`), quoted
where a rule is invoked. No graph tooling was available; structure was established by import
tracing and reading the routers end to end.

## Layering — service/route boundary (I-22)

The rule: *services raise `AppError`, never `HTTPException`; routes hold no business logic.*

| Module | Verdict |
|---|---|
| `capture_failures.py` | **Compliant.** Raises nothing; signals "not found" by returning `None` and lets the route render the opaque 404. No FastAPI import. |
| `inbound_health.py` | **Compliant.** No FastAPI import; returns dataclasses. |
| `vendor_resolution.py` | **Compliant.** Pure function over a session. |
| `extraction.clear_fields_for_retry` | **Compliant.** Raises `ConflictError` (an `AppError`), which is what carries the machine-readable `capture_has_review` code the SPA branches on. |

The routes are thin: they validate, call one service, map to a schema, and audit. The one
place a route holds logic is `_resolve_vendor` in `invoices.py`, which now orders
resolve-then-create-then-audit. That ordering is load-bearing (F-05 in the change-impact
report explains why resolving after the create would produce a false audit trail) and is
covered by a dedicated test, but it is business sequencing sitting in a route. **Minor
architectural debt, recorded, not fixed** — moving it into a service is a sensible follow-up.

## Coupling and dependency direction

`extraction_provider.py` now imports `capture_failures` at module level. I verified
empirically that **no import cycle exists**: `capture_failures` imports only models
(`extraction_run`, `email_intake`, `capture_acknowledgement`) plus SQLAlchemy, and the module
imports cleanly in isolation.

It is nonetheless a slightly awkward direction: a *provider* (low-level, parse-time) now
depends on a *worklist service* (higher-level, presentation-adjacent) purely to obtain an
exception class and four string constants. **Recommendation:** move `CaptureError` and the
code constants to `app/core/` — the repository already uses `app/core/` for exactly this kind
of shared vocabulary (`app/core/errors.py`, `app/core/dimensions.py`). Low priority; no
functional consequence.

## Duplication

The repo rule against forking a query definition is respected. `capture_failures` deliberately
does **not** re-implement `extraction.pending_review_filters` (which is the *parsed* queue);
it defines a genuinely different predicate (`status == "failed"`, plus the inbound table).

However, `pending_review_filters` carries a docstring stating it is *"THE definition of a
capture pending human review… two hand-written copies of the filter would be exactly the drift
ADR-0023's projection rule forbids."* By that principle the new **failed** filter should
arguably live beside it as a named, single-source definition rather than inline inside
`worklist()`. It is currently used in exactly one place, so there is no drift yet.
**Recorded as a consistency observation, not a violation.**

## Sibling consistency

The two inbound webhook handlers diverged in how they scoped tenancy and recorded health —
found as **F-03** and fixed. They are now symmetric. This was the most valuable architectural
finding in the review: not because it was dangerous, but because asymmetric siblings are how
the wrong pattern propagates.

## Advisory-never-binding (§4.19) and additive (§4.20)

Both upheld, and both are the explicit design centre of the change set:

- `vendor_resolution` **abstains** rather than deciding, and writes nothing.
- `capture_failures` acknowledgements change no capture state.
- `inbound_health` gates nothing; it reports.
- Every schema change is additive (2 nullable columns, 2 new tables, 0 removals).

The P0 fix is the sharpest expression of the same principle: a machine action may not destroy
human work without being asked.

## Models and migrations

Both new tables follow the established shape (GUID PK, `org_id` FK with `ON DELETE CASCADE`,
`TimestampMixin`, registered in `TENANT_MODELS`, RLS in the creating migration, real parity
probe in the same commit).

**Index coverage checked against the actual queries:**

- `capture_acknowledgements`: `ix_capture_acks_org_ref (org_id, channel, ref_id)`. The
  service queries `org_id = ? AND ref_id IN (...)` — leading column matches, `ref_id` is the
  third column so the index is used partially. Acceptable; a `(org_id, ref_id)` index would
  serve the actual query better.
- `inbound_channel_health`: `uq (org_id, channel)` exactly matches both access patterns.

## Verdict

**The change set leaves the codebase more coherent than it found it.** It introduces three
closed vocabularies where there was previously free-form prose or silence, it documents its
own rejected alternatives, and it does not fork an existing query definition. Its guarantees
are stated as invariants and proven by seeded violation rather than asserted in comments.

The debts it adds are small and recorded: one awkward import direction, one piece of ordering
logic in a route, and one filter definition that could be named alongside its sibling. None
of them compounds.
