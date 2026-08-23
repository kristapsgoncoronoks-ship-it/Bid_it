"""iCalendar (RFC 5545) rendering for the personal schedule feed (WO-B B2).

Deliberately tiny: VCALENDAR + one VEVENT per assignment, UTC times, text
escaped per the RFC, lines CRLF-terminated and folded at 74 octets. No
library — the subset we emit is small enough that owning it beats a
dependency, and the tests pin the exact wire format.

Status mapping: planned → TENTATIVE, confirmed/done → CONFIRMED; cancelled
assignments are excluded by the caller (a cancelled visit disappearing from
the phone is the behaviour a field worker expects).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.project_assignment import ProjectAssignment

_PRODID = "-//InvoiceIQ//Schedule//EN"


def _esc(text: str) -> str:
    """RFC 5545 §3.3.11 TEXT escaping."""
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _utc(dt: datetime) -> str:
    # SQLite hands back naive datetimes; they were stored as UTC (the API
    # normalizes on write), so a missing tzinfo means UTC, not local time.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> list[str]:
    """Fold long content lines (RFC 5545 §3.1: continuation = CRLF + space)."""
    out = []
    while len(line.encode()) > 74:
        # Cut at a safe character boundary ≤ 74 bytes.
        cut = 74
        while len(line[:cut].encode()) > 74:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return out


def render(
    assignments: list[ProjectAssignment],
    project_names: dict[str, str],
    *,
    calendar_name: str = "Work schedule",
    now: datetime | None = None,
) -> str:
    """The complete .ics document, CRLF line endings included."""
    stamp = _utc(now or datetime.now(UTC))
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(calendar_name)}",
    ]
    for a in assignments:
        summary = project_names.get(a.project_id) or "Assignment"
        status = "TENTATIVE" if a.status == "planned" else "CONFIRMED"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{a.id}@invoiceiq",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(a.starts_at)}",
            f"DTEND:{_utc(a.ends_at)}",
            f"SUMMARY:{_esc(summary)}",
            f"STATUS:{status}",
        ]
        if a.note:
            lines.append(f"DESCRIPTION:{_esc(a.note)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    folded: list[str] = []
    for line in lines:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"
