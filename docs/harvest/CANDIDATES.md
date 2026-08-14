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

## SCOUT S3 — search-index consistency (mostly a cautionary tale)  ✅ complete

We are on Postgres and do not have their design. Most of this is what NOT to do.

### S3-1 — Silent deferral: success reported for a write that has not happened
On lock exhaustion the wrapper does not raise — it logs, schedules a delayed
task, and returns. The caller gets 2xx. Nothing records that this document's
index state is behind: no per-row dirty marker, no queue depth, no banner.
Derived from constants: a request can block ~40-47s on the index before giving
up (4 attempts x 10s + capped random backoff), then a ~30-minute total repair
budget before the write is abandoned with no trace. *(Timing is arithmetic on
constants, not measured — NEEDS VERIFICATION.)*
**Guarantee.** A write reported successful is searchable, or the system knows —
durably and queryably — that it is not, and converges without human action.
**Spec.** Keep the search vector in the same table, populated in the same
transaction: "committed" and "searchable" become the same event and the window
disappears. Where a projection genuinely cannot be in-transaction, give the
source row an explicit `search_state` (current / pending / failed) plus
last-projected-at. Then one query answers "how many rows are behind and for how
long", the oldest pending row is an alarmable SLO, and the UI can say "indexing"
instead of lying. Never let a user request block on an index lock.
**Not taken.** Swallowing lock exhaustion, telling the caller nothing, keeping no
record of which documents are behind — it converts a loud bounded failure into a
silent unbounded one. Also rejected: spending ~47s of a web request on a lock.

### S3-2 — Divergence is undetectable, and the health check can lie
There is no verification path from index back to database anywhere — not in the
sanity checker, the status endpoint, or any scheduled task. The absence is
complete. Status reports OK if the backend merely *opens*, and freshness is the
max file mtime (acknowledged in-code as a proxy). Their docs state the detection
strategy plainly: if search returns nothing or returns non-existent documents,
recreate the index manually — i.e. the detector is a human noticing.
Worse: opening is self-healing **by wiping**. A schema/language sentinel
mismatch deletes the directory contents and writes fresh sentinels — so a
process that opens first can leave a valid, empty, correctly-stamped index, and
the conditional reindex then reports "up to date" and does nothing.
**Guarantee.** "Is my search surface consistent with my system of record?" must
be a cheap, scheduled, automatic question; a health signal must never report OK
for an index that is structurally intact but semantically empty.
**Spec.** We already own this pattern — the live PDF store is re-hashed against
stored digests with a fast incremental sweep and a deep pass the cache can never
mask. Extend it: a cheap drift probe (counts + max-updated-at on both sides) and
a deep probe reconciling identifier sets **in both directions**, reporting
**missing** (correctness/revenue) separately from **orphaned** (data exposure).
Route results to the admin error log and a red banner, on the same schedule as
the document-integrity check. Make health semantic: a canary record must be
findable, counts within tolerance; if the check can only prove a file opened it
must report `unknown`, never `OK`. **Never let a component self-heal by
destroying data on a config mismatch** — mark stale and refuse, or serve
degraded with a loud banner.
**Not taken.** Liveness-only probes; mtime as freshness; auto-wipe on open;
sentinel files written before content is proven present.

### S3-3 — Repair is wipe-and-rebuild, and it can cause the divergence it fixes
The only repair is a full reindex: delete the directory, rebuild from scratch,
inside **one database transaction**. No incremental mode, no resumability, no
coordination with live writers. Search returns zero results for the whole
rebuild. Documents ingested during the run land in files the rebuild already
deleted and are silently absent afterwards — so the operator runs it again, and
loses whatever arrived during *that* run.
On Postgres the long transaction is far worse than on their common SQLite
deployments: it pins the oldest snapshot, blocks autovacuum database-wide, and
on a replica either gets cancelled or blocks WAL replay.
**Spec.** Three named operations, all restartable and safe under load: targeted
repair (explicit id list — the unit of everything else, fed directly by the
drift probe's samples), incremental repair (everything not `current`, batched,
committing per chunk, with a durable high-water mark), and full rebuild (same
loop, no filter, still committing per chunk). If an external structure must ever
be fully rebuilt, build into a new location and swap atomically. Coordinate with
a **database advisory lock keyed by operation name** — we already have process-
lock discipline for the close. Rate-limit and report progress; the operator's
real question is "how much longer", and the honest answer is what stops them
killing it halfway. Delete no-op ceremony (their "optimize" is a documented
no-op, still scheduled, still logging — it trains people to ignore index tools).
**Not taken.** Wipe-and-repopulate with no incremental path, no resumability, no
writer coordination, whole run in one transaction.

