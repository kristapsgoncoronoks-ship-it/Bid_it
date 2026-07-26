# ADR-0027 — AI capture policy (decided before any model is wired)

**Status:** Accepted — the policy is in effect and CI-tested **now**, while the
system contains no external AI at all. Extends ADR-0009 (deterministic-first
extraction).

## Context

There is no AI in the system today: extraction is a deterministic chain
(structured e-invoice XML → Factur-X embedded XML → PDF text layer → OCR →
CSV/JSON parsers), the "AI" validator is a deterministic rule engine, and
`ai_enrich()` is a literal no-op seam. That makes this the cheapest possible
moment to fix the rules an AI integration must obey — before a vendor SDK, a
demo deadline or a prompt-tuning session can negotiate them downward. The rules
are harvested from the retired Fleet Fuel system's most defensible design
decisions (its AI capture pipeline was opt-in, advisory, independently verified
and DLP-gated) and encoded as an ADR plus an executable test.

## Selected approach

Any AI capture/validation integration MUST be, in this order:

1. **Opt-in, default-off.** A fresh organization runs the deterministic chain
   only. Enabling AI is an explicit, per-organization, audited setting — never a
   deploy-time default.
2. **Advisory.** AI output is a *draft* or a *finding* a human confirms. AI never
   silently mutates a financial record, never gates a legal/derived figure, and
   never auto-approves anything (invariant §4.19).
3. **Strict.** The model never invents a field. A value the source document does
   not support is absent, not guessed; absence is representable and honest
   (`fx_source="unknown"` → NULL is the pattern to copy).
4. **Best-effort.** AI failure falls back to the deterministic chain; an AI
   outage degrades capture *quality*, never capture *availability*.
5. **Independently verified.** A verification step — a different model/provider
   than the capturer, or a deterministic checker — verifies the captured draft
   field-by-field against the source document. **The document is the source of
   truth**, never the capture. An *absent* verification result is a failure, not
   a pass.
6. **DLP-gated.** A data-classification step runs before anything leaves the
   box, persisting `{type, count}` findings and **never the matched value** (a
   DLP store must not become the leak it guards against). The gate fails
   **open** on a scan error (a broken scanner must not halt capture — the
   advisory covenant caps the blast radius) and fails **closed** only when an
   explicit policy is set and exceeded (the operator has told us what must not
   leave; then we refuse).
7. **Confidence governs only whether the advisory review runs** — it never
   skips or weakens a deterministic gate.

**The acceptance test (in CI now):** with all settings at defaults the system
runs end to end — upload → queue → worker parse → review → confirm — with the
network **blocked at the socket layer**:
`tests/test_ai_policy.py::test_defaults_make_zero_external_calls`. Any change
that wires a model into the default path fails this test before a customer
document ever leaves the box.

## Alternatives considered

- **Write the policy when the AI lands** — rejected: policies written after
  integration inherit the integration's compromises; this one is enforced while
  it costs nothing.
- **AI as the primary extractor with deterministic fallback** — rejected
  (inverts ADR-0009): deterministic paths are exact, free, offline and
  provenance-rich; AI belongs where determinism runs out.
- **Fail-closed on DLP scan errors** — rejected for the *advisory* pipeline: a
  broken scanner would halt capture entirely, and the human-confirm gate already
  bounds the harm of an unscanned advisory draft. A future *non-advisory* AI use
  (none is planned) would need to revisit this choice explicitly.
- **Confidence thresholds that auto-accept high-confidence captures** —
  rejected: capture accuracy is unmeasured; auto-accept is exactly the
  Fleet Fuel `autopilot.py` mistake this platform declined to harvest.

## Why appropriate

The product's promises — "every AI suggestion reviewable", "audit-ready" — are
architectural properties, not feature toggles. Encoding them as an ADR + a CI
test while the system is provably offline means the *first* AI PR must argue
against a green test and a written decision, not against a reviewer's memory.

## Risks

- The socket-level test cannot see a leak through a co-located proxy binary that
  is itself allowed — mitigation: the test blocks socket construction in-process,
  and no such binary exists in the deploy; review any future sidecar against
  this ADR.
- "Advisory" can creep: a UI that pre-fills and auto-submits an AI draft is
  auto-mutation with extra steps — mitigation: the human confirm gate is a
  server-side state transition, not a frontend affordance.

## Revisit when

A concrete AI integration is proposed (this ADR becomes its review checklist);
capture accuracy becomes measurable (the precondition for even discussing any
auto-accept); or a DLP policy store is introduced (the fail-open/closed split
gets per-policy configuration).
