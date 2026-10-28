# Development PostgreSQL bootstrap — 28 October 2026

**DB-PG-BOOTSTRAP-01: implementation candidate; human review and delivered-head
CI remain required.** Parent: integration PR #94 at
`fc3a85bfacd2d74272051c632992c1494a2d7818`, migration head `f0b2d4e6a8c1`.
This is a separate follow-up to DB-PG-PARITY-01 and the uploaded alternative patch.

## Problem and result

Direct non-production startup calls `create_all` for developer convenience.
Portable composite `ON DELETE SET NULL` attempts to clear both the optional
reference and non-null `org_id` on PostgreSQL. Native controls reproduce
SQLSTATE `23502` on each of the four child relationships. Migration
`c4d6e8f0a2b4` already correctly limits deletion to the optional reference.
Docker development and production run Alembic first; this is not evidence of
missing production migrations or broken migrated constraints.

| Existing relationship | Column permitted to clear |
| --- | --- |
| `expense_items.fk_expense_items_project` | `project_id` |
| `invoices.fk_invoices_project` | `project_id` |
| `issued_invoices.fk_issued_invoices_project` | `project_id` |
| `issued_invoices.fk_issued_invoices_issuer` | `issuer_id` |

`app/core/development_schema.py` reuses the existing exact policy inventory.
Before copying, it checks each original constraint, column order, referenced
table/columns/schema, non-null tenant and nullable reference. It also requires
the original constraint and elements to agree on `ondelete`, `onupdate`,
`deferrable`, `initially`, `match`, `use_alter` and `link_to_name`. This prevents
`Table.to_metadata` from hiding contradictory original element options, the gap
reproduced in the supplied alternative patch.

The PostgreSQL copy changes only the four constraint/element delete actions.
The full remaining metadata, column order, indexes, schema and naming convention
are preserved. SQLite receives the original metadata. Non-production lifespan
uses this selection inside its existing transaction. The production branch,
SQLAlchemy models, Alembic environment, existing comparison helper, dependencies
and all 155 migration files are unchanged.

## Scope and alternatives

Running Alembic as an implicit lifespan migration was considered. Existing
deployment and Docker paths already run it explicitly; adding an application
migration runner would expand ownership and startup failure behavior. A small
development-only metadata selection keeps the existing direct-start convenience
and production migration boundary. No framework or executor is added.

The adversarial objection is that copying can hide source-policy inconsistencies
or silently change unrelated DDL. Original-policy validation, clone-first
negative controls, all-table/index compilation and native existing-schema
preservation address that boundary. Independent AI-assisted review is recorded;
it is not Lead or human acceptance.

**`create_all` does not repair an existing generic foreign key.** Repeated
bootstrap on new-correct and old-generic fixture databases issues only inspection
SELECTs, retaining all rows and catalog definitions. The comparison helper
continues to refuse the old generic action. Any existing schema requiring a
repair needs a separately reviewed migration with preflight and rollback.

Model-created schemas also lack migration-only RLS policies and version tracking.
Native bootstrap tests explicitly assert both tenants remain visible and no
policies/version marker exist. They demonstrate composite foreign-key integrity
and deletion behavior; migrated tenant-selected RLS remains covered separately
by the unchanged parity contract. Bootstrap is not a production security setup.

## Executed evidence

| Cohort | Result and boundary |
| --- | --- |
| Portable metadata and actual SQLite/production lifespan | 26 passed; concurrent dialects, complete table/index DDL, original-policy refusal, schema/naming and no production create/project |
| Native PostgreSQL bootstrap | 14 passed, zero skips; four catalog, four new deletion, four legacy failure and two preservation/idempotence cases |
| Existing migrated PostgreSQL parity | 15 passed, zero skips; includes the unchanged migration/comparison path at `f0b2d4e6a8c1` |
| Existing migration, registration, boundary, tenant and docs guards | 65 passed; includes SQLite migration compatibility, not a complete backend run |
| Application typing and source style | mypy 436 app files; Ruff/format checks pass |
| Workflow/security checks | Pinned actionlint and online zizmor pass; PII tree scan clean with its existing empty-denylist notice retained |
| Collection | 5,243 backend cases; collection is not execution |

The four new deletion cases use synthetic two-tenant families with nonzero
Decimal financial fields and independent linked masters/control rows. Full child
rows retain tenant IDs, values and other references; only the intended link
clears. Cross-tenant assignments fail with actual SQLSTATE `23503` and the exact
constraint name. Transaction rollback and final catalog/row snapshots are checked.
Disposable UUID databases are explicitly created and dropped under a separate
admin URL, while writes use a non-superuser/no-BYPASSRLS application role.

The generic-projection mutant fails the four-action DDL regression; a clone-before-
validation mutant fails all ten original-element policy cases. These controls
fail without changing product sources. The four legacy native controls are
included in the 14-case cohort, not added as separate unique coverage.

Two initial test-bookkeeping failures are retained in detailed artifacts:
SQLAlchemy reordered unordered constraint clauses during copying, so compilation
snapshots compare complete canonical DDL lines while pinning column order;
PostgreSQL's internal `char` catalog field arrived as bytes, corrected by an
explicit SQL `::text` cast with the same exact action assertion. Neither required
a product behavior change, skipped check or relaxed constraint.

See the [source-bound machine receipt](../security/evidence/DEVELOPMENT-POSTGRES-BOOTSTRAP-2026-10-28.json).
Sanitized detailed evidence is retained under
`/workspace/artifacts/development-postgres-bootstrap-2026-10-28/`.
No frontend or full backend/image run is claimed locally. Preceding integration
CI is a baseline, not delivered-head qualification for this change.

## Maintenance prompt and remaining queue

> Keep direct non-production PostgreSQL bootstrap consistent with the four
> committed tenant-safe optional links. Validate original constraint and every
> element option before copying. Preserve complete unrelated DDL and portable
> SQLite metadata; never run development create_all in production. Prove native
> deletion/full-row/tenant-key preservation, cross-tenant FK refusal, old generic
> failure and idempotent non-repair, plus concurrent dialect use and actual
> lifespan compatibility. Keep existing comparison/env/history byte-identical.
> Do not infer migrated RLS from bootstrap or normalize real installed drift.
> Bind tests to the source/head, retain negative controls and execute the native
> module in CI without silent skips. Repair existing schemas only in a separate
> preflighted migration. No main merge or deployment is part of this change.

Optional migrated-catalog/wrong-action breadth DB-PG-CONTRACT-02 remains open.
So do UI-PLANNING-FOCUS-01, the retained finance queue, owner governance decisions
and release/acceptance controls. No production data or provider service is used.

## Rollback and review

Revert the startup selection and development helper/tests/CI step to roll back
code. No existing data or schema needs rollback: no migration or repair is
performed. Correctly created development constraints remain correct; code
rollback would restore generic DDL only for subsequently created tables.

AI-assisted: implementation, tests, negative controls, documentation and separate
adversarial review. No external implementation was copied. Human/Lead review
remains pending. No unreviewed main merge, production migration, deployment or
repository-setting change occurred.
