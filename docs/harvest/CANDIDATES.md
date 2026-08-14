# Harvest candidates — paperless-ngx → InvoiceIQ

Source read: `paperless-ngx/paperless-ngx`, branch `dev`, HEAD
`01c12d9ea46c9e897464a017311be8da67668d0a`.

Produced under `docs/prompt/PAPERLESS-HARVEST.md`. Clean-room: the scouts that
read paperless-ngx never touch InvoiceIQ code; every candidate below is a prose
specification implementable by an engineer who has never seen that repository.
No code was copied, adapted or transliterated. Paths are cited as provenance,
not as source material.

**Status: PHASE 1 (harvest). Nothing assessed, nothing approved, nothing built.**

---

## SCOUT S2 — email ingestion  ✅ complete

Aimed deliberately at their thinnest-tested subsystem (17 source files, 7 test
files) on the theory that thin coverage over an external-input channel marks
where the hard-won behaviour lives. It did.

### S2-1 — An inbound channel can die completely while every dashboard stays green
**Problem.** Connection and auth failures are caught per account, logged, and the
loop continues. The task's result string is derived only from a count of newly
queued documents, so "every account failed to authenticate" and "no new mail"
produce an identical SUCCESS with "no new documents". No per-account health state
exists — no last-success timestamp, no last error, no consecutive-failure count.
Failures occurring *before* a message is fetched leave no database trace at all.

**Guarantee required.** For every inbound channel, persist the outcome of the
most recent poll and the time of the last *successful* poll, and escalate when
the gap exceeds a threshold — independently of whether documents arrived.

**Failure modes.** Rotated mailbox password → silent failure every poll forever.
Renamed folder → that rule dies, aggregate still green. Zero-document days are
indistinguishable from broken-channel days, so the operator's natural heuristic
never fires.