### S3-4 — The single-writer file lock is an architectural ceiling
All mutation serialises through one advisory lock on one file, with a commit and
merge-wait *inside* the critical section. Concretely it forbids: more than one
effective writer ever; more than one host (it is a local filesystem primitive,
and advisory locking over NFS/SMB is least trustworthy exactly there); web/worker
separation; and bulk operations at scale — the batch path is good design, but it
makes held duration proportional to batch size, so one bulk edit converts a
burst of interactive writes into a burst of silent deferrals. Parameters are
compile-time constants: an operator who hits the ceiling has no dial. Nothing in
the docs states the index directory must be local and single-owner.
**Spec.** Do not introduce a global writer lock for search. In-row vectors mean
N writers touching N records never contend; two writers on one record serialise
on the row lock that already protects it. Where mutual exclusion is genuinely
required (rebuild, schema migration) use a **database advisory lock**: visible
in lock views, released automatically when the session dies (a file lock leaked
by a killed process is not), identical across hosts. State storage-locality
assumptions explicitly in operator docs — an undocumented locality assumption is
an incident waiting for a well-meaning sysadmin. Chunk bulk operations into
fixed-size committed batches.

### S3-5 — An index entry can outlive its document, and it carries the content
The index stores content, title, notes, custom-field values **and the permission
grants**. Removal and row deletion are two steps with no transaction between
them, ordered **both ways** in different call sites: single delete removes from
the index first (so a failed row delete leaves a document that exists but is
permanently unfindable, with no repair path); bulk delete deletes rows first and
the index removal *raises* on lock exhaustion — caught by a broad handler that
logs and returns success. The entries, with their text, survive permanently.
Two things make this disclosure rather than correctness: **type-ahead is served
purely from the index**, filtered by the permissions stored *in* the index — so a
document whose access was revoked keeps leaking its vocabulary until the
projection catches up, which per S3-1 can be unbounded. And deletes in a
segment index are tombstones: the text is physically present until a merge.
**Spec.** Delete derived data in the same transaction as the record — in-row
vectors give this free, and it is the strongest argument for that design. For
any store that cannot be in-transaction, **invert the ordering**: remove the
derived copy first, commit the record deletion second, conditional on the
removal having succeeded. Failing safe means "still there and still findable",
never "gone but still searchable". An unconfirmed erasure is an open obligation
— a durable pending-deletion row alarmed on age, never a logged warning. Treat
every derived store as in scope for sensitivity labels, retention clocks, legal
hold and the backup/erasure inventory. **Never authorise from a snapshot**: every
surface deriving from documents — lists, type-ahead, aggregates, similarity —
applies permissions from the live record store at query time. Type-ahead is
exactly where this is forgotten, because it returns terms rather than records.
**Not taken.** Two-step delete with inconsistent ordering and a swallowed
failure; storing permission grants in a derived index at all.

### S3-6 — Dual-write inside a transaction: the index can be *ahead* of the record
The index write is triggered inside the storing transaction, so it commits
before the database does. A rollback afterwards strands the entry — with full
text — permanently; invisible to the result list (which intersects ids) but
visible to type-ahead and counted in statistics forever. The transaction is also
held open across an acquisition of the index file lock, so ingest transactions
sit open for a duration determined by *search* contention: idle-in-transaction
sessions, blocked vacuum, pool exhaustion.
**Spec.** State it as inviolable and enforce it in review: **a database
transaction may contain only database work** — no file lock, no network call, no
external commit, no message publish. Anything that must happen *because* a write
committed hooks the commit event, not the write. Our existing pattern is already
right and should be the only one: commit the row plus a durable queue entry in
the same transaction, and let the worker act on it. Obligation stored
transactionally, execution outside the transaction. Order the systems so the
record store always leads: a follower that lags is a bounded, measurable,
automatically-repairable staleness problem; a follower that *leads* is a phantom
with no repair.

