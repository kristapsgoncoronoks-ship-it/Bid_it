# Twenty → InvoiceIQ Post-Implementation Review

Reference: `twentyhq/twenty@59a777ea6774d3377753b982d09b6605dca50afe`
InvoiceIQ base: `kristapsgoncoronoks-ship-it/Bid_it@d30f90b69566b4d9055e597bd824932e25e4daf6`

## TW-P2-01 — Permission-aware quick navigation

### Reference Repository Engineer
**PASS**

The implementation preserves the useful Twenty principle: centralized keyboard-accessible discovery. It does not copy Twenty's metadata engine, command-menu DSL, action registry, GraphQL stack or application framework.

### InvoiceIQ System Architect
**PASS**

The proposed component consumes the same `navGroups` already filtered by module and served permissions in `Layout.tsx`. It introduces:
- no backend endpoint;
- no database table;
- no cache;
- no queue;
- no additional authorization source;
- no dependency.

The sidebar remains the primary IA and source of destination availability.

### Product / UX Engineer
**PASS WITH FULL E2E REQUIREMENT**

Benefits:
- faster navigation in a large finance workspace;
- keyboard discovery;
- explicit empty state;
- existing accessible `Modal` provides dialog/focus semantics.

Scope is deliberately navigation-only. It must not be labeled or presented as global invoice/customer/document search.

### Security Engineer
**PASS**

The palette does not calculate permissions. It receives the already-filtered destination set. Server authorization remains authoritative.

Required regression: a role that cannot see `Upload` in the sidebar must also be unable to discover `Upload` through the palette.

### QA Engineer
**PARTIAL PASS**

Executed:
- TypeScript/TSX parse + transpile: PASS;
- static no-network check: PASS;
- source-of-truth check (`navGroups`): PASS;
- shortcut/listbox/option/modal/navigation invariants: PASS.

Not executed:
- full `npm run build`;
- Playwright navigation suite;
- complete E2E;
- visual regression;
- structural frontend gates.

Those require the repository branch/runtime and remain mandatory.

### Adversarial Engineer
**PASS**

Rejected alternatives:
- a new search API;
- server-side command metadata;
- command/action plugin registry;
- keyboard-specific permission logic;
- another modal/dialog implementation.

The change has one source of truth, one new component and no persistent state.

### Lead Developer
**APPROVE FOR BRANCH — NOT DONE**

Keep the implementation if the existing frontend suite passes. Do not mark DONE until it is applied to a repository branch and all required CI checks are green.

---

## TW-P2-02 — Write-capable AI/MCP safety contract

### Review result
**APPROVED AS ARCHITECTURE LAW / NO RUNTIME IMPLEMENTATION REQUIRED TODAY**

InvoiceIQ's current AI posture remains advisory/read-only. The contract deliberately blocks future write-capable AI until actor attribution, agent role, run-as constraints, tool allowlisting, step/spend limits, idempotency and auditability exist.

This is a preventive architecture decision, not permission to add agent writes.

---

## TW-P2-03 — Plan-before-apply migration policy

### Review result
**APPROVED AS ENGINEERING POLICY**

InvoiceIQ's current Alembic architecture remains authoritative. The policy formalizes the strongest behavior already demonstrated by migration `c4d6e8f0a2b4`: inspect data before narrowing invariants; fail closed on ambiguous data; normalize only exact semantics.

Do not build a parallel metadata migration engine.

---

## Cross-repository hardening

Twenty independently reinforces the previously prepared Paperless/Scrapling runtime hardening:
- receiver-directed `Retry-After`;
- connect-time SSRF/DNS enforcement;
- background job correlation.

That cumulative patch remains separate from TW-P2-01 and still requires repository application/full CI.

---

## Delivery status

Attempted branch:
`chatgpt/twenty-reference-learnings`

Base:
`d30f90b69566b4d9055e597bd824932e25e4daf6`

Result:
`403 Resource not accessible by integration`

Final status:
**IMPLEMENTATION ARTIFACTS PREPARED; ISOLATED VALIDATION PASSED; REPOSITORY DELIVERY BLOCKED; NOT DONE.**
