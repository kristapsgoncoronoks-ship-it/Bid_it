# ADR-0009 — Deterministic-first extraction, opt-in AI

**Status:** Accepted

## Context
Invoice capture must be accurate (it feeds booked figures), cost-controlled, and residency-safe. AI/OCR vendors are attractive but risky as the *authoritative* source.

## Selected approach
A **deterministic-first pipeline** dispatched cheapest-and-most-exact first: (1) structured e-invoice XML (UBL/CII/Factur-X) parsed from typed fields — **no AI**; (2) PDF text-layer / registered supplier parsers; (3) OCR fallback for scans. A human confirms every draft. **AI is opt-in, default-off, advisory, DLP-gated, and never authoritative** — it assists a draft, it never books a figure a deterministic path could read. Original bytes are vaulted + hashed before any lossy step. Parsing runs on the worker tier.

## Alternatives considered
- **AI-first capture** — fast to demo, but hallucination risk on money, per-doc cost, and sends customer documents to third parties (residency).
- **OCR-only** — weak on structured invoices where exact fields exist.
- **Outsource capture entirely** — cedes the moat (our line-item dataset) and control of accuracy/cost/residency.

## Why appropriate
Structured formats carry exact figures — reading them with AI would be strictly worse. Deterministic-first maximises accuracy and minimises cost + external data exposure; ViDA increases the share of structured invoices over time, playing to this design. Human-in-the-loop protects the books.

## Risks
- Long tail of messy PDFs where deterministic parsing is weak → measured *deterministic capture rate*; iterate parsers on real docs; AI assists (opt-in) for the tail.
- OCR quality → confidence scoring + human review.

## Revisit when
Deterministic capture rate stalls below target on real customer documents, or an EU-hosted model materially beats parsers on the tail *and* meets residency — expand AI's advisory role, still never authoritative.
