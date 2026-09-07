# Transfer Instructions for the Receiving Engineering Agent

You are receiving the complete InvoiceIQ reference-repository learning archive.

## Mission

Absorb the knowledge without flattening or overwriting prior lessons.

### Step 1 — Load cumulative knowledge first

Read:

- `CUMULATIVE-ENGINEERING-PATTERN-LIBRARY.md`
- `CUMULATIVE-REFERENCE-REPOSITORY-LEARNINGS.md`

Treat those as the latest cumulative internal engineering playbook.

### Step 2 — Preserve provenance

For any recommendation or implementation, retain:
- pattern ID;
- source repository;
- reference evidence;
- InvoiceIQ evidence;
- Lead decision;
- validation status.

Never convert a source observation into an InvoiceIQ requirement without the
anti-cargo-cult test.

### Step 3 — Read individual bundles when implementing

Each repository folder contains the detailed investigation and, where available:
- patches;
- implementation orders;
- validation;
- post-implementation review;
- status;
- security/branch-protection notes.

The individual bundle is authoritative for the exact scope and validation claim
of its patches.

### Step 4 — Never overclaim implementation status

Before applying any proposed patch:
1. fetch current InvoiceIQ main;
2. compare current files to the patch's recorded base/source context;
3. rebase the patch if main moved;
4. run the named targeted tests;
5. run migration checks if DB models/migrations changed;
6. run lint/type checks;
7. run full backend/Postgres/CI as required;
8. only then mark DONE.

`git apply --check` is not a test suite.

### Step 5 — Keep cumulative pattern IDs

Do not renumber or erase prior PAT / NO pattern IDs.
Append new patterns.

### Step 6 — Keep InvoiceIQ as source of truth

Reference repositories teach principles. The target is not:
REFERENCE → COPY.

The target is:
multiple reference repos + InvoiceIQ constraints + measured product requirements
→ InvoiceIQ-native architecture.

## Important known current implementation blockers

At the time of packaging, GitHub write operations from ChatGPT were blocked by
integration permissions (`push=false`, branch creation 403). Therefore some
patches are ready but not landed.

Always inspect each investigation's status/validation file before execution.
