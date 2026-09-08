<!-- ENG-GOV-001 (reference integration R3). Evidence, not ceremony: a box is
     ticked only when the thing it names was actually done. The reviewer reads
     this template against docs/architecture/engineering-rules.md §11 and
     docs/architecture/AI-ENGINEERING-POLICY.md. -->

## Objective

What problem does this change solve? Why is this change required now?

## Scope

What changes? What deliberately does **not** change?

## Risk / impact

Mark each that is relevant and explain below. Do not tick a box merely to satisfy the template.

- [ ] Authentication / authorization / tenant isolation
- [ ] Financial calculation, payment, tax, billing, or reconciliation
- [ ] Personal data / retention / deletion / audit evidence
- [ ] Database schema / migration / data rewrite
- [ ] Public API / webhook / integration contract
- [ ] Background jobs / concurrency / idempotency
- [ ] GitHub Actions / deployment / secrets / infrastructure
- [ ] Dependency added or materially changed
- [ ] No material security/data/financial impact

**Impact explanation:**

## Evidence

What code, measurements, standards, issue reports, or repository evidence justify the change?

## Tests actually executed

List the exact commands/tests that were run and their result lines.
Do **not** write "all tests pass" unless they were actually executed.

## Failure / rollback

How can this fail? How do we recover or revert safely?

For migrations: IMPACT / PREFLIGHT / REFUSAL / NORMALIZATION / POSTCONDITION / ROLLBACK
(`docs/architecture/engineering-rules.md` §9), and whether downgrade is actually safe.

## External / network / secrets review

Does this add or change: outbound hosts/URLs; credentials/tokens; GitHub Actions
permissions or action versions; file parsing/upload behaviour; logging of sensitive data?
If yes, explain the threat model and controls. If a security control's status changed,
update `docs/security/security-controls.json` and re-render.

## AI assistance disclosure

- [ ] No AI assistance was used in this change.
- [ ] AI assistance was used.

If AI was used, describe what it produced or changed and how the output was independently
reviewed/tested (`docs/architecture/AI-ENGINEERING-POLICY.md` §1).

> AI-generated code, migrations, tests, security conclusions and documentation have the
> same evidence and review requirements as human-authored work — disclosure lowers nothing.

## Owner decisions / unresolved items

List any business/legal/security policy decision that cannot be inferred from code
(and its `docs/DECISIONS-NEEDED.md` section). If none: `None`.

## Reviewer notes

What was checked and what was not (root cause, simpler alternatives, authorization and
tenant boundaries, financial/data invariants, failure-path tests, docs/snapshots/register).
Prose, not boxes — a box ticked by the author is not a review.
