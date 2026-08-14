"""Inbound-channel health (H-2) — is the pipe still open, and how would we know?

THE FAILURE THIS EXISTS TO CATCH
--------------------------------
An inbound channel can die completely while every dashboard stays green. The
mechanism is always the same: success is inferred from a DOCUMENT COUNT, so
"nothing arrived today" and "nothing could get through today" produce identical
output, and the operator's natural heuristic ("it's quiet, must be a slow week")
never fires. The only cure is to record the outcome of each delivery ATTEMPT
independently of whether documents came out of it.

PESSIMISTIC-FIRST, AND WHY
--------------------------
`begin_attempt` increments `consecutive_failures` and commits BEFORE any document
work starts. Success is recorded afterwards, and joins the caller's transaction —
so it lands only if the document work itself commits.

That ordering is the whole guarantee. If the request crashes, times out, or is
rolled back halfway, the health row still says the attempt did not succeed. The
naive ordering (record the outcome at the end) is silent about exactly the
deliveries that went most wrong, because the code that would have recorded them
is the code that did not run.

A REJECTED DOCUMENT IS NOT A BROKEN CHANNEL
-------------------------------------------
A delivery whose attachments were all refused by the security gate is a SUCCESS
here. The channel did its job — the message reached us. Counting it as a channel
failure would light up an alarm about the pipe when the problem is the contents,
and send the operator to the wrong screen. Refused documents are H-1's subject
(`capture_failures`), and the two must not be conflated.

WHAT WE CANNOT ATTRIBUTE, WE DO NOT INVENT
------------------------------------------
A delivery rejected at AUTHENTICATION has no tenant: the inbound endpoint checks
the shared secret before resolving the address token, deliberately, and returns
one indistinguishable 401 for a bad secret and an unknown token so the endpoint
cannot be used to enumerate live addresses. Recording those against a guessed org
would be a fabrication, so they are not recorded here at all — they surface in
the platform logs. The tenant-visible consequence is still caught, from the other
side: a channel whose secret was rotated simply stops succeeding, and
`last_success_at` going stale is precisely the signal `status_for` reports.

STALENESS IS NOT GUESSED
------------------------
`expected_cadence_days` is NULL until someone states it. With no stated cadence
we report the elapsed time as a fact and decline to call the channel broken —
one customer's fortnight of quiet is another's outage, and an alarm nobody trusts
is worse than no alarm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inbound_channel_health import InboundChannelHealth

# The inbound channels we actually have. `email` is the per-org inbound address
# an email provider's webhook delivers to. Direct upload is deliberately NOT a
# channel here: a human is watching a browser tab when it fails, so it cannot die
# silently, and listing it would pad the screen with a row that is always green.
CHANNEL_EMAIL = "email"
CHANNELS = (CHANNEL_EMAIL,)

# Classified failure kinds. Closed vocabulary — never free-form prose.
ERR_AUTH = "auth"  # attributable auth failure (token known, credential wrong)
ERR_NOT_ACTIVATED = "not_activated"  # the module is off for this tenant
ERR_MALFORMED = "malformed_delivery"  # the provider sent something we cannot read
ERR_INCOMPLETE = "incomplete"  # started, never reported an outcome (crash/timeout)
ERR_INTERNAL = "internal"

# STICKY kinds do not fix themselves — a rotated credential and a deactivated
# module stay broken until a human acts, so one occurrence is already the alarm.
# Everything else may be a blip, and alarms after a run of them.
STICKY_KINDS = frozenset({ERR_AUTH, ERR_NOT_ACTIVATED})
TRANSIENT_FAILURE_ALARM_AT = 3

# Health states, most to least serious.
STATE_FAILING = "failing"
STATE_SILENT = "silent"
STATE_OK = "ok"
STATE_NEVER_USED = "never_used"

_ERROR_TEXT: dict[str, str] = {
    ERR_AUTH: "Deliveries to this address are being rejected as unauthenticated.",
    ERR_NOT_ACTIVATED: "Email intake is switched off for this workspace, so deliveries "
    "are refused.",
    ERR_MALFORMED: "The email provider sent something we could not read.",
    ERR_INCOMPLETE: "A delivery started and never finished — it may have timed out or "
    "hit an error.",
    ERR_INTERNAL: "A delivery failed on our side.",
}


def error_text(kind: str | None) -> str | None:
    return _ERROR_TEXT.get(kind or "")


async def _row(db: AsyncSession, org_id: str, channel: str) -> InboundChannelHealth:
    row = await db.scalar(
        select(InboundChannelHealth).where(
            InboundChannelHealth.org_id == org_id,
            InboundChannelHealth.channel == channel,
        )
    )
    if row is None:
        row = InboundChannelHealth(org_id=org_id, channel=channel, consecutive_failures=0)
        db.add(row)
    return row


async def begin_attempt(db: AsyncSession, org_id: str, channel: str) -> None:
    """Record that a delivery attempt STARTED, pessimistically, and commit it.

    Commits on purpose, before the caller does any document work: a health record
    written at the end of the request is missing for exactly the deliveries that
    failed hardest. Until `record_success` runs, this attempt counts as a failure.
    """
    row = await _row(db, org_id, channel)
    now = datetime.now(UTC)
    row.last_attempt_at = now
    row.consecutive_failures = (row.consecutive_failures or 0) + 1
    row.last_error_kind = ERR_INCOMPLETE
    row.last_error_at = now
    row.last_error_detail = None
    await db.commit()


async def record_success(db: AsyncSession, org_id: str, channel: str) -> None:
    """Mark the attempt succeeded. Deliberately does NOT commit — it joins the
    caller's transaction, so the channel is only called healthy if the documents
    it delivered actually landed."""
    row = await _row(db, org_id, channel)
    row.last_success_at = datetime.now(UTC)
    row.consecutive_failures = 0
    row.last_error_kind = None
    row.last_error_detail = None


async def record_failure(
    db: AsyncSession, org_id: str, channel: str, kind: str, detail: str | None = None
) -> None:
    """Classify an attempt that failed for a KNOWN reason, and commit it.

    Commits because a classified failure has no document work to be atomic with —
    and because the paths that reach it (a module gate, a malformed payload) end
    the request immediately afterwards.
    """
    row = await _row(db, org_id, channel)
    now = datetime.now(UTC)
    if row.last_attempt_at is None:
        row.last_attempt_at = now
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
    row.last_error_kind = kind if kind in _ERROR_TEXT else ERR_INTERNAL
    row.last_error_at = now
    row.last_error_detail = (detail or None) and detail[:1000]
    await db.commit()


async def set_expected_cadence(
    db: AsyncSession, org_id: str, channel: str, days: int | None
) -> InboundChannelHealth:
    """State how often this org expects deliveries — the only thing that lets us
    call quiet 'broken'. `None` withdraws the claim rather than resetting it to a
    default, because there is no honest default."""
    row = await _row(db, org_id, channel)
    row.expected_cadence_days = days if (days is None or days > 0) else None
    await db.commit()
    await db.refresh(row)
    return row


@dataclass
class ChannelStatus:
    channel: str
    state: str
    # A sentence stating the situation POSITIVELY — "last successful delivery: 4
    # days ago" — because an empty list reads as "fine" and absence is the whole
    # signal here.
    headline: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    days_since_success: int | None
    consecutive_failures: int
    last_error_kind: str | None
    last_error_text: str | None
    last_error_at: datetime | None
    expected_cadence_days: int | None
    # True only when a cadence was STATED and the gap exceeds it. With no stated
    # cadence this is False and `state` is never `silent` — we report, we do not
    # accuse.
    overdue: bool


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _plural_days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _status(row: InboundChannelHealth | None, channel: str, now: datetime) -> ChannelStatus:
    if row is None or row.last_attempt_at is None:
        return ChannelStatus(
            channel=channel,
            state=STATE_NEVER_USED,
            headline="Nothing has ever been delivered to this address.",
            last_attempt_at=None,
            last_success_at=None,
            days_since_success=None,
            consecutive_failures=0,
            last_error_kind=None,
            last_error_text=None,
            last_error_at=None,
            expected_cadence_days=(row.expected_cadence_days if row else None),
            overdue=False,
        )

    days = None
    if row.last_success_at is not None:
        days = max(0, (now - _as_utc(row.last_success_at)).days)

    cadence = row.expected_cadence_days
    overdue = bool(cadence and days is not None and days > cadence)

    fails = row.consecutive_failures or 0
    kind = row.last_error_kind
    failing = fails > 0 and (kind in STICKY_KINDS or fails >= TRANSIENT_FAILURE_ALARM_AT)

    if failing:
        state = STATE_FAILING
        headline = error_text(kind) or "Deliveries to this address are failing."
        if days is not None:
            headline += f" Last successful delivery was {_plural_days(days)} ago."
        else:
            headline += " No delivery has ever succeeded."
    elif row.last_success_at is None:
        # Attempted, never succeeded, but not yet over the transient threshold.
        state = STATE_FAILING if fails else STATE_NEVER_USED
        headline = "Deliveries have been attempted but none has succeeded yet."
    elif overdue:
        state = STATE_SILENT
        headline = (
            f"No delivery for {_plural_days(days or 0)} — longer than the "
            f"{_plural_days(cadence or 0)} you expect."
        )
    else:
        state = STATE_OK
        headline = f"Last successful delivery: {_plural_days(days or 0)} ago."
        if cadence is None and (days or 0) > 0:
            # No cadence stated, so we report the gap and make no claim about it.
            headline += " No expected frequency is set, so nothing is overdue."

    return ChannelStatus(
        channel=channel,
        state=state,
        headline=headline,
        last_attempt_at=row.last_attempt_at,
        last_success_at=row.last_success_at,
        days_since_success=days,
        consecutive_failures=fails,
        last_error_kind=kind,
        last_error_text=error_text(kind),
        last_error_at=row.last_error_at,
        expected_cadence_days=cadence,
        overdue=overdue,
    )


async def status_for(db: AsyncSession, org_id: str) -> list[ChannelStatus]:
    """Health for EVERY known channel, including ones with no row yet — a channel
    that has never been used must still appear, or its absence from the list is
    indistinguishable from it being fine."""
    rows = {
        r.channel: r
        for r in await db.scalars(
            select(InboundChannelHealth).where(InboundChannelHealth.org_id == org_id)
        )
    }
    now = datetime.now(UTC)
    return [_status(rows.get(c), c, now) for c in CHANNELS]
