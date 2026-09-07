# Twenty → InvoiceIQ Knowledge-Transfer Bundle

## Reference

- Repository: `twentyhq/twenty`
- Commit: `59a777ea6774d3377753b982d09b6605dca50afe`
- Analysis date: 2026-09-06

## InvoiceIQ source of truth

- Repository: `kristapsgoncoronoks-ship-it/Bid_it`
- Base commit: `d30f90b69566b4d9055e597bd824932e25e4daf6`

This bundle is cumulative: it preserves the earlier Scrapling and Paperless-ngx lessons and appends Twenty rather than replacing them.

## Lead decisions

### Implement / retain
1. `TW-P2-01`: lightweight Ctrl/Cmd+K quick navigation derived from existing permission/module-filtered `navGroups`.
2. `TW-P2-02`: architecture safety contract before any write-capable AI/MCP tools.
3. `TW-P2-03`: plan-before-apply migration policy using Alembic and fail-closed data preflights.
4. Keep the previous cumulative runtime hardening: Retry-After + connect-time SSRF/DNS + background-job log context.
5. Protect `main` and pin GitHub Actions as already approved in the Paperless cycle.

### Defer
- semantic OpenAPI breaking-change classification;
- saved invoice/claim worklist views;
- richer per-domain/tool metrics;
- lazy AI tool discovery;
- isolated extension UI.

### Reject for current InvoiceIQ core
- schema-per-workspace;
- metadata-defined financial objects;
- dynamic GraphQL;
- Redis/BullMQ replacement;
- tenant-authored executable logic;
- generic app marketplace/plugin runtime;
- broad workflow graph;
- generic field/row permission DSL replacing explicit capabilities/RLS;
- write-capable AI without explicit agent authorization.

## Files

- `docs/reference-repository-learnings.md` — cumulative Scrapling + Paperless + Twenty learning report.
- `docs/engineering-pattern-library.md` — cumulative internal engineering playbook.
- `docs/ai-agent-action-safety.md` — hard requirements before write-capable agents.
- `docs/migration-plan-before-apply.md` — destructive migration review/preflight policy.
- `patches/twenty-command-palette.patch` — proposed InvoiceIQ quick-navigation patch.
- `patches/CommandPalette.tsx.proposed` — proposed component source.
- `patches/combined-reference-runtime-hardening.patch` — previous cumulative runtime hardening reinforced by Twenty.
- `patches/pin-github-actions.py` — previous immutable GitHub Actions helper.
- `BRANCH-PROTECTION.md` — production-branch governance order.
- `VALIDATION.md` — what was actually executed.
- `POST-IMPLEMENTATION-REVIEW.md` — specialist review verdicts.

## Recommended application order

1. Enable GitHub write permission for the integration or create a developer branch manually.
2. Start from `d30f90b69566b4d9055e597bd824932e25e4daf6` or rebase/review if `main` moved.
3. Apply `twenty-command-palette.patch`.
4. Add the cumulative docs.
5. Run the complete frontend build/E2E/VR/structural gates in `VALIDATION.md`.
6. Apply/review the prior cumulative runtime-hardening patch separately.
7. Run full backend/Postgres/frontend CI.
8. Protect `main` using `BRANCH-PROTECTION.md`.
9. Merge only after all required checks pass.
10. Mark items DONE only after merge/deployment evidence exists.

## Repository delivery

Branch creation was attempted on 2026-09-06 and GitHub returned:
`403 Resource not accessible by integration`.

Therefore this package is **commit-ready but not repository-delivered**.