### S3-7 — What they got right, and we should take
**(a) Defer by identifier, re-derive at execution.** Deferred tasks carry only an
id, re-read current state at execution, and treat a missing record as success.
That makes retries idempotent and order-insensitive: a stale update queued at T
cannot resurrect old content at T+300 because it re-reads at T+300. Contrast
queueing a serialised body, which reintroduces last-writer-wins. **Rule: every
async derived-data job carries identifiers and nothing else.**
**(b) The index proposes, the database disposes.** The ranked path uses the index
only for candidate ids and ranking, then intersects against a permission-filtered
queryset. That is why their consistency bugs manifest as missing results rather
than leaks — everywhere they applied it. They did not apply it to type-ahead;
the lesson is that it must cover *every* surface, including ones returning terms.
**(c) Degrade at a third-party boundary.** A scoring crash across the FFI
boundary is caught broadly, logged with cause **and operator remedy**, degraded
to "no similar documents" rather than a 500 — with the upstream fix named and a
removal condition for the workaround. The addition worth taking over our current
practice is the **removal condition** in the comment.
**(d) A record-store-only search mode** exists but is a manual toggle, not an
automatic fallback. Principle for us: any advisory enrichment (semantic ranking,
AI query rewrite) must be strictly additive and fall back automatically to the
deterministic path, telling the user it is in basic mode.

### S3 non-findings (complete absences, not gaps in reading)
No index-vs-database verification anywhere. No per-document index-state tracking.
No user-visible indexing-status signal. Lock parameters not configurable. Docs
never state the single-writer / local-filesystem requirement.

---

## SCOUT S4 — what makes a large document set workable  ✅ complete

Judged strictly by "does this help a finance operator find, verify and act on
invoices faster". Several document-manager features are explicitly rejected.

### S4-1 — The working set as a first-class, versioned, persisted object
Their filter schema is a flat list of `(integer_rule_code, string_value)` pairs;
the vocabulary has grown to ~50 numeric codes, three marked deprecated-but-
retained *because saved views still contain them*, with two migrations that
rewrite stored filter values by regex when the query language changed.
**Failure modes.** Positional integers mean the on-disk meaning of a view lives
in two lookup tables that must be edited in lockstep. A regex rewrite over
user-authored query strings is lossy with no verification pass. Deprecated codes
accrete permanently and warn at *query* time, per request. A single 255-char
value column means "any of these 12 suppliers" is a comma-joined string with a
silent ceiling. View *placement* was originally a boolean on the shared object,
so one user pinning a shared view changed it for everyone.
**Spec.** A named, owned, permissioned **Saved Working Set** storing predicates,
sort, page size, column set, display mode. Predicates use **string operator
keys** (`supplier.in`, `period.relative`, `claim.deadline_within_days`) — never
positional integers; a string key is self-describing in a database dump and
survives reordering. Operands are JSON, not truncated strings. Carry a
`predicate_schema_version`; an unknown version opens **read-only with a banner**
rather than evaluating under new semantics. Never regex-rewrite a user query
without escrowing the original. Separate *definition* (shared) from *placement*
(per-user). Show a dirty marker with save/save-as/revert, and a persistent
"(filtered)" indicator with one-click reset next to the count — an operator
staring at "0 invoices" must tell in one glance whether that is truth or a stale
filter. Finance predicates that earn their place: supplier/issuing entity,
supply country, relative period, claim status, readiness bucket, has/missing
source PDF, unmatched lines, amount range, currency, duplicate-suspect,
awaiting-confirm. **Reject**: storage path, archive serial, MIME type, more-like-
this, shared-by-me.

