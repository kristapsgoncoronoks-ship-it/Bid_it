"""Mailgun inbound-route webhook authentication (E1.6).

Mailgun signs every webhook call (including inbound-route delivery) with an
HMAC-SHA256 over `timestamp + token`, keyed by the account's signing key
(https://documentation.mailgun.com/... "Webhooks" — Securing Webhooks). This
module holds ONLY the pure verification logic — no I/O, no FastAPI, no DB — so
it is trivially unit-testable and reusable if a second Mailgun-fronted webhook
is ever added.

Two independent checks compose the gate:
  1. `verify_signature` — proves the request was actually signed by the holder
     of the account's signing key (constant-time compare; never `==`, which
     leaks timing information proportional to the matching prefix length).
  2. `is_fresh` — proves the signed request is recent. Without this, a captured
     valid (timestamp, token, signature) triple could be replayed indefinitely
     — the signature alone says "signed by Mailgun", not "signed just now".

Both fail CLOSED: a missing/malformed field is treated as invalid, never as
"skip this check".
"""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_signature(*, token: str, timestamp: str, signature: str, signing_key: str) -> bool:
    """True iff `signature` is the correct HMAC-SHA256(signing_key, timestamp+token)
    hex digest. Constant-time compare (`hmac.compare_digest`) so neither the
    length nor a matching prefix of a wrong guess is observable via timing."""
    if not token or not timestamp or not signature or not signing_key:
        return False
    expected = hmac.new(
        key=signing_key.encode("utf-8"),
        msg=(timestamp + token).encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def is_fresh(timestamp: str, *, max_age_seconds: int = 300, now: float | None = None) -> bool:
    """True iff `timestamp` (a Unix epoch string, Mailgun's format) is within
    `max_age_seconds` of now and not itself in the future beyond a small clock-
    skew allowance. Replay protection: a signature is valid crypto forever, so
    freshness is what actually bounds the replay window."""
    if not timestamp:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    ref = now if now is not None else time.time()
    skew_allowance = 60  # tolerate modest clock drift between Mailgun and us
    if ts > ref + skew_allowance:
        return False
    return (ref - ts) <= max_age_seconds
