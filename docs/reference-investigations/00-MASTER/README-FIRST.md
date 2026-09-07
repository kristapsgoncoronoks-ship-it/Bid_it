# InvoiceIQ — Master Reference Repository Knowledge Transfer

This archive contains **all reference-repository investigation bundles available
in the current workspace on 2026-09-07**.

## Investigations

1. Scrapling
2. Paperless-ngx
3. Twenty
4. Personal Security Checklist
5. Lago
6. DuckDB
7. Stirling-PDF
8. Flask

Each investigation is preserved in its own folder with its original reports,
patches, validation notes, post-implementation review, and Lead Developer orders
where those artifacts exist.

## Start here

Read, in this order:

1. `00-MASTER/CUMULATIVE-ENGINEERING-PATTERN-LIBRARY.md`
2. `00-MASTER/CUMULATIVE-REFERENCE-REPOSITORY-LEARNINGS.md`
3. `00-MASTER/TRANSFER-INSTRUCTIONS.md`
4. The most relevant individual repository bundle(s)
5. Each bundle's `VALIDATION*.md` / `STATUS*.md` before applying any patch

## Critical interpretation rule

The cumulative documents preserve lessons across repositories. A later repository
does **not** replace earlier learning.

Treat statuses precisely:

- `PATCH READY` = proposed implementation artifact exists.
- `git apply --check PASS` = patch syntax/context check only.
- `isolated semantic validation PASS` = only the described isolated test ran.
- `DONE` is valid only if the real repository implementation and required tests/CI
  have actually passed.
- Several cycles were blocked from GitHub write because the connected integration
  had read access but no push permission (`403 Resource not accessible by integration`).

## Architectural objective

Do not copy reference repositories. Use them as engineering evidence.

The target remains InvoiceIQ's own architecture:

- typed financial core;
- strict tenant isolation;
- PostgreSQL durability;
- bounded automation;
- human-controlled financial actions;
- advisory / strictly scoped AI;
- strong operator UX;
- strong development safety gates;
- evidence-backed security posture;
- immutable build/release dependencies;
- durable external-provider handoff;
- immutable semantic financial side effects.

## Integrity

See `SHA256SUMS.txt` for file-level checksums.
