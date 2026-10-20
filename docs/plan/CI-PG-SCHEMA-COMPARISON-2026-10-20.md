# PostgreSQL schema comparison plan — 20 October 2026

**Status: planned, not implemented.** Findings CI-20-PG-SCHEMA-DIFF, SYS-06 and
historical DB-019. The current required hosted migration gate passes; this plan
qualifies PostgreSQL drift comparison before making it an additional gate.

Read-only investigation; no repository files, running services, constraints or published migrations changed. Existing local drift.log and drift-baseline-proof.json were inspected. No database claim was requalified.

## Diagnosis

Four ORM constraints still use portable `ondelete="SET NULL"`:
- backend/app/models/expense.py:143 — fk_expense_items_project.
- backend/app/models/invoice.py:104 — fk_invoices_project.
- backend/app/models/issued_invoice.py:52 — fk_issued_invoices_project.
- backend/app/models/issued_invoice.py:87 — fk_issued_invoices_issuer.

Published migration backend/alembic/versions/c4d6e8f0a2b4_db_integrity_batch3.py:219-249 deliberately installs PostgreSQL column subsets: issuer_id only and project_id only. This preserves the non-null org_id. The recorded four remove/add pairs match that difference and these model classes are byte-equivalent to the development baseline.

Installed SQLAlchemy2.1.4 natively supports column-list ondelete strings on PostgreSQL (introduced2.0.40), reflects them intact, and Alembic1.20 includes the complete ondelete string in FK signatures. Therefore call this a portable expected-metadata mismatch; do not claim the current dependency cannot represent the desired PostgreSQL action or that reflection is broken.

A tiny in-memory compile/parse inspection confirmed all four intended clauses compile for PostgreSQL and reflection's parser distinguishes plain/subset actions. The same subset strings raise CompileError for SQLite. Original Base.metadata remained unchanged. This did not connect to a database.

## Smallest safe follow-up

1. Explicitly annotate those four model constraints with their PostgreSQL-only null-column intent in constraint.info, preserving SQLite's current portable action. Add one small dialect metadata projection using public MetaData/Table.to_metadata APIs. For PostgreSQL only, clone metadata and express the declared subset using native ondelete strings. Validate annotation/local columns/base SET NULL action and reject contradictory declarations; quote identifiers through the dialect. Leave unrelated constraints and original Base.metadata untouched.
2. Supply that expected metadata in backend/alembic/env.py:26-42 based on the active connection dialect (and URL dialect in offline mode). Keep default Alembic comparisons; do not hide/drop foreign-key objects, strip the column list from reflected constraints, monkey-patch Alembic, or introduce name-based skip exceptions. No new database migration is required solely to correct expected metadata. If further real drift is found, stop and give it a separate migration/preflight.
3. After meaningful positive/negative controls pass, add PostgreSQL alembic check beside its existing fresh upgrade gate. Preserve SQLite's existing upgrade/check gate. This is a future gate enhancement, not a repair to a currently failing hosted migration gate.

## Verification before acceptance

- Fast compile tests: PostgreSQL gets the exact target subset for all four; SQLite remains unchanged; metadata is unchanged after alternating/concurrent dialect requests; unrelated FK signatures are unchanged; contradictory annotations fail clearly.
- Fresh migrated real PostgreSQL: native inspector plus pg_constraint/pg_attribute proves local/referred composite column order, confdeltype=n and exactly the intended confdelsetcols. Alembic check has zero operations.
- Negative control per constraint: in an owned rollback-capable schema replace only the subset action with broad SET NULL, wrong subset, or CASCADE and require alembic check to fail; restore/rollback and require clean again. Also seed unrelated real FK drift so no broad exemption can pass unnoticed.
- Extend real delete behavior to invoices and expense_items as well as issued_invoices (existing backend/tests/test_db017_db018_composite_set_null_pg.py:31 currently exercises only issued_invoices project/issuer detach). Assert child records and org_id survive, only the target link becomes null, and cross-tenant assignment remains refused. Do not generate and apply the unsafe broad replacement migrations.

## Scope limitation

backend/app/main.py:45-47 calls Base.metadata.create_all for all non-production environments, including PostgreSQL. Its current plain-FK DDL can therefore produce broader actions on an empty non-production PostgreSQL database. This is source-inferred and was not runtime-tested here. Either use the same dialect projection for that explicit create_all path or require migrated non-production PostgreSQL; record and test the choice rather than implying an Alembic-only fix solves model-created PostgreSQL too.

Production is migration-managed. A green SQLite DB-012 parity test is not proof of PostgreSQL subset parity: backend/tests/test_migrations.py:394-413 explicitly creates two SQLite databases.

## Development prompt

Implement dialect-specific expected metadata for the four composite SET NULL
constraints while preserving SQLite behavior, original metadata and every
unrelated constraint. Use supported SQLAlchemy APIs and native Alembic
comparison. Reproduce the recorded PostgreSQL difference first; prove exact
column-subset and tenant-preserving delete behavior on a fresh migrated
PostgreSQL database. Require broad SET NULL, wrong-subset, CASCADE and unrelated
FK negative controls to fail the comparison, then restore and prove clean.
Qualify the non-production create_all path explicitly. Add PostgreSQL drift
enforcement only after these checks pass. Do not rewrite published migrations,
broaden database nulling behavior, suppress comparison objects or claim
unmeasured database behavior. Record source hashes, rollback and CI evidence for
Lead review.

AI-assisted: read-only diagnosis and plan drafting. No external implementation
copied; no schema or policy decision made.
