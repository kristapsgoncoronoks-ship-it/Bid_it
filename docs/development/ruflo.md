# Ruflo development workflow

Ruflo 3.63.3 is optional development tooling. It adds no application dependency,
CI job, provider key, recurring task, daemon or production integration.

## Run

With Node.js 20+ and npm available, from the repository root:

```sh
npx -y ruflo@3.63.3 --version
npx -y ruflo@3.63.3 doctor --json -c node
npx -y ruflo@3.63.3 analyze code --path frontend/src --format json
```

In ChatGPT Work, invoke the saved Ruflo skill, which restores its pinned local
CLI as needed. It runs through shell tools; local MCP does not automatically
become a native ChatGPT tool.

For a separate Codex CLI client, copy only the MCP table from
.agents/config.toml into your local .codex/config.toml. Preserve existing
settings. The project template is inert until configured by that client.
Client startup/tool discovery must be checked in that client; the Codex CLI
was unavailable during setup. The local Ruflo MCP handshake was verified
during the preceding installation, independently of Codex registration.

## Working method

1. Read repository engineering rules, AI policy, relevant ADRs and current code.
2. Use explicit Ruflo analysis or structured project-local memory as useful.
3. Treat findings as leads. Confirm them in source and with relevant tests.
4. Implement only the requested scope and provide the repository PR evidence.

Never store production/client documents, bank details or credentials in memory.
Runtime directories are ignored. Do not add Ruflo to the application lockfiles.
Review version changes explicitly. Documentation and config can be reverted
without database or application rollback.

## Initial review — 2026-10-26

Source: main commit 90891d8d52cf5aefc4467e3fa7f6070c042acec2.
GitHub has renamed Bid_it to SME-financial-workspace; InvoiceIQ remains the
application brand. AI-assisted setup and review; no external implementation
code was copied into the application.

Executed evidence:

- Ruflo CLI: 3.63.3; Node doctor passed (Node 24.22.1).
- Frontend basic quality scan: 160 files, 34,220 lines, 614 functions,
  787 imports, four TODO markers (Ruflo heuristics, not correctness metrics).
- Backend basic scan returned zero files despite Python sources being present:
  excluded from any backend quality/security conclusion.
- Independent Python AST migration graph inspection: 149 unique revisions,
  one head c129fa806b32, no missing parents. This inspects current main only;
  it does not validate pending PR combinations, migrations executing against
  a database, schema drift, RLS or data preservation.

Priorities for a subsequent scoped implementation:

1. Review large frontend modules with their existing regression tests:
   lib/types.ts (2,223 lines), pages/VatAdmin.tsx (1,608), pages/Issue.tsx
   (1,157), pages/ProjectDetail.tsx (1,022), pages/Settings.tsx (1,007).
   Split only where source ownership and measured maintenance needs justify it.
2. Evaluate Python services with the existing Ruff, mypy, pytest and boundary
   checks, rather than the unsupported basic Ruflo scanner.
3. Recheck the combined Alembic graph on each proposed integration branch,
   then require PostgreSQL migration and tenant-isolation CI evidence.

No application build, backend suite, PostgreSQL checks, live provider execution
or vulnerability audit was performed for this tooling/configuration change.
No agents, background workers or remote federation were started.
