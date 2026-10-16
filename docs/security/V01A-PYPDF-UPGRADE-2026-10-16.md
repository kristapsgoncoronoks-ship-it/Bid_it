# V01a — Focused pypdf security upgrade (2026-10-16)

Implemented on the development branch, pending GitHub CI/review. Not deployed.
Source: [security backlog](../BACKLOG.md), V01a, and
[analysis / V01](VULNERABILITY-ANALYSIS-AND-FIX-PLAN-2026-10-16.md).

Changed the sole pypdf pin from **6.16.2 to 6.21.0**. The development environment
was upgraded with `pip install --no-deps pypdf==6.21.0`; no other declared runtime
or developer pins changed. No API, schema, totals, workflow or UI behavior changed.

## Evidence

- **203 tests passed** across 17 relevant modules: embedded UBL/CII/Factur-X,
  invoice PDF claims/layout, PDF/OCR/statement extraction, upload and email intake,
  invoice issuance/commands/delivery, credit notes, line fields and report artifacts.
  The two disjoint groups passed 179 and 24 tests. Existing meaningful document
  tests were used; no assertion/fixture was weakened and no version-only test added.
- Local integration runs used Python 3.12.14. Separate ephemeral Docker library
  smoke checks passed on **Python 3.11.17 and 3.14.8** with pypdf 6.21.0: two pages,
  multiple attachments, byte-exact XML recovery and append/rewrite round trips.
  These are library smoke checks, not full application tests on those runtimes.
- Fresh pip-audit 2.10.1 resolved the actual runtime + developer manifests:
  **pypdf 6.21.0 has zero findings**. The eight observed pypdf advisories are absent.
  OSV had also reported no advisories for this candidate during the prior analysis.
- The overall audit still fails on `python-jose 3.5.0` (`PYSEC-2026-4298`) and
  `ecdsa 0.19.2` (`PYSEC-2026-1325`, duplicated in the feed). These are tracked by
  V02/V03; no new ignore or audit threshold change was made. This package fix
  alone does **not** make PR #62 eligible for a main merge.
- `pip check`: no broken requirements. Source/tree PII and documentation checks
  were run for this change. No new full-suite or browser result is claimed here;
  the preceding complete backend result belongs to the earlier source snapshot.

## Remaining boundary and rollout

V01b is next: the generic thread/async timeout still cannot kill untrusted PDF
parsing, and the thumbnail renderer's process limits do not cover attachment
extraction. This version update is not a claim that resource isolation is done.
Other JWT, frontend, image, CI-service and history-PII backlog items remain open.

Existing PDF/intake/issue actions keep their current contracts and recovery paths.
The security plan retains their engineering prompts. At rollout, rebuild images
and restart API/worker processes so long-running interpreters do not retain the
old imported package. No production restart or deployment was performed here.

Rollback must not silently repin the vulnerable 6.16.2 package. If compatibility
problems appear, stop rollout and assess a tested patched alternative or repair;
retain the existing immutable-image, backup and application rollback procedures.
There is no database migration or historical document rewrite in this slice.
