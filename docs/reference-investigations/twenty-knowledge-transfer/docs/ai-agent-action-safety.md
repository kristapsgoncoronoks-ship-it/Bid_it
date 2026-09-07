# InvoiceIQ AI / MCP Action Safety Contract

Status: **Architecture law — required before any write-capable agent/tool surface**

Reference lesson: Twenty treats an AI agent as a permissioned actor with an explicit role, tool catalog, usage controls and execution telemetry. InvoiceIQ's current AI extraction/review remains advisory; the transport MCP design is read-only. This document defines the safety boundary before that changes.

## Required controls

1. **Attributable actor**
   Every execution carries `org_id`, agent identity, initiating user/workspace member (when applicable), source, run ID and audit correlation.

2. **Agent role**
   An agent has an explicit server-side capability set. Absence of a role means no business tools.

3. **Run-as cannot escalate**
   When an agent acts for a user, effective permissions must be the safe intersection/composition of the agent's permissions and the user's permissions. Never union privileges.

4. **Tool catalog is default-deny**
   Tools are registered server-side. The model cannot invent a tool name or URL and gain capability.

5. **Read and side-effect tools are different classes**
   Payment, tax submission, invoice issuance, role changes and external writes require explicit higher-risk grants.

6. **No implicit recursion**
   Agent/workflow/automation tools have an explicit re-entry policy and server-side maximum depth/step count.

7. **Usage ceilings**
   Enforce max model steps, max tool calls, per-tenant/user cost/credit ceilings and timeout budgets server-side.

8. **Idempotency**
   Consequential tools must be naturally idempotent or require a durable idempotency key.

9. **Human approval**
   Payment execution and tax submission remain human-approved unless the product owner deliberately changes the risk policy with an explicit design review.

10. **Telemetry**
    Record per-tool latency, error, retries, model usage and cost. Do not rely only on HTTP request metrics.

11. **Secrets**
    The model never receives raw application credentials unless the specific tool requires them and the secret can be consumed server-side without exposing it to the model output/context.

12. **Audit**
    The audit record names the actor/agent, user/run-as context, tool, target entity, outcome and durable execution ID; sensitive payloads are redacted.

## Minimum validation before first write tool

- cross-tenant denial test;
- insufficient user-role denial;
- insufficient agent-role denial;
- union-escalation regression test;
- duplicate/idempotency test;
- recursive-call limit test;
- spend/step exhaustion test;
- audit attribution test;
- secret-redaction test;
- tool-not-in-catalog denial;
- failed tool call does not partially mutate financial state.

Until this contract is implemented, AI/MCP business tools remain **READ-ONLY or ADVISORY**.