### S4-2 — "Apply to everything matching this filter" is a different, more dangerous operation
Their bulk API accepts an id list *or* an "all" flag plus filters, re-resolving
the filter server-side at execution — while the confirmation the user saw was
built from a count the *client* held from the last page load.
**Failure modes.** Time-of-check/time-of-use: ingestion between render and
execution silently widens the scope. Permission filtering happens inside the
resolution, so the effective set narrows with no "12 of your 340 were skipped"
message — and the explicit-id path 403s while the filter path silently narrows,
two different semantics for one button. The endpoint collapses any exception
into a generic "check logs".
**Spec.** Treat them as distinct contracts. Explicit selection writes exactly
those ids or nothing. Filter selection sends the filter **and the count shown to
the user**; the server re-resolves and **aborts** on mismatch, returning the new
count for re-confirmation — a cheap optimistic-concurrency token over a set that
converts silent scope creep into visible scope creep. Report structured outcomes
(`requested / resolved / changed / skipped_no_permission / skipped_locked /
failed[]`) with domain skips first-class: filed or locked claims, closed
periods. **Refuse filter-selection entirely for irreversible actions** (delete,
submit, lock) — those require explicit ids so the operator has seen every record.

### S4-3 — Bulk edit safety: the delta model, the sentence, and the undo
Worth stealing: a **tri-state** editor driven by a selection-summary endpoint
returning per-attribute counts (full count = selected, partial = indeterminate),
with the write sending only **deltas** the user toggled, so untouched partial
attributes are left alone. And confirmation sentences built from the actual
operands and count, not a generic "are you sure".
**Failure modes.** The tri-state read is a separate round-trip, so the selection
can change under it. Confirmations are **globally suppressible** by a preference
— once off, a mis-click silently rewrites hundreds of records. There is **no
undo**; the only reconstruction is the audit log, which is conditional on audit
being enabled, and whose field snapshot comes from a **hand-maintained
method→field map** — a new bulk operation added without updating it produces no
audit trail at all, silently. Tag add/remove auto-expands through the hierarchy
but the confirmation names only the tag the user picked.
**Spec.** Selection-summary endpoint + delta writes (never transmit full desired
state from a tri-state UI — that is how partial values get flattened). Compose
the sentence from real operands and count, **including derived effects** (if
assigning a supplier re-derives the issuing entity, say so). Make bulk edits
**undoable for a bounded window**: persist a reversal record derived
*mechanically from the write itself* — snapshot the fields the write is about to
touch — rather than from a hand-maintained map, which is exactly where their
trail goes missing. Refuse to bulk-modify records in a submitted claim or closed
period, reported as explicit skips.

### S4-4 — Operator-defined fields, and the money mistake to avoid
Their model: a definition plus one instance row per (document, field) with a
column per type. **The weak point is money**: stored as a display *string* with
an embedded 3-char currency prefix, recovered by a generated column that strips
the prefix and casts — so mixed-currency values in one field compare and sum as
if commensurate, and the same fragile 3-char assumption is implemented a second
time in the filter layer "for backwards compatibility with saved views".
**Spec.** Definition + typed instance table, unique on (record, field). **A money
field is two columns — decimal amount and ISO currency code** — never a prefixed
string, never a generated column recovering a number from display text. Reject
at write time an amount whose currency differs from the field's declared
currency unless explicitly multi-currency, in which case aggregation must refuse
to sum across currencies and say why. (This is our §4.14 invariant; their design
violates it structurally.) Filter via a small typed expression tree with
per-type operator allowlists and **hard caps on nesting depth and atom count** —
the caps are load-bearing, since this is user input compiled into SQL. Errors
addressed by their path in the tree so the UI highlights the offending row.
Store select values as stable generated option ids, never labels. Provide the
thing they lack: a **previewable type-change path** showing how many values
convert cleanly, how many are lossy, how many fail.

