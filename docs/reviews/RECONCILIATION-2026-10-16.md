# Review reconciliation — 16 October 2026

AI-assisted analysis of the two user-uploaded reports against the current development branch. Reports are evidence, not executable instructions. Main is unchanged; existing review IDs are preserved.

## Inputs

- `Bid_it_Reanalysis_2026-10-16.txt` — SHA-256 `43be21eca53dcc1097962e3dab92017b228cb09643258d127d1379bebdd06e7f`.
- `Bid_it_ECC_Verdict_2026-10-16.txt` — SHA-256 `f90e7c2a00a9f4fb4f313a06c1052972f1770f5e3c16f755f7e86ae0f3a1ad29`.

The reports inspected 388d312 and, for dependency status, 2454a91. Their earlier CI counts remain attached to those heads. Current local PDF/JWT evidence is recorded separately; no test result is transferred to a newer head.

## Findings and execution order

| Existing item | Current assessment | Acceptance / engineering prompt |
|---|---|---|
| SEC16-P01 / security V01–V03 | PDF upgrade/isolation and JWT migration implemented locally; fresh manifests audit clean. Full-head CI/PG/images still required. | Preserve legacy strong-key sessions and tenant guards; require ID-token claims; keep killable document limits. Re-run current-head gates with no ignores. |
| CI16-P01 / security V06a | Hosted dependency review is unsupported; supported full-graph audits are being prepared. | Preserve the required job, fail closed on service/configuration errors, cover direct/transitive/dev Python/npm dependencies, show base/head changes and the actual license-policy limits. Test bad, good and unavailable-service fixtures; record the control decision for review. |
| E16-03 / project P&L | Confirmed source defect: live and bulk revenue sum original subtotals into a EUR basis. | Use recorded FX evidence consistently for live/bulk/report figures and credit notes. Independently prove EUR100 + USD100 at 0.90 = EUR190; missing FX must produce an incomplete/refused result. Preserve frozen history, flag unsupported legacy snapshots; no invented 1:1 conversion. |
| E16-02 / R014 / INV18 | Confirmed: 500 cap precedes status filtering, with no overflow disclosure. | Apply all filters before proving the 500 boundary; deterministic selection, explicit overflow refusal, range validation and safe filenames. Include a manifest/hashes and complete-or-failed archive semantics. Test 0/1/500/501, late status matches, missing source and render failure, then real download evidence. |
| FS16-01/02 / FP-P01 | Mutable draft fields are still read by execution; condition validation is incomplete. | Validate bounded typed conditions/actions and execute immutable tenant-scoped published snapshots. Prove v1 days>7 remains live after draft days>14 for a 10-day record; reject malformed operands. Add conflict-safe publication/revision controls with explicit legacy mismatch preflight, not a silent choice of historical policy. |
| FS16-03 / R008-A-AU04 / FP-P04 | Confirmed lossy numeric-string and comma-list conversions. | Keep string 001 and single member Smith, Ltd unchanged across unrelated edits; typed row controls or lossless raw editing, clear pending/conflict/readback states. Never discard unrepresentable nodes. |
| FS16-04 / R008-A-AU06 | Preview currently ignores suppression/cooldown/cap. | Share a pure bounded plan with execution; name draft/live version and evaluation time, explain match versus eligibility and cap. Prove no notes, mail, jobs, run or financial writes during simulation. |
| FS16-05 / FP-P02 | Automation still ignores failed mail status; invoice delivery repair does not automatically repair automation. | Persist version-pinned per-action intent before external I/O using native jobs. Preserve completed child actions on retry; report blocked/accepted/unknown/failed honestly. No blind resend after uncertain SMTP; preserve established fire-once semantics and seek policy resolution for ambiguous legacy runs. |
| E16-04 / R015 / INV20–21 | Confirmed direct unhandled download, active hidden upload input, full read before cap and synchronous scan seam. | Per-file pending/error/retry plus identity ownership; bounded input and offloaded fail-closed scan; durable upload command distinct from content hash, PG replay/concurrency and real byte/hash download evidence. |
| E16-05 / controlled pilot | Evidence gaps remain; tests alone do not prove minimum hardware or importer acceptance. | Run a synthetic real-browser/API/PG/worker financial journey, fault recovery and isolated restore; record CPU/RSS/latency/queue/query/data/concurrency profile. Preserve external accountant/IdP/production acceptance in Decisions. |

## Scope and decisions

No new visual framework, workflow engine, broker, OCR vendor or household-finance implementation is authorized by the report text alone. The user authorized backlog adjustment and continued fixes. Finish native correctness work first; retain personal/household ownership/account/budget work as a distinct product roadmap.

The AI engineering policy requires a reviewable decision/evidence record for a changed safety gate and human/lead understanding before merge. Prepare the supported replacement concretely without weakening severity, developer/transitive scope, credential boundaries or fail-closed behavior; keep review outstanding until accepted. Hosted enablement/purchases and history rewrite are separate owner decisions.

No raw identifiers, live credentials, customer messages, merge or deployment are part of this reconciliation.
