# V07a — Private historical PII triage preparation

AI-assisted tooling, tests and analysis. Preparation implemented locally; no finding
is approved/cleared, no allowlist widened and no history rewritten. This inventory
is not the PII gate: its successful execution means a private report was produced,
not that history passed. Current-tree and historical results remain separate.

Fetched the repository's branch refs/tags for read-only inspection. A first local
8-ref inventory lacked the unrelated legacy branch; it was not used as clearance.
The complete fetched 18-ref inventory scanned 7,217 eligible unique blobs and
reproduced **872 masked finding rows: 817 VAT / 55 IBAN, across 196 blobs**.
All 196 are unresolved. A file path suggesting a test identifies a review candidate,
not synthetic provenance: 74 such blob candidates and 122 other blobs.

Two affected historical blobs are reachable from main/development. Another 194
are in the unrelated-history `claude/gracious-curie-c7so3s` branch. This ref distinction
matters: merging that history would import findings not currently in main's ancestry.
Do not merge it or delete/rewrite shared refs merely to hide a failing scan. These
counts are finding rows/blobs, not people, customers or confirmed privacy incidents.

The detailed JSON is outside the checkout in the private workspace cache, mode
0600. It contains blob IDs, ref membership, path hints and masked findings, never
identifier values or source snippets. The reproducible stdlib tool refuses output
inside the checkout and refuses overwrite. It uses the existing scanner/allowlist/
denylist logic, and does not mutate them. An empty owner-held denylist still means
only structural coverage, not verified absence of retired customer identifiers.

## Specific cleanup proposal for owner/lead review

1. Assign a named verifier under Decisions §24. Review the two main-history blobs
   first, privately using their inventory IDs/line masks; inspect raw content only
   in the authorized local archive/repository, never in tickets or external AI.
2. Review legacy-only blobs separately. Establish whether that unmerged branch
   needs preservation, access restriction or an independently approved deletion/
   rewrite. Preserve needed forensic/backup evidence before any destructive action.
3. For each match, record source/provenance, synthetic evidence or confirmed protected
   data, affected refs/artifacts and disposition. No fixture-path blanket approval.
   Verified synthetic cases need the existing named-verifier allowlist procedure.
4. Owner supplies the private retired-data identifier list/salt through the established
   secure process. Store only the supported salted hashes, never raw values in Git.
5. If protected data is confirmed, prepare exact path/blob/ref rewrite scope, backups,
   collaborator coordination, artifact/cache/fork/clone cleanup and postconditions.
   Obtain approval for that concrete shared-history change; do not run a mass rewrite
   or claim deleting a current file expunges old copies.
6. Re-run tree and uncached all-ref gates with the verified inputs after the approved
   cleanup. Keep the failing history check visible until then.

Validation: the private-tool regression builds a disposable Git history with a
synthetic identifier removed from its current tree. The old blob is inventoried,
all output remains masked, classification remains unresolved, refs are included,
permissions are 0600, in-checkout output is refused and Git stays unchanged. The
paired evidence-tool tests passed. No raw identifier is committed or printed.
Rollback of this preparation is removing the optional tool/report; it does not
change app data or gate policy. Named verification/denylist/rewrite remain blocked
external work, not falsely completed engineering.

Follow-up privacy regression: identifier-shaped path/ref metadata is now redacted
too, independently of fixture allowlisting. A deliberately sensitive synthetic
filename made the old tool fail its privacy assertion; the revised tool passes.
No raw match or private inventory is committed.

Final application-head refresh at `4635173`: **19 fetched/local refs, 7,378 eligible
unique blobs, 872 masked rows in 196 affected blobs**, all unresolved. The added
local development ref does not change the finding population. Inventory remains
private, mode 0600; the owner-held denylist still has zero entries. This refresh
changes neither classification nor history and does not clear Decisions §24.