### S4-5 — Automation must not overwrite a human, must abstain when ambiguous, must explain
Three safety behaviours worth stealing: automatic assignment **returns
immediately if a value already exists** unless an explicit replace flag is passed
(reachable only from an admin job, never routine ingestion); on multiple matches
the caller can choose **assign nothing**, logging that it deliberately abstained;
and every rule match emits a reason naming *why* ("contains all of these words",
"&lt;matched text&gt; matches &lt;pattern&gt;"). Their re-tagging job has a **dry-run** that
prints would-set / would-add / would-remove per document without writing, and
the interactive suggestions endpoint returns candidates as ids to offer the user
— it never writes, and is cache-invalidated against the classifier identity.
**Failure modes.** "Take the first match" is the default and "first" is queryset
ordering — arbitrary among equal candidates, and the record carries no marker
that the value came from an ambiguous set, so nobody can find the guesses later.
Explanations live only in server logs. Nothing distinguishes human-set from
machine-set except an indirect heuristic (a tag with no pattern and not auto ⇒
manual) that breaks the moment someone adds a pattern to a previously-manual tag
— their manual assignments retroactively become machine-managed and removable.
Operator-supplied regex needed a safe-search wrapper, i.e. it is a live DoS
surface. Fuzzy matching's own explanation is candidly "parts somehow match".
**Spec.** Store **provenance on the record**, not inferred: source (`human` /
`rule:<id>` / `model:<id+version>` / `import`), confidence, timestamp, actor, and
a human-readable reason. Everything else follows mechanically: a non-human write
refuses to overwrite a `human` field; ambiguity **abstains** and sets a
needs-decision marker carrying the candidates and their reasons; the reason is
shown in the UI beside the value ("Supplier matched: VAT number LT1234… found in
footer") — the single highest-value item here, because trust in automation is
bounded by the ability to interrogate one instance of it. Re-derivation jobs run
**preview-by-default**. Suggestions are read-only and cache-invalidated against
the ruleset identity. Bound operator regex execution and validate at save time.
**Do not ship fuzzy matching for supplier identification** — it cannot explain
itself and mis-identifying a seller corrupts a VAT claim; marker-based (VAT/reg
number) with human confirm is strictly better, which is what we already do.

### S4-6 — Duplicates and superseded documents, as the user experiences them
The hash check runs **before** any processing, against original *and* archived
renditions of all records **including soft-deleted**, and the rejection carries
the **id of the record it duplicates** plus a trashed flag — which is what makes
the UX work: the task list renders "Duplicate of document #N" with a
click-through, and the record grows a Duplicates tab.
**Failure modes.** The default is to **consume the duplicate anyway** — the
loud, useful behaviour is opt-in. Detection is exact-content only, so a supplier
re-issuing the same invoice as a freshly-rendered PDF is invisible — the highest-
value duplicate class in an invoice platform. Version resolution needed a
per-request memo cache because "which document am I looking at" is genuinely
ambiguous in the URL space; a deep link omitting the version means "latest",
which changes meaning over time.
**Spec.** Check before processing, against every stored rendition including
soft-deleted; **reject by default**; return structured data (matched id, title,
lifecycle state) so every surface renders a link. Layer a **second, advisory
domain check** hashing cannot do — same (supplier VAT, invoice number) or same
(supplier, date, gross, currency) — surfaced as "possible duplicate" with
differences highlighted and an explicit recorded human dismissal, and wired into
the claim-readiness gate, because a duplicated invoice inside a claim is a filing
error. Model supersession as **versions on a stable root**: a root reference
means current, a version reference means exactly that, and neither-of-those is an
error rather than a silent fallback. **Anything legally referenced — a filed
claim, an audit snapshot, an exported workbook — pins the specific version id**,
never the root, so a later re-upload cannot retroactively change what was filed.

### S4-7 — The background-work ledger is the "why isn't this what I expected" surface
Theirs is quite complete and worth mining: type and **trigger source** as
separate closed enumerations (scheduled / UI / API upload / folder / email /
system self-heal / manual), a documented status machine, created-started-
completed timestamps, and **two** derived durations — execution time *and queue
wait*. Plus structured input and result, an acknowledged flag, and derived
related-record ids resolving either the produced record or the one it duplicated.
Indexes built for the query that matters: (owner, acknowledged, created).
**Failure modes.** Results are free-form JSON and the related-records accessor
pattern-matches keys, so a new task type naming its output differently silently
loses click-through. Acknowledgement is a bare boolean — "who decided this
failure was fine" is unanswerable. The ledger covers *system* work only; user
bulk writes are audited elsewhere and conditionally.
**Spec.** One row per async unit with type, **trigger source**, status, the
timestamps, execution duration **and queue wait** (the number that tells an
operator whether the system is behind — and the one usually missing). Define the
outcome as a **typed contract**, not free-form JSON, so every surface renders a
click-through without key-sniffing. Errors carry a stable code and a remediation
sentence written for a finance operator ("supplier VAT number could not be read
from the footer — open the invoice and set it manually"), not a stack trace.
Acknowledgement is a record (who, when, note), not a flag. Group repeated
identical failures — 400 failed fetches are one row with a count. Extend the same
ledger to **user-initiated bulk writes** so "what did that button do" and "what
did the system do overnight" are one query. Distinguish four list states
honestly: nothing exists / nothing matches your filter (show it, offer reset) /
the query failed (inline, not a vanishing toast) / still loading.

### S4-8 — Period-relative working sets
Their filter rules have **no relative-date concept**; relative dates exist only
as string conventions inside the free-text query ("created:[-1 week to now]"),
recovered by two hand-written regexes to light up a chip from a fixed twelve-
phrase vocabulary. Absolute-date rules in saved views silently rot — a view saved
as "created after 2025-01-01" keeps growing forever and nobody is told.
**Spec.** Make period-relativity a **first-class predicate operand type** —
`current_quarter`, `previous_quarter`, `current_filing_year`, `last_n_days:<n>`,
`deadline_within_days:<n>` — resolved server-side against the tenant's fiscal
calendar and timezone. Structured tokens need no regex round-trip, compose with
every other predicate, and let the UI render "resolves to 2026-04-01 –
2026-06-30" under the chip. Highest-value tokens here are deadline-relative:
"filing deadline within N days", "period closes within N days", "older than N
days without a source document". Mark saved sets containing stale absolute
bounds so nobody keeps working a view that quietly stopped meaning its name.

### S4 rejected as bad product fit (recorded so they are not re-harvested)
Storage paths / templated filesystem layout, archive serial numbers, arbitrary
user-authored document links (their bulk edit has to maintain **symmetric**
back-references on add *and* remove), share links with bundles and watermarks,
more-like-this browsing, tag hierarchies with cascade. All are document-manager
identity features; our records already have canonical identity (supplier +
invoice number + period) and a hashed vault.
**Bulk PDF surgery** (rotate / split / merge / delete pages / remove password)
exposed over a filtered selection: in a VAT context the source PDF is evidence,
and bulk-mutating evidence across a filter is a liability. If page correction is
needed it is a single-record, single-confirm action producing a new version with
provenance.
**Icon pickers persisted as a database enumeration** (~60 entries) — every new
icon becomes a migration.
**Globally suppressible confirmations** — the most tempting thing to copy and the
most likely to cause a costly incident.

---

## ASSESSMENT — candidates vs. what we already have  ✅ complete

A1 built the InvoiceIQ capability inventory without ever opening paperless-ngx.
Assessment below is the coordinator's, applying it. Most candidates die here.
That is the intended outcome.

### ⛔ REJECTED — we already have this, equal or stronger

| Candidate | Why it dies |
|---|---|
| **S1-4** worker death / re-run safety | We are strictly stronger: atomic guarded claim, `UniqueConstraint(org,kind,idempotency_key)`, exponential backoff, **leases + `reclaim_stale`**, dead-letter, missing-handler dead-letters immediately. They have early-ack and no lease. |
| **S1-1** content-addressed store | Already ours: `content_key(prefix, org, sha256)` → `prefix/org/ab/cd/<sha>`, `Path.is_relative_to` traversal guard, `Storage` protocol (Memory/Local/S3). The reconciliation-sweep idea is partly covered by `services/integrity.py`. |
| **S1-6** hostile input | `filesec.py` is a stronger gate than theirs: magic-byte sniff + per-kind structural recheck + universal executable/archive rejection + EICAR/clamd **fail-CLOSED** + one single definition of the size cap with a structural test forbidding a second. |
| **S1-5** duplicate detection | Have both layers already: byte-identical `check_duplicate_upload` (409 `duplicate_upload`, explicit override) **and** `services/duplicates.py` with three never-conflated signals (exact / cross-supplier / scored ±1% in 14 days), all advisory. |
| **S2-6** credential custody | *Partially* — see LATER. Our secret-at-rest story for mail accounts is **NEEDS VERIFICATION**; A1 found no envelope-encryption module in this repo. |
| **S3-1/3/4/6** index lock, wipe-rebuild, dual-write | We have **no search index at all** (ADR-0014 is "Proposed"; zero `tsvector` hits). Nothing to fix. Retained purely as **design constraints for the search we have not built** — see BUILD LATER. |
| **S4-4** custom fields | Their model is the anti-pattern here: money as a currency-prefixed **display string** recovered by a generated column. That violates our §4.14 outright. We have five registry-defined dimensions with master data. Not adopting theirs. |
| **S4-6** duplicate/version UX | Versions exist (`document_versions.py`, one `is_current`). The *advisory business-duplicate* layer exists. Only the "pin the specific version id on anything legally referenced" rule is worth carrying — folded into LATER. |
| all **S4 rejected-as-bad-fit** | Storage-path templating, archive serials, user-authored document links, share links, more-like-this, tag hierarchies, **bulk PDF surgery over a filtered selection**, icon enums, globally suppressible confirmations. Recorded so they are not re-harvested. |

### ✅ BUILD NOW — real gaps, high value, additive

**H-1 · Failed-capture worklist** *(from S1-2, S1-3, S4-7; confirmed gap in A1 §10)*
A1: *"there is no failed-capture worklist — no route or page enumerates failed
extraction runs for a tenant… a silently failed capture is a document the
customer thinks was processed."* `extraction.pending_review_filters` covers only
`status=="parsed"`. Today a failed run is visible only by polling
`GET /invoices/upload/{run_id}` — you must already know the id.
Take from S4-7: a typed outcome contract (never free-form JSON), a stable error
**code plus a remediation sentence written for a finance operator**, grouped
repeats, and acknowledgement as a *record* (who/when/note) not a boolean. Take
from S1-2: never let a post-commit side-effect failure mark the whole thing
failed without naming what did succeed.
Additive, no invariant conflict, no migration to existing semantics.

**H-2 · Inbound-channel health** *(from S2-1, S2-7)*
Their inbound channel dies silently while every dashboard stays green, because
success is reported from a *document count*. We have `email_intake` and a
`/health/queue` SLO, but A1 found no per-channel health state.
Durable per-channel record: last attempt, last success, consecutive failures,
classified error (auth / network / folder-missing / server-refused / internal).
Sticky failures alarm on the first occurrence; transient after N. Render absence
as a positive statement — *"last successful fetch: 4 days ago"* in red — never as
an empty list. And **timeouts**: S2-7 found none anywhere in their remote I/O;
we must set connect, read and per-poll wall-clock deadlines.

**H-3 · Automation provenance shown to the operator** *(from S4-5)*
We already refuse to let derived values overwrite humans (I-18, `capture_memory`
is read-only by construction). What we lack is S4-5's third leg: **the reason,
on the record, in the UI**. "Supplier matched: VAT number LT1234… found in
footer." Their explanations exist only in server logs, which is why their users
cannot interrogate automation. Trust in automation is bounded by the ability to
interrogate one instance of it.
Plus **abstain-on-ambiguity**: never take first-match; leave empty, set a
needs-decision marker carrying the candidates and their reasons.

### 🕐 BUILD LATER — valuable, bigger, or needs a decision first

- **L-1 · Saved working sets** *(S4-1, S4-8)* — we have **zero** saved views and
  a single-column `ILIKE` on invoice number. Highest operator value in the whole
  harvest, but a real build. When we do it: **string operator keys, never
  positional integers**; JSON operands, not truncated strings; a
  `predicate_schema_version` where an unknown version opens read-only with a
  banner; period-relativity as a **first-class operand type**
  (`previous_quarter`, `deadline_within_days:<n>`) resolved against the tenant's
  fiscal calendar — never as phrases inside a text query recovered by regex.
- **L-2 · Search** *(design constraints from all of S3)* — ADR-0014 is unbuilt.
  When built: in-row `tsvector` maintained **in the same transaction**, so
  "committed" and "searchable" are the same event; a drift probe reporting
  **missing** and **orphaned** separately; a health check that is semantic
  (canary findable) or reports `unknown`, never `OK`; targeted/incremental/full
  repair, all restartable, coordinated by a **database advisory lock**, never a
  file lock; and **never authorise from a snapshot** — type-ahead included.
- **L-3 · Mail-account secret custody** *(S2-6)* — **NEEDS VERIFICATION** of how
  `email_intake` credentials are stored today. If plaintext: envelope encryption,
  never return the secret even masked, express "unchanged" by **omitting the
  field** rather than an asterisk sentinel, and refresh proactively on a margin.
- **L-4 · Bulk operations** *(S4-2, S4-3)* — we have no multi-select anywhere.
  A1 warns bulk collides with per-record audit old→new, per-record SoD, opaque
  404 and quota metering. **If** we build it: the agreed-count guard (client
  sends the count it displayed; server aborts on mismatch), structured outcomes
  with domain skips first-class, a reversal record derived *mechanically from the
  write*, and **filter-selection refused outright for irreversible actions**.
- **L-5 · Version pinning on legally-referenced documents** *(S4-6)* — anything
  filed, snapshotted or exported pins the specific version id, never the root,
  so a later re-upload cannot retroactively change what was filed.

### 🔴 P0 — NOT a harvest item: a live defect A1 found in our own code

`POST /invoices/upload/{run_id}/retry` (`api/routes/invoices.py:879-884`) deletes
**every** `ExtractionField` row for the run — including `reviewed_value`s a human
typed. The guard only refuses when `run.invoice_id is not None or status ==
"saved"`, so a capture that is parsed **and human-reviewed but not yet saved** is
in scope. The audit chain records the corrections, so it is forensically
recoverable, but the human's work is silently discarded from the live record and
the docstring does not warn.
Human-triggered, so it does not violate §4.19 literally. It is still the sharpest
edge in the codebase and it is thematically identical to the harvest's dominant
lesson: **automation must not destroy human work**. Fix before any harvest item.

### Recommended first work order
**H-1, the failed-capture worklist.** It closes a hole where a customer believes
a document was processed and it was not; it is additive; it conflicts with no
invariant; and it is the natural place to land S4-7's typed-outcome contract that
several later items depend on. **P0 (the retry data-loss fix) goes first** — it is
ours, it is small, and it is a correctness bug rather than a feature.

### Owner decisions needed before Phase 3

**1. P0 retry fix — SETTLED (shipped).** `342c1fa`, `docs/plan/plan-a/wo/
WO-98-capture-retry-review-guard.md`.

**2. BUILD NOW scope — SETTLED: all three shipped.** The owner chose "both H-2
and H-3" after H-1 landed.
* H-1 · failed-capture worklist — `WO-99-failed-capture-worklist.md`
* H-2 · inbound-channel health — `WO-100-inbound-channel-health.md`
* H-3 · automation provenance — `WO-101-automation-provenance.md`

**3. L-4 bulk operations — SETTLED: yes, build it carefully.** The owner chose to
build multi-select WITH the guards, so A1's warning becomes a design constraint
rather than a reason to skip. The agreed shape:
* the client sends the COUNT it displayed; the server aborts on mismatch (the
  list moved under them, so their selection is not what they think it is);
* structured per-record outcomes, with domain skips first-class — not a boolean
  and not an exception count;
* a reversal record derived MECHANICALLY from the write, never hand-authored;
* filter-selection ("everything matching this filter") refused outright for
  irreversible actions — only an explicit, enumerated selection may destroy.
Not yet built; this is the next work order.

**4. L-3 mail-account secret custody — ANSWERED, and it is a non-issue.**
Verified in code rather than assumed:

* We store **no mail-account credentials at all.** Email intake is PUSH, not
  pull: each org gets an inbound address token (`email_intakes.token`) and the
  provider POSTs the message to us. There is no mailbox we log into, so there is
  no mailbox password, OAuth token or refresh cycle to protect. The paperless-ngx
  finding (S2-6) is about a pull-based IMAP fetcher we do not have.
* The only mail secret is the **outbound** SMTP password
  (`config.smtp_password`), which lives in environment config and is never
  written to the database.
* A1's "no envelope-encryption module in this repo" is **wrong**:
  `app/core/keyvault.py` exists (ADR-0016) and SSO client secrets are sealed
  through it (`sso_config.py`, `oidc.py`). If we ever add IMAP pull, the seam is
  already there — `keyvault.seal` with a context-bound AAD.

L-3 is therefore closed, not deferred.
