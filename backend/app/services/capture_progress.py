"""Honest progress for a capture that takes minutes (WO-X, X2).

WHY THIS EXISTS
---------------
`GET /invoices/upload/{run_id}` reported four words: queued, running, parsed,
failed. A 3-page text-layer PDF and a 40-page scan therefore look *identical*
while they run — one finishes in under a second, the other takes minutes, and
the screen says the same thing about both. The person watching cannot tell a
long job from a hung one, so they reload, re-upload, or open a support ticket
about a document that was being read correctly the whole time.

The fix is not a nicer spinner. It is to publish what the parser already knows
and was throwing away: which phase it is in, and — on the only phase that is
actually slow — which page of how many.

THE CONTRACT
------------
`stage` is a code from the closed vocabulary in `STAGES`, in the order the
parser passes through them:

    queued  → the run exists, no worker has picked it up
    reading → pulling text out of the document (fast: text layer, XML, CSV)
    ocr     → rendering and recognising pages (slow; the only counted phase)
    interpreting → turning recovered text into a draft invoice
    done    → the parse finished (parsed or failed; `status` says which)

`stage` is NULL on a run recorded before this module existed. That is read as
"we do not know", never back-filled to a guess — a stage invented for a
historical row would be indistinguishable from one that was measured.

WHY THE PERCENT IS OFTEN NULL
-----------------------------
`percent` is reported ONLY where it is measured — during OCR, where the page
count is known and pages complete one at a time. Every other phase returns
None and the screen shows an indeterminate progress state.

The temptation is to map the stages onto invented numbers (reading = 10%,
interpreting = 90%) so the bar always moves. That is worse than no bar: the
number would be a claim about remaining time that nothing measured, and a bar
sitting at 90% for four minutes teaches the operator that the number lies. An
honest "Reading page 12 of 40" carries more information than a fabricated 47%.

THE SINK
--------
Parsing runs on a worker THREAD (`run_in_threadpool`) so it cannot touch the
async session itself. `report()` therefore writes to a sink installed in a
contextvar by whoever is driving the parse — `extraction.extract_upload`
installs one that persists to the run row; every other caller of the parser
(email intake, the synchronous test paths) installs nothing and `report()` is a
no-op. The parser stays ignorant of persistence; this module owns the seam.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextvars import ContextVar, Token

log = logging.getLogger("invoiceiq.capture")

# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #

QUEUED = "queued"
READING = "reading"
OCR = "ocr"
INTERPRETING = "interpreting"
DONE = "done"

#: Ordered. The order IS part of the contract: progress never goes backwards,
#: and `is_forward` is what enforces it at the one place that writes.
STAGES = (QUEUED, READING, OCR, INTERPRETING, DONE)
_RANK = {stage: i for i, stage in enumerate(STAGES)}

#: A sink takes (stage, pages_done, pages_total) and persists it. Sync, because
#: the parser that calls it is sync and runs off the event loop.
Sink = Callable[[str, int, int | None], None]

_sink: ContextVar[Sink | None] = ContextVar("capture_progress_sink", default=None)


def install(sink: Sink) -> Token:
    """Route `report()` calls made on this context (and any thread it is copied
    into) to `sink`. Returns a token for `uninstall`."""
    return _sink.set(sink)


def uninstall(token: Token) -> None:
    _sink.reset(token)


def report(stage: str, *, pages_done: int = 0, pages_total: int | None = None) -> None:
    """Publish a progress observation from inside the parser.

    Best-effort in the strongest sense: a progress write must never be able to
    fail a parse. A document that was read correctly but whose progress row
    could not be updated is a *successful* capture, and treating it as anything
    else would trade a real outcome for a cosmetic one.
    """
    sink = _sink.get()
    if sink is None:
        return
    if stage not in _RANK:  # pragma: no cover - guards a typo at a call site
        log.warning("capture progress: unknown stage %r ignored", stage)
        return
    try:
        sink(stage, pages_done, pages_total)
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("capture progress report failed (%s): %s", stage, exc)


def is_forward(current: str | None, incoming: str) -> bool:
    """May a run at `current` move to `incoming`?

    Only forwards. A parser that falls back — the text layer came up short, so
    OCR runs — must not be able to make the screen say an earlier phase again,
    and a stale report arriving late must not undo a later one. An unknown
    `current` (NULL: a run from before this contract) accepts anything, because
    there is nothing to contradict.
    """
    if current is None or current not in _RANK:
        return True
    return _RANK[incoming] >= _RANK[current]


def percent(
    stage: str | None, pages_done: int, pages_total: int | None, *, status: str
) -> int | None:
    """The MEASURED completion percentage, or None when nothing measured it.

    Returns 100 only for a run that actually finished parsing, and never 100
    while work remains — a bar that reaches the end before the page does is the
    specific lie this whole module exists to avoid.
    """
    if status in ("parsed", "saved"):
        return 100
    if status == "failed":
        return None  # it stopped; how far it got is not progress toward anything
    if stage == OCR and pages_total and pages_total > 0:
        return min(99, round(100 * max(0, pages_done) / pages_total))
    return None


def on_loop_thread() -> bool:
    """True when called on the event-loop thread.

    The DB-backed sink schedules its write onto the loop and BLOCKS until it
    lands, which is correct from a worker thread and a deadlock from the loop
    thread. Sinks use this to refuse rather than hang.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