**Spec.** Durable per-channel health record: last attempt, last success,
consecutive failures, classified last error (auth / network / folder-missing /
server-refused / internal). Written every poll, success or failure, in its own
transaction, before other bookkeeping. Classify *sticky* failures (auth
rejected, folder missing) — alarm on first occurrence — separately from
*transient* ones (alarm after N). Render absence as a positive statement ("last
successful fetch: 4 days ago", in red), never as an empty list. The scheduled
job's own recorded outcome must reflect the **worst** channel outcome, not a
document count.

**Not taken.** Their isolation of per-account exceptions is right and should be
kept — but the swallow must cost a health-record write rather than being free.
Not taken: reporting success based on document counts.

**Provenance.** `src/paperless_mail/mail.py`, `tasks.py`, `models.py`.

---

### S2-2 — The dedup ledger key must not contain mutable configuration
**Problem.** Duplicate suppression keys on (rule, folder, server message UID,
mailbox identity epoch). The epoch column arrived later as its own migration —
field evidence that a UID-only key failed in production. Two structural
weaknesses follow from including the *rule* in the key: ledger rows cascade-
delete with the rule, so deleting and recreating a rule — routine admin — silently
rearms the whole mailbox for re-import; and editing a rule's folder re-imports
everything the old rule had already handled.

**Guarantee required.** An inbound artifact is imported at most once, and that
survives restarts, configuration edits, and a server that renumbers message
identities. The dedup key must not include mutable configuration.

**Failure modes.** Server-side UID reassignment after a mailbox rebuild or
restore. Rule recreated → full re-import of the retention window. Two rules
matching one message → two imports. The same UID twice in one response (they
defend against this explicitly).

**Spec.** Key the ledger on *channel + folder + server message identity + server
identity-epoch* — never on the routing rule. Rules are a routing concern and must
be freely editable. Where the server cannot supply an epoch, record "unknown"
explicitly rather than null-meaning-anything, and define the comparison once.
Belt-and-braces: also store a content digest (raw message, or message-id +
received date) and treat a digest match as authoritative regardless of UID —
that digest is the only defence that survives a mailbox rebuild. Write a ledger
row for the "considered, imported nothing" outcome with a distinct status.
Deduplicate within a single fetch batch as well as against the ledger. Never
cascade-delete the ledger with configuration; expose an explicit audited
"forget this message" action instead.

**Not taken.** Their two-layer check (bulk pre-filter + per-message re-check) is
good and worth keeping — the pre-filter is performance, the inner check is
correctness, and conflating them would be a mistake. Not taken: rule-scoped
keying; nullable epoch with permissive semantics.

**Provenance.** `src/paperless_mail/mail.py`, `models.py`,
`migrations/0004_processedmail_uid_validity.py`.

---

### S2-3 — Acknowledge after processing — but a failure must not be terminal
**Problem.** Server-side acknowledgement (mark read / flag / move / delete) is
correctly deferred until after processing. But the error path *also* writes a
ledger row, and the duplicate check does not consult status — so a message whose
processing failed for any transient reason is recorded as seen and never
retried, while still sitting unread in the mailbox looking untouched. Their own
documentation warns of exactly this. Symmetrically, failures occurring *before*
queueing write no ledger row at all, so a malformed message is re-downloaded on
every poll forever with no backoff and no attempt counter.

**Guarantee required.** Every inbound message reaches a terminal state:
imported, deliberately ignored, or failed-and-visibly-parked. A failure is
either retried with bounded backoff or surfaced — never both silently
suppressed and silently invisible.

**Spec.** Separate three concepts the implementation conflates: *seen*,
*acknowledged on the server*, and *terminal*. Only success and deliberate-ignore
are terminal. A failure records an attempt count and a next-eligible-retry time;
the pre-filter skips messages not yet due and reconsiders those that are. After
an attempt ceiling, move to a **loud** dead-letter state — a count on the
operations page, not a modal behind a rule row. Never acknowledge on the server
on a failed path. Give the operator "retry this message" and "retry all failed"
actions rather than requiring them to delete bookkeeping rows to force a retry.
Back off *before* the body is re-downloaded, so a poison message costs one cheap
identity check per poll rather than a full fetch.

**Not taken.** Deferring acknowledgement until after success is right and should
be copied. Not taken: terminal record on the failure path; status-blind dedup
lookup; row-deletion as the retry mechanism.

**Provenance.** `src/paperless_mail/mail.py`, `models.py`, `docs/usage.md`.

---

### S2-4 — Poll cost proportional to new mail, not mailbox size
**Problem/strength.** Two-phase by design: ask the server only for *identities*
matching the criteria, diff against the local ledger in chunks sized under the
SQL host-variable ceiling, then bulk-fetch bodies only for the residual. A test
asserts that a message already recorded as "considered, imported nothing"
triggers **no body fetch** on later polls — because such a message gets no
server-side acknowledgement and therefore keeps matching forever.

**Guarantee required.** Steady-state polling cost is O(new messages), not
O(matching messages); no poll is starved by a historical backlog.

**Spec.** Cheap-discovery then selective-retrieval for every remote inbox poll.
Chunk the local lookup so the identifier list never becomes an unbounded query
parameter set. Batch payload retrieval with an explicit size and assert in tests
that a backlog larger than one batch loses nothing at the boundary. Write a
ledger record for messages you inspect and decline, or the decline is not
durable. Add a per-cycle work cap (which they appear to lack) so a huge first
run degrades into several cycles rather than one that never finishes.

**Not taken.** Nothing — this part is done well and transfers directly.

**Provenance.** `src/paperless_mail/mail.py`, `tests/test_mail.py`.

---

### S2-5 — Attachment selection is hostile-input handling
**Problem.** Layered selection: disposition gate (inline requires explicit
opt-in, with a UI hint admitting the permissive mode is unusable without a
filename filter, since every signature logo becomes a document); case-folded
include/exclude globs; declared content type explicitly distrusted in favour of
sniffing the bytes; filenames Unicode-normalised to a composed form (a dedicated
test file exists solely for this, motivated by decomposed filenames from certain
clients) then path-sanitised into a per-message temp directory. **Gap: no
attachment size cap** — the payload is held fully in memory.

**Guarantee required.** Only parts a human would call a document are imported;
no attacker-controlled string reaches a filesystem path, filename, or type
decision; one oversized message cannot exhaust a worker.

**Spec.** Fixed-order eligibility with a logged reason per rejected part:
disposition → filename globs (case-insensitive both sides) → **size ceiling**
→ content type from sniffed bytes, never from the sender's declaration or the
extension. Normalise filenames to a canonical Unicode composition *before* any
comparison, storage or title derivation — otherwise two visually identical names
behave as different documents. Sanitise against traversal and reserved names;
deterministic generated name for unnamed parts. Extract into a fresh per-message
directory so cross-message collisions are structurally impossible. Enforce
per-part and per-message byte ceilings **while streaming**, and record an
oversized part as a visible rejection, not a silent skip. State the nested/
forwarded-message policy explicitly in the UI — recursing, ignoring, or
importing the embedded message are three defensible answers and the user must
know which they get.

**Not taken.** Their header-reordering trick to steer a downstream type sniffer
is a workaround for their router's design and must not be copied — carry an
explicit format hint from the ingesting subsystem to parser selection instead of
manipulating file bytes. Not taken: unbounded payload size.

**Provenance.** `src/paperless_mail/mail.py`, `models.py`,
`tests/test_mail_nfc.py`.

---

### S2-6 — Credential custody and the masking round-trip
**Problem.** The mailbox secret is a plaintext column; protection is delegated
entirely to the database. On read it is replaced with asterisks; on write, a
value consisting only of asterisks means "unchanged" — a sentinel-value
protocol, reimplemented separately in the connectivity-test endpoint. OAuth
tokens share the column. Refresh is attempted inline only when an expiry exists
and is past; a null expiry means refresh is never attempted. Refresh failure
skips the account with no state change and no alarm. Provider asymmetry in
refresh-token rotation is handled conditionally — get it wrong and the account
dies at the refresh-token lifetime boundary.

**Guarantee required.** Inbound-channel secrets are encrypted at rest under a
key the database alone does not yield; rotation and refresh failures are loud;
"leave the password alone" is expressed structurally, not by a sentinel.

**Spec.** Envelope encryption — per-secret data key wrapped by a KEK held
outside the database and bound to the secret's context. Never return the secret,
masked or otherwise; return "is set" plus a last-changed timestamp, and express
unchanged by **omitting** the field. Implement omit-means-unchanged in exactly
one place both the save and test paths call. Refresh proactively on a margin
before expiry; treat a missing expiry as expired rather than as valid forever.
Assume refresh-token rotation as the default — always persist a returned token.
Record every refresh outcome in the S2-1 health record, and store
provider-side revocation distinctly from a network failure: revocation needs
re-authorisation and no retry will fix it. Keep the non-public-address guard but
resolve once and connect to the resolved address, so check and connection cannot
disagree.

**Not taken.** Plaintext at rest; asterisk sentinels; reactive-only refresh;
duplicated masking logic.

**Provenance.** `src/paperless_mail/models.py`, `serialisers.py`, `views.py`,
`oauth.py`, `src/paperless/network.py`.

---

### S2-7 — Misbehaving servers: the accumulated quirk list is the asset
**Problem.** Real accommodations earned from real breakage: both source and
destination folders validated *before* work starts; on failure the code
enumerates available folders into the log specifically so the operator can
discover the server's delimiter (which varies — dot, slash, pipe); per-account
character set because search encoding is server-specific; ASCII/UTF-8 login
fallback; capability probing that changes both search criteria and tagging;
graceful degradation when a server doesn't support the identity-epoch query;
three-level failure isolation (message / rule / account).
**The conspicuous gap: no timeouts anywhere** — no connect, no read, no overall
deadline. A server that accepts the connection and goes quiet hangs the worker
indefinitely. There is also a capability flag computed and logged but never used
in the login decision.

**Guarantee required.** No remote server can hold a worker indefinitely; every
remote interaction has a deadline; one server's misbehaviour is contained.

**Spec.** Explicit connect timeout, read timeout, and wall-clock deadline per
channel poll; on deadline, abandon the channel, record a transient failure, move
on. Validate *all* referenced remote locations before doing any work. When a
named location is missing, **enumerate what does exist into the error the
operator reads** — a list of actual folder names beats any documentation about
delimiters, and this is the single highest-value operator affordance in the
subsystem. Probe capabilities once per connection and branch explicitly; treat
optional features as absent-by-default with a defined degradation logged once
per poll, not per message. Keep the three-level isolation, but pair each level
with a counter so "142 messages failed" is a number the operator sees rather
than 142 log lines. Never compute a capability and then ignore it.

**Not taken.** Timeout-free remote I/O; per-message failures invisible in
aggregate.

**Provenance.** `src/paperless_mail/mail.py`, `models.py`, `docs/usage.md`.

---

### S2-8 — The rule model: what a business user can actually predict
**Problem.** One object bundles four separable concerns: where to look, what to
match, what to import, what to do afterwards. Rules are ordered with a stop flag
whose real condition ("stop later rules if this rule imported anything") is not
what "stop processing" reads as. Cross-rule suppression exists but only within a
single run and only if the earlier rule produced a document — so overlapping
rules behave as first-match-wins, but only sometimes.

The sharpest problem: **the server-side action doubles as the deduplication
mechanism.** "Mark read", "flag" and "tag" each add a negative search criterion;
"delete" and "move" do not. So a user choosing an action is unknowingly choosing
a dedup strategy — and choosing "mark as read" makes their own mail client a
source of ingestion bugs, because a human reading the message first causes it to
be skipped forever.

**Guarantee required.** A non-technical user can predict, before saving, which
messages a rule will act on, what it will produce and what it will do to their
mailbox — and can see afterwards which rule handled a given message and why the
others did not.

**Spec.** Split the rule into visibly separate sections in model and form:
**source** / **match** (with an explicit all-or-any semantic on screen) /
**extract** / **classify** / **acknowledge**. Never let acknowledge determine
deduplication — dedup is the ledger's job and must work identically for all
actions; if server-side state is also used as a cheap pre-filter, present it as
an optimisation checkbox with its consequence spelled out. Provide a **dry run**:
show the last N messages the rule would have matched, what it would import, and
what it would do to each — before saving. Show the age window as an absolute
date at save time, not a day count. Validate glob patterns on entry. Record on
every imported document which rule produced it, and on every skipped message
which rule claimed it or why none did — *"why did this invoice not arrive"* is
the question the operator will actually ask and it must be answerable without
reading a log. Make cross-rule suppression durable rather than per-run, so
behaviour is identical whether two rules run in one poll or across two.

**Not taken.** Coupling the mailbox action to the dedup strategy; run-scoped-only
suppression. Worth copying: the four-way correspondent source, dual
include/exclude filename filters, per-account character set, and the default age
window — each is evidence of a real user problem.

**Provenance.** `src/paperless_mail/models.py`, `mail.py`, `serialisers.py`,
`docs/usage.md`, the mail-rule edit dialog under `src-ui/`.

---

### S2 null results (done well, no transferable lesson)
Provider-specific tagging quirks (vendor trivia beyond "probe capabilities").
The OAuth authorisation-code flow itself (standard; the interesting parts are in
S2-6). The GPG decryption preprocessor (clean optional extension point; we have
equivalent seams — its one transferable detail is folded into S2-3).

### S2 dropped for licensing
The specific two-phase fetch control flow and the task-graph shape: describable
only by mirroring their structure. The *guarantees* survive in S2-3 and S2-4;
the mechanism is not reproduced.

---

## SCOUT S1 — ingestion lifecycle & failure modes  ✅ complete

The richest vein in the repository: a decade of real users pushing real files.

### S1-1 — The commit boundary and the byte-writing boundary are not the same boundary
**Problem.** Files are written *inside* the database transaction, so a file-write
failure can roll the row back. That protects "row with no bytes" but structurally
cannot protect the reverse: any failure after the first byte is written rolls
back the row and leaves files behind permanently, referenced by nothing. Same
asymmetry in reprocess (derived file replaced, thumbnail move fails, row
reverts → on-disk file no longer matches the recorded hash) and in deletion
(file removal in a post-delete hook, outside the deleting transaction).

**Guarantee.** Exactly one of two end states: a committed record whose every
referenced byte-stream exists and hashes to the recorded value, or no record and
no bytes. If a third state can occur, the system must **detect and name it**
without a human diffing directory listings against the database.

**Spec.** Content-addressed, write-once store; the database row is the only thing
ever "committed". Stage bytes in a per-job temp area, hash each, move into a
final location named by hash (so a repeated move is idempotent and never
collides destructively), then commit a row recording hash + size + location per
stream. Human-readable names become a *derived view*, never what correctness
depends on. A crash before commit leaves hash-named files nothing references —
reclaimable. Ship a reconciliation sweep (rows→bytes and bytes→rows) reporting
four classes: missing bytes, hash mismatch, unreferenced bytes, internally
inconsistent rows. On demand *and* scheduled; it must never mutate — repair is a
separate explicit action. Deletion ordered the safe way: commit the row deletion
first, reclaim bytes in a separate idempotent pass, so a crash leaks storage
rather than destroying a referenced file. Their severity split (missing/mismatch
= error, orphan = warning) is right and worth copying as a concept.

**Not taken.** Writing files inside the DB transaction and relying on rollback
plus an out-of-band checker to name the leftovers: it gives the illusion of
atomicity while guaranteeing one-directional leakage, and makes the transaction
span slow filesystem I/O while holding row locks. Also rejected: template-
generated human-readable filenames as the primary path, which forces a
uniqueness search, a length-limit fallback, and a rename-on-metadata-change
subsystem with its own compensating rollback.

---

### S1-2 — The task's reported outcome does not match what happened to the document
**Problem.** Divergence in both directions. A post-commit user hook that fails
marks the **whole job failed** — so an operator re-drops the file and creates a
second copy of a document that was successfully ingested. Conversely the
reprocess task catches every exception, logs, and returns normally — so a
document whose text re-extraction failed shows as a *successful* job.

**Guarantee.** A job's terminal status is a truthful function of the durable
state it produced. Post-commit side effects must never turn a committed document
into a "failed" job without saying so.

**Spec.** Split into two recorded facts: *ingestion outcome* (committed /
rejected / errored, with the document id when one exists) and *post-processing
outcome* (per-side-effect status). A failed side effect degrades the job to
"completed with warnings" **and names the document**, never to "failed". Forbid
by convention and review any task that catches broadly and returns normally.
Every terminal state carries a machine-readable code from a closed enumeration
plus a human sentence. Their taxonomy is worth imitating as a concept because it
names the *stage* as well as the fault — file-not-found, unsupported-type,
duplicate, duplicate-in-trash, hook-missing, hook-errored, parse-stage,
thumbnail-stage, save-stage — so an operator can tell "we never got the bytes"
from "we got them and the converter died".

**Not taken.** Routing post-commit hook failures through the same fail-the-job
helper as pre-commit parse failures; the completion handler rewriting a
successful return value into a failure based on the returned dict's shape.

---

### S1-3 — A failed item has no retry, no quarantine, and re-enters on every restart
**Problem.** The input file is deleted only on success, so it stays in the
watched folder; startup enumerates and re-enqueues everything present. A poison
item therefore fails, sits, and is re-failed on every restart, burying real
failures. There is no per-document retry action — ingestion is not in the
runnable-task allow-list — so the only retry is to touch the file on disk, which
is impossible for items that arrived by API or email.

**Guarantee.** Every failed ingestion is individually retryable by an operator
without filesystem access, and is not automatically re-attempted forever. After
bounded attempts it moves to an inspectable quarantine with its reason attached
and stops consuming worker capacity.

**Spec.** Give every arriving item an identity independent of its path — content
hash plus an arrival record — the moment it is accepted, and move the bytes out
of the watched folder into a system-owned intake area *before* processing. From
then the watched folder is empty and cannot re-offer anything. The intake record
carries attempt count, last error code and state (queued / running / failed /
quarantined / done). Retry only error classes that can plausibly succeed later
(converter crash, OOM, storage unavailable); deterministic errors (unsupported
type, malformed, policy rejection) go straight to quarantine with attempts=1.
Quarantine is listed in the UI with reason, original filename, a download of the
exact bytes, and retry-now; plus retry-all-quarantined for after an operator
fixes the cause. Retention explicit, expiry announced before it happens.

**Not taken.** Leaving the failed file in the watched folder and re-enumerating
at startup; the path-keyed in-flight set with existence-based pruning.

---

### S1-4 — Worker death mid-job is unrecoverable by design
**Problem.** Default early acknowledgement, one task per child process. A worker
killed mid-parse loses the job entirely: no redelivery, and the job record sits
in "started" forever with nothing sweeping it. Idempotency of a re-run is not
designed in — it is emergent, resting on two accidents: the input file was not
yet deleted, and a preflight hash check refuses a second attempt *if* the first
got as far as committing. Everything between is a genuine hole. Telling detail:
their failure handler defends against the runtime handing it a pre-formatted
traceback because the worker process itself died — evidence this is a lived,
frequent event.

**Guarantee.** Losing a worker at any instant leaves the job either
automatically re-attempted from a defined restart point, or visibly stuck and
operator-restartable. No job sits running indefinitely unwatched. Re-running is
safe by construction, not by luck.

**Spec.** Persist each ingestion as a small state machine — received → staged →
extracted → committed → finalized — recording the artifacts of each completed
stage. Re-entry resumes at the first incomplete stage, so a crash re-does at
most one stage. Acknowledge the queue message only at a terminal state, so
worker death causes redelivery; the state machine makes redelivery safe. Add a
renewed lease; a sweeper marks lease-expired jobs interrupted and re-queues them
within the S1-3 attempt bound. Enforce per-job wall-clock, memory and
page/pixel ceilings so the common death (converter eats the box) becomes a
*recorded* failure with a reason code instead of an invisible kill.

**Not taken.** Early acknowledgement, one-shot children, no lease, no
stale-running sweeper, idempotency derived from "source file still there plus a
hash check".

---

### S1-5 — Duplicate detection: what counts, and the policy underneath
**Problem.** Two features worth stealing: the incoming hash is compared against
**both** the stored original and the stored derived-file hash (so a document
re-uploaded in its converted form is still recognised), and the comparison
deliberately includes soft-deleted documents with a *distinct* reason for
"duplicate of a trashed document" — otherwise a user deletes something,
re-uploads it, and cannot understand why it vanishes. One default worth arguing
with: rejection is opt-in, so by default a byte-identical file is logged as a
duplicate and ingested anyway; the hash column has no uniqueness constraint and
their tests assert the second copy. Intentional, but it means the default
installation accumulates duplicates.

**Guarantee.** Duplicate policy is a stated, visible decision enforced
identically across every intake channel. The submitter learns immediately which
existing document they collided with — including when it is in a state they
cannot currently see.

**Spec.** Hash the exact received bytes at the intake boundary before any
conversion. Check against all stored streams — original and derived — regardless
of lifecycle state. Make the outcome an explicit per-channel, per-tenant policy:
*reject* (default for finance-grade data), *accept-and-link* (record created but
marked duplicate-of, both visible, one mergeable), *accept-silently*. The
rejection names the colliding document, its identifier and its lifecycle state,
and the API returns the identifier so callers can link straight to it. Keep
byte-identical detection **distinct** from business duplicate detection (same
supplier, invoice number, amount) — a re-scan at a different DPI is not
byte-identical but is a business duplicate.

**Not taken.** Defaulting to log-and-consume; coupling "reject the duplicate" to
a setting whose name is about *deleting the incoming file*, so enabling
rejection also destroys the submitted bytes.

---

### S1-6 — Hostile and broken input: the specific shapes users produced
**Problem.** Mostly behavioural knowledge. What they learned the hard way:
the extension lies (sniff content); *but* content sniffing also lies in one
recoverable way — a class of scanners emits PDFs whose leading bytes sniff as
generic binary, so they keep a named allow-list of "might really be a PDF",
repair, and re-sniff — keeping the **unrepaired original** as the preserved
bytes and using the repaired copy only for extraction. Encrypted or signed PDFs
cannot be OCR'd at all: caught specifically and degraded to existing plain text
rather than failing. "Has text" is a decision two subsystems must agree on —
they found a PDF can yield non-empty text that is pure whitespace, and when the
"produce an archival copy" and "skip OCR" decisions disagreed, documents ended
up with **neither**; the fix was one shared predicate over normalised text with
a length threshold plus a structural check. Image decompression bombs get a
pixel-count ceiling before opening. Rendered-image conversion needs **both**
memory and memory-mapped-I/O ceilings, because capping only RAM makes the
converter fall through to mapping and get OOM-killed anyway.

**Guarantee.** No input may crash a worker, exhaust the host, or produce a
partially-committed document. Type by content. A failed conversion never
discards the received bytes.

**Spec.** At intake, before anything: reject zero-length with its own reason
code; enforce a max size with its own code; determine type by content with the
suffix only as tiebreaker and as the trigger for a narrow named repair path.
Preserve received bytes unchanged forever — every repair or conversion produces
an *additional* artifact, never a replacement. Run conversion in a subprocess
with wall-clock, address-space and output-size limits so a bomb kills a child
and returns a code. One shared "already carries usable text" predicate that every
downstream decision must call. Three parse outcomes: extracted, extracted-empty
(commit, flag for review), unprocessable (quarantine). Encrypted/signed inputs
are a named category with their own code and operator guidance. Route folder,
API, email and portal fetch through **one** intake function so they cannot
disagree about what is acceptable.

**Not taken.** In-process repair that rewrites the working file; applying the
recoverable-type carve-out separately in the upload validator and the consumer
(two places that must be kept in step); using the OCR library's exception
taxonomy as the system's error vocabulary.

---

### S1-7 — The folder watcher: file stability, and the file it never sees
**Problem.** Two bug classes, each with an issue number in their comments —
meaning each was a real user report. *The missed file*: the watcher is torn down
and recreated between batches, and a fresh watcher silently adopts current
directory contents as its baseline, so a file landing in the gap is never
reported. Their fix is a periodic full enumeration as a safety net. *The
re-queued file*: network storage, antivirus and NAS metadata touches generate
spurious events for a file already queued, so it gets ingested twice; fixed with
an in-flight path set. Underneath both is the partial-write problem, handled with
a stability window (size *and* mtime unchanged for a configured delay,
re-arming on change), plus a hard-won ignore list of platform noise.

**Guarantee.** A file placed in a watched location is ingested exactly once,
only after it is completely written, regardless of how the writer produced it or
whether the watcher restarted. No file sits unnoticed indefinitely.

**Spec.** Treat notifications as a latency optimisation only; the source of
truth is periodic full enumeration. Readiness by stability window, configurable
**upward** because network shares and slow scanners need far longer than local
disk. Recommend and document an atomic-rename protocol for senders you control
(write to a temp name, rename into place — complete in one step); keep the
stability window as the fallback. Deduplicate by content hash in the intake
table, not by path — a path-keyed in-memory set cannot survive a restart and
leaks entries when a job dies. Move accepted bytes out of the watched folder
immediately, which kills the whole "watcher sees it again" class at the root.
Ship a maintained, operator-extensible ignore list, and log at debug every file
seen and ignored — *"why didn't my file get picked up"* is the single most
common support question this subsystem generates.

**Not taken.** Recreating the watcher per batch to reconfigure its timeout
(which created the missed-file gap); the path-keyed in-flight set; notifications
as primary discovery with enumeration as a five-minute backstop.

---

### S1-8 — Search-index divergence, in both directions
**Problem.** On lock-retry exhaustion they do not fail the ingestion — they log
and schedule a deferred index write, so the document commits while the index
lags. If that deferred job is lost (same worker-death exposure as S1-4) the
document is unsearchable forever with nothing detecting it. Mirror image: a
deleted document whose index removal was deferred and lost leaves a search hit
resolving to nothing.

**Guarantee.** The index is a derived, rebuildable projection, never a store of
record. Divergence is bounded in time by automatic convergence, detectable by an
operator, and fully repairable by a rebuild needing no manual reconciliation.

**Spec.** Drive index updates from a durable **outbox**: every committed change
appends an index-intent row in the same transaction as the change, and a single
indexer drains it in order. "Committed but not yet indexed" becomes a *visible
queue depth* rather than an invisible hope; one writer eliminates lock
contention; drain is idempotent because add-or-update is delete-then-add by
primary key (that upsert discipline is worth copying). Expose outbox lag as an
operator-visible number. Provide an always-safe full rebuild, plus a
*conditional* rebuild that runs automatically on startup after an upgrade —
their mechanism is genuinely good and worth imitating as a concept: the index
directory stores a schema-version number and a language marker, and a cheap
startup check rebuilds only if they differ from what the running code expects.
That turns "the upgrade broke search" into a non-event.

**Not taken.** Single-writer-with-file-lock plus retry plus deferred task, where
the fallback is triggered by swallowing a lock exception at the call site and the
resulting lag is invisible to operators.

---

### S1 null results (done well, nothing to fix)
The media lock around filename generation (right shape for *their* problem;
irrelevant once S1-1 removes name arbitration). The reconciliation sweep's
severity model and per-document grouping. The task-record lifecycle: the row is
created at publish, marked started at pickup, and the failure handler owns the
failure path exclusively while the completion handler skips failures — avoiding
the classic double-write race; they also record queue wait separately from
execution duration, exactly the pair that distinguishes "we're slow" from "we're
backed up". The double-sided staging file's hard expiry and its `finally` that
deletes both halves on failure, on the reasoning that the staged half might be
the corrupt one — sound for any multi-part assembly. **One caveat to carry:**
their staging lives in a scratch directory cleared on restart, so a restart
silently loses the first half with no user-visible record; any two-part assembly
we build must make the pending half a durable, listed, expiring record.

### S1 dropped for licensing
Nothing. Every finding is expressible as behaviour, guarantee and failure mode
without reference to their code structure.

### S1 open items needing verification
1. **Zero-byte and truncated files** — no explicit guard found. Check whether an
   empty file is rejected by the unsupported-type path, and confirm the stability
   tracker treats zero bytes as stable (believed yes).
2. **The watcher's in-flight set after a dead job** — confirm a file whose job
   died stays permanently in the set and is skipped by both the event path and
   the rescan until restart. If so, S1-3 is considerably stronger.
3. **Queue acknowledgement mode** — no late-ack or reject-on-worker-lost found,
   implying default early acknowledgement and no redelivery on worker death.
   Confirm against their deployment configuration before treating as settled.

---

## SCOUTS S3, S4 — pending
## ASSESSMENT (A1 inventory, A2 invariants, A3 product fit) — pending
