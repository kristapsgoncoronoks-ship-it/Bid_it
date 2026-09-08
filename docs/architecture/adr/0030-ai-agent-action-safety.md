# ADR-0030 — AI / MCP action safety: the contract required before any write-capable agent

**Status:** Accepted as architecture law — no runtime implementation today
(reference integration R3, 2026-09-07; origin: the Twenty cycle, TW-P2-02,
TWENTY-010/012/013/014, TWENTY-D6). Extends ADR-0027 (what the product may send
to AI) with what an AI may *do* in the product.

## Context

Twenty treats an AI agent as a permissioned actor with an explicit role, a tool
catalogue, usage budgets and execution telemetry — "no role means no registry
tools". InvoiceIQ's AI extraction and review are advisory; the transport MCP
concept is read-only. Nothing in `backend/app` exposes a write-capable AI or MCP
tool. This decision fixes the boundary before that changes, so the first write
tool is designed against a contract instead of setting one by accident.

## Decision — required controls before the first write-capable tool

1. **Attributable actor.** Every execution carries `org_id`, the agent identity,
   the initiating user or workspace member (when applicable), the source, a run
   id and an audit correlation id.
2. **Agent role.** An agent has an explicit server-side capability set from the
   same `PERMISSIONS_BY_ROLE` vocabulary as people. Absence of a role means no
   business tools.
3. **Run-as cannot escalate.** When an agent acts for a user, the effective
   permissions are the *intersection* of the agent's and the user's — never the
   union.
4. **Tool catalogue is default-deny.** Tools are registered server-side; the
   model cannot invent a tool name or URL and gain a capability.
5. **Read and side-effect tools are different classes.** Payment, tax submission,
   invoice issuance, role changes and external writes require explicit
   higher-risk grants.
6. **No implicit recursion.** Agent / workflow / automation tools carry an explicit
   re-entry policy and a server-side maximum depth and step count.
7. **Usage ceilings.** Max model steps, max tool calls, per-tenant and per-user
   cost/credit ceilings and timeout budgets are enforced server-side.
8. **Idempotency.** Consequential tools are naturally idempotent or require a
   durable idempotency key (`jobs.enqueue` already models this).
9. **Human approval.** Payment execution and tax submission stay human-approved
   unless the product owner deliberately changes that policy with an explicit,
   audited risk review (`docs/DECISIONS-NEEDED.md`).
10. **Telemetry.** Per-tool latency, errors, retries, model usage and cost are
    recorded; HTTP request metrics alone are not enough.
11. **Secrets.** The model never receives raw application credentials unless the
    specific tool needs them and the secret is consumed server-side without
    reaching model output or context.
12. **Audit.** The audit record names the actor/agent, the run-as context, the
    tool, the target entity, the outcome and the durable execution id; sensitive
    payloads are redacted (ADR-0012 rules apply).

## Minimum validation before the first write tool

A cross-tenant denial test; an insufficient-user-role denial; an
insufficient-agent-role denial; a union-escalation regression test; a duplicate /
idempotency test; a recursive-call limit test; a spend / step exhaustion test; an
audit attribution test; a secret-redaction test; a tool-not-in-catalogue denial;
and proof that a failed tool call does not partially mutate financial state.

## Consequences

- Until this contract is implemented, AI / MCP business tools remain **read-only
  or advisory**. A PR that adds a write-capable tool without every control above
  is refused at review (`.github/PULL_REQUEST_TEMPLATE.md` → authorization row).
- Future agent contracts link to `engineering-rules.md` and this ADR rather than
  restating them (Lago LAGO-020, DuckDB DUCK-020 lessons).
- Deferred with a trigger, not rejected: a compact tool catalogue with lazy
  loading (TW-P4-02) once tool-selection cost can be measured; per-tool metrics
  (TW-P3-03) alongside the queue's per-kind metrics.
