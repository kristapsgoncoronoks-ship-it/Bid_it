"""SEC-005 (audit 2026-09-05) — capability tokens never reach a log line.

The client portal (`/portal/{token}`) and the calendar feed
(`/calendar/feed/{token}.ics`) carry their credential in the URL path: that is
what a magic link is. Logged verbatim, every access-log line was a live key —
in the app's own structured line, in nginx's origin log, and in whatever ships
those logs onward. Both loggers now mask the token and keep the surface.
"""

from __future__ import annotations

import logging
import pathlib
import re

import pytest

from app.core.observability import redact_path

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_redact_path_masks_the_token_and_keeps_the_surface():
    assert redact_path("/api/v1/portal/k3yK3Y-abc") == "/api/v1/portal/<redacted>"
    assert (
        redact_path("/api/v1/portal/k3yK3Y-abc/documents/doc-9")
        == "/api/v1/portal/<redacted>/documents/doc-9"
    )
    assert (
        redact_path("/api/v1/calendar/feed/t0k3n.ics?u=1")
        == "/api/v1/calendar/feed/<redacted>.ics?u=1"
    )
    # Untouched: everything that carries no capability token.
    for p in ("/api/v1/invoices", "/api/v1/portal", "/health/ready", "", None):
        assert redact_path(p) == p


@pytest.mark.asyncio
async def test_the_access_log_line_for_a_portal_request_carries_no_token(client, caplog):
    token = "SECRETtoken0123456789"
    with caplog.at_level(logging.INFO, logger="invoiceiq"):
        await client.get(f"/api/v1/portal/{token}")
    lines = [r for r in caplog.records if r.getMessage() == "request"]
    assert lines, "the access-log line was not emitted"
    for rec in lines:
        assert token not in str(rec.__dict__)
        assert getattr(rec, "path", "").startswith("/api/v1/portal/<redacted>")


def test_nginx_masks_the_same_paths_in_its_origin_log():
    """The origin log is nginx's, not the app's: the map + log_format must exist
    and the map's expressions must mask the same two shapes. The regexes are
    exercised here with Python's engine, which agrees with PCRE for these
    patterns."""
    conf = (REPO / "frontend" / "nginx.prod.conf").read_text()
    assert "map $request_uri $log_request_uri" in conf
    assert "log_format redacted" in conf
    assert re.search(r"access_log\s+\S+\s+redacted;", conf), "the HTTPS server does not use it"
    patterns = re.findall(r'"~(\^[^"]+\$)"\s+"([^"]+)"', conf)
    assert len(patterns) == 2, patterns
    portal_re, calendar_re = (re.compile(p.replace("?<", "?P<")) for p, _ in patterns)
    m = portal_re.match("/api/v1/portal/SECRETtoken/documents/doc-1")
    assert m and m.group("pre") == "/api/v1/portal/" and m.group("post") == "/documents/doc-1"
    m = calendar_re.match("/api/v1/calendar/feed/SECRETtoken.ics?u=1")
    assert m and m.group("cpre") == "/api/v1/calendar/feed/" and m.group("cpost") == "?u=1"
    assert not portal_re.match("/api/v1/invoices")
