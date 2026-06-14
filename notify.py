"""
DIGEST MAILER — a periodic "what needs action" e-mail digest of the worklist,
expiring customer documents and any documents stuck in the intake queue.

WHY: the dashboard worklist (gaps #3/#5) and the expiring-document alerts (gap #1)
are only seen by someone who opens the app. This turns the same READ-ONLY signals
into a digest that can be e-mailed on a schedule so a stall is noticed off-screen.

It is strictly read-only — it never mutates or gates a claim, figure or document; it
only reports. Amounts are NET EUR (VAT excluded), quantised via money.f2.

TRANSPORT IS INJECTABLE — no live SMTP is required (or used) in tests. send_digest()
resolves a transport in this order:
  * an explicit `transport=` argument (tests inject a fake with .send(to, subject,
    html, text)), else
  * one built from admin SMTP settings (smtp_host/smtp_port/smtp_user/smtp_pass/
    smtp_from) using stdlib smtplib.
If no SMTP host or no recipients are configured the call is a logged no-op (never
raises). Every successful send is recorded on the import_log "notify" channel.
"""
import os
import datetime
from markupsafe import escape as esc

import applog
import money

WORKDIR = os.path.dirname(os.path.abspath(__file__))
log = applog.get("notify")

_AGING_DAYS = 120   # an unpaid submitted claim older than this needs chasing (mirror app)


# ---------------------------------------------------------------- read-only collection
def _digest_sections(year=None):
    """Pull the actionable signals, read-only, into a list of
    (heading, [line, ...]) sections. Never raises — a failing source is logged and
    contributes no section so the digest still goes out."""
    if year is None:
        year = datetime.date.today().year
    sections = []

    # claims blocked / unresolved / aging — same sources the dashboard worklist reads.
    try:
        import vat_refund as VR
        ov = VR.claims_overview(year)
        recs, _ = VR.recovery_report(str(year))
        blocked = [c for c in ov.get("to_submit", []) if not c.get("ready")]
        lines = []
        n_unres = 0
        for c in blocked:
            for iss in (c.get("issues") or []):
                if "unresolved invoice ref" in iss:
                    n_unres += 1
            why = ", ".join(c.get("issues") or []) or "not ready"
            lines.append(f"Unblock {c['entity']} · {c['country']} {c['period']} — {why}")
        if n_unres:
            lines.insert(0, f"Resolve UNMATCHED — {n_unres} blocked claim(s) with "
                            f"unresolved invoice ref(s)")
        if lines:
            sections.append(("Claims needing attention", lines))
        aging = []
        for r in recs:
            if r.get("status") in ("submitted", "approved") \
               and isinstance(r.get("age_days"), int) and r["age_days"] >= _AGING_DAYS:
                aging.append(f"Chase {r['entity']} · {r['country']} {r['period']} — "
                             f"€{money.f2(r.get('vat_eur') or 0):,.2f} submitted "
                             f"{r['age_days']}d ago, unpaid")
        if aging:
            sections.append(("Aging unpaid claims", aging))
    except Exception as e:
        log.warning("digest: claims section failed: %s", e)

    # filing/response deadlines at risk — the highest-urgency signal (missing the
    # statutory 30-Sep filing deadline forfeits the entire refund). Single source of
    # truth = vat_refund.approaching_deadlines.
    try:
        import vat_refund as VR
        lines = []
        for d in VR.approaching_deadlines(within_days=60):
            dl = d.get("deadline")
            when = (f"OVERDUE by {-d['days_left']}d" if d.get("overdue")
                    else f"{d['days_left']}d left")
            if d.get("kind") == "filing":
                lines.append(f"FILE {d['entity']} · {d['country']} {d['period']} — "
                             f"€{money.f2(d.get('vat_eur') or 0):,.2f} VAT, deadline "
                             f"{dl} ({when})")
            else:
                what = "document request" if d.get("code") == "2B" else "appeal"
                lines.append(f"RESPOND ({what}) {d['entity']} · {d['country']} "
                             f"{d['period']} — deadline {dl} ({when})")
        if lines:
            sections.append(("Filing deadlines approaching", lines))
    except Exception as e:
        log.warning("digest: filing-deadlines section failed: %s", e)

    # customer documents expired / expiring soon.
    try:
        import customer_master as CM
        ccon = CM.connect()
        try:
            docs = CM.expiring_documents(ccon, within_days=60)
        finally:
            ccon.close()
        lines = []
        for d in docs:
            left = d.get("days_left")
            when = ("EXPIRED" if isinstance(left, int) and left < 0
                    else f"expires in {left}d" if isinstance(left, int) else "expiring")
            where = f" ({d['country']})" if d.get("country") else ""
            lines.append(f"Renew {d['kind']} for {d['customer']}{where} — {when} "
                         f"({d['valid_until']})")
        if lines:
            sections.append(("Expiring customer documents", lines))
    except Exception as e:
        log.warning("digest: expiring-docs section failed: %s", e)

    # open document requests still awaiting their signed original (e.g. a PoA out for
    # signature). Informational chase only — not a gate.
    try:
        import customer_master as CM
        ccon = CM.connect()
        try:
            reqs = CM.pending_document_requests(ccon)
        finally:
            ccon.close()
        lines = []
        for d in reqs:
            ctry = f" ({d['refund_country']})" if d.get("refund_country") else ""
            flag = " — OVERDUE" if d.get("overdue") else ""
            lines.append(f"Awaiting signed {(d.get('kind') or '').replace('_', ' ')} for "
                         f"{d['customer']}{ctry} — sent {d.get('age_days', 0)}d ago{flag}")
        if lines:
            sections.append(("Pending document requests", lines))
    except Exception as e:
        log.warning("digest: document-requests section failed: %s", e)

    # documents stuck in the intake queue.
    try:
        import waiting_room as WR
        cnt = WR.counts()
        stuck = (cnt.get("failed") or 0) + (cnt.get("held") or 0)
        ilines = []
        if stuck:
            ilines.append(f"{stuck} document(s) stuck in intake "
                          f"(failed={cnt.get('failed', 0)}, held={cnt.get('held', 0)}) "
                          f"— review on the queue page")
        # a stalled/starved worker: the oldest still-flowing job is older than the SLO.
        h = WR.queue_health()
        if h.get("age_breach"):
            hrs = (h.get("oldest_pending_age_s") or 0) // 3600
            ilines.append(f"Oldest pending document is {hrs}h old — the intake worker "
                          f"may be stalled (SLO {WR.OLDEST_PENDING_SLO_HOURS}h).")
        if ilines:
            sections.append(("Intake queue", ilines))
    except Exception as e:
        log.warning("digest: intake section failed: %s", e)

    return sections


def render_digest(year=None):
    """Build the digest body as (plain_text, escaped_html). Every interpolated value
    is HTML-escaped in the html part. Returns ('', '') when there is nothing to report
    (callers may then skip the send)."""
    sections = _digest_sections(year)
    if not sections:
        return "", ""
    today = datetime.date.today().isoformat()
    text_parts = [f"Fleet Fuel & VAT — action digest ({today})",
                  "Amounts are NET EUR (VAT excluded).", ""]
    html_parts = [f"<h2>Fleet Fuel &amp; VAT — action digest "
                  f"({esc(today)})</h2>",
                  "<p>Amounts are NET EUR (VAT excluded).</p>"]
    for heading, lines in sections:
        text_parts.append(f"== {heading} ==")
        text_parts.extend(f"  - {ln}" for ln in lines)
        text_parts.append("")
        html_parts.append(f"<h3>{esc(heading)}</h3><ul>")
        html_parts.extend(f"<li>{esc(ln)}</li>" for ln in lines)
        html_parts.append("</ul>")
    return "\n".join(text_parts), "".join(html_parts)


# ---------------------------------------------------------------- transport
class _SmtpTransport:
    """A thin stdlib-smtplib transport built from admin settings. Only constructed
    when an SMTP host is configured; never instantiated by the tests (they inject
    their own fake transport)."""
    def __init__(self, host, port, user, password, sender):
        self.host, self.port = host, port
        self.user, self.password, self.sender = user, password, sender

    def send(self, to, subject, html, text):
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.sender or self.user or "noreply@localhost"
        msg["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(self.host, self.port or 25, timeout=30) as s:
            try:
                s.starttls()
            except Exception as e:
                log.debug("STARTTLS unavailable on %s (best effort): %s", self.host, e)  # plain server / already TLS — not fatal
            if self.user:
                s.login(self.user, self.password or "")
            s.send_message(msg)


def _settings_transport():
    """Build an SMTP transport from admin settings, or None if no host configured."""
    import auth
    host = (auth.get_setting("smtp_host") or "").strip()
    if not host:
        return None
    port = auth.get_setting("smtp_port") or 0
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 0
    return _SmtpTransport(host, port,
                          auth.get_setting("smtp_user"),
                          auth.get_setting("smtp_pass"),
                          auth.get_setting("smtp_from"))


def _recipients():
    import auth
    raw = auth.get_setting("notify_recipients") or ""
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


# ---------------------------------------------------------------- send
# send_digest outcomes — the scheduler distinguishes these so a broken SMTP is
# RETRIED (not silently stamped as done) and surfaced to the admin error log.
#   SENT   genuine success (a message went out)
#   NOOP   nothing to do this tick: no recipients / no transport configured, or
#          nothing outstanding to report — legitimately nothing to send
#   FAILED transport error (SMTP/connection) — should retry + be alerted on
# SENT is the ONLY truthy result; NOOP and FAILED are both falsy (and distinct via
# `==`), so a naive `if send_digest():` treats a transport FAILURE as "not sent"
# (safe) rather than success, and the legacy `is True`/`is False` contract holds.
SENT, NOOP, FAILED = True, False, None


def send_digest(transport=None, year=None):
    """Render and send the action digest. `transport` (with .send(to, subject, html,
    text)) is injected by tests; in production it is built from admin SMTP settings.

    Returns one of SENT (truthy), NOOP, or FAILED so the scheduler can tell a real
    SMTP failure (retry + alert) apart from a legitimate no-op (nothing to send).
    Never raises — failures are logged and reported via the FAILED return."""
    recipients = _recipients()
    if not recipients:
        log.info("notify: no recipients configured (notify_recipients) — skipping digest")
        return NOOP
    if transport is None:
        transport = _settings_transport()
    if transport is None:
        log.info("notify: no SMTP host configured (smtp_host) — skipping digest")
        return NOOP
    text, html = render_digest(year)
    if not text:
        log.info("notify: nothing outstanding — no digest sent")
        return NOOP
    subject = "Fleet Fuel & VAT — action digest"
    try:
        transport.send(recipients, subject, html, text)
    except Exception as e:
        log.warning("notify: digest send failed: %s", e)
        return FAILED
    try:
        import import_log
        import_log.log("notify", "action-digest", "success", actor="scheduler",
                       records=text.count("\n  - "),
                       message=f"sent to {len(recipients)} recipient(s)")
    except Exception as e:
        log.warning("notify: import_log record failed: %s", e)
    log.info("notify: digest sent to %d recipient(s)", len(recipients))
    return SENT


# ---------------------------------------------------------------- per-event alerts
# A digest is BATCHED on a cadence; some conditions warrant an IMMEDIATE "wake someone
# up" alert the moment they appear (DLQ growth, the intake worker stalling, a VAT
# filing deadline going overdue). send_alert is the one-shot equivalent of send_digest
# (same SENT/NOOP/FAILED contract); critical_alert is the per-EVENT engine that fires
# only when the critical set CHANGES (fingerprint dedup) so it never spams every tick.

def send_alert(subject, lines, transport=None):
    """Send ONE immediate alert built from `subject` + `lines` (a list of strings),
    as a plain-text + escaped-HTML body. `transport` (with .send(to, subject, html,
    text)) is injected by tests; in production it is built from admin SMTP settings.

    Returns one of SENT (truthy), NOOP, or FAILED — mirrors send_digest so the caller
    can tell a real SMTP failure (retry + alert) apart from a legitimate no-op (no
    transport / no recipients / nothing to say). Never raises."""
    if not lines:
        log.info("notify: empty alert — nothing to send")
        return NOOP
    recipients = _recipients()
    if not recipients:
        log.info("notify: no recipients configured (notify_recipients) — skipping alert")
        return NOOP
    if transport is None:
        transport = _settings_transport()
    if transport is None:
        log.info("notify: no SMTP host configured (smtp_host) — skipping alert")
        return NOOP
    today = datetime.date.today().isoformat()
    text = "\n".join([subject, f"({today})", ""] + [f"  - {ln}" for ln in lines])
    html = (f"<h2>{esc(subject)}</h2><p>({esc(today)})</p><ul>"
            + "".join(f"<li>{esc(ln)}</li>" for ln in lines) + "</ul>")
    try:
        transport.send(recipients, subject, html, text)
    except Exception as e:
        log.warning("notify: alert send failed: %s", e)
        return FAILED
    log.info("notify: alert sent to %d recipient(s): %s", len(recipients), subject)
    return SENT


def send_test(transport=None):
    """Send a one-off test e-mail to verify the SMTP relay configuration. Returns the
    same SENT/NOOP/FAILED tri-state as send_alert. Never raises."""
    return send_alert("Fleet Fuel & VAT — test email",
                      ["This is a test of the SMTP relay configuration.",
                       f"Sent {datetime.date.today().isoformat()}."],
                      transport)


def critical_events():
    """READ-ONLY snapshot of the CURRENT critical conditions worth an immediate alert.
    Returns a list of (severity, line) tuples — genuine "wake someone up" events only.
    Never raises: each source is independently guarded so a failing source contributes
    nothing rather than sinking the whole check."""
    events = []

    # intake queue: a non-empty dead-letter queue (terminal failed/held jobs needing a
    # human redrive) and/or the oldest pending job breaching the worker-progress SLO.
    try:
        import waiting_room as WR
        h = WR.queue_health()
        dlq = h.get("dlq") or 0
        if dlq > 0:
            events.append(("critical", f"{dlq} document(s) in the dead-letter queue"))
        if h.get("age_breach"):
            events.append(("critical", "oldest pending document exceeds the SLO"))
    except Exception as e:
        log.warning("critical_events: intake source failed: %s", e)

    # overdue filing deadlines: a statutory 30-Sep filing deadline already missed forfeits
    # the whole refund — the single most urgent thing in the system.
    try:
        import vat_refund as VR
        for d in VR.approaching_deadlines(within_days=0):
            if d.get("kind") == "filing" and d.get("overdue"):
                events.append(("critical",
                               f"OVERDUE: FILE {d.get('entity')} · {d.get('country')} "
                               f"{d.get('period')} — deadline {d.get('deadline')}"))
    except Exception as e:
        log.warning("critical_events: filing-deadline source failed: %s", e)

    return events


def _fingerprint(lines):
    """A deterministic fingerprint of the current critical SET (order-independent), so
    an unchanged set dedups and any change re-alerts. Empty set → '' (re-arm sentinel)."""
    import hashlib
    if not lines:
        return ""
    joined = "\n".join(sorted(lines))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def critical_alert(transport=None):
    """Per-EVENT alert engine: e-mail the moment a NEW critical condition appears, but
    NOT every tick while it persists. Computes critical_events(), fingerprints the set
    and compares it to the stored fingerprint (`notify_last_alert_fp`):

      * new/changed critical set → send_alert(...); on SENT store the new fingerprint,
        on FAILED do NOT store (so it retries next tick) and return FAILED;
      * no critical events → store '' (re-arm, so a later re-breach re-alerts) → NOOP;
      * unchanged set already alerted → NOOP (dedup, no re-spam).

    Returns SENT / NOOP / FAILED. Never raises."""
    try:
        import auth
        events = critical_events()
        lines = [ln for _sev, ln in events]
        fp = _fingerprint(lines)
        stored = auth.get_setting("notify_last_alert_fp", "") or ""
        if not lines:
            # quiet now — re-arm so a later re-breach fires again.
            if stored != "":
                auth.set_setting("notify_last_alert_fp", "")
            return NOOP
        if fp == stored:
            return NOOP            # same critical set already alerted — dedup
        res = send_alert("Fleet Fuel & VAT — CRITICAL", lines, transport)
        if res == SENT:
            auth.set_setting("notify_last_alert_fp", fp)
        # on FAILED/NOOP leave the stored fingerprint untouched so it retries next tick.
        return res
    except Exception as e:
        log.warning("critical_alert failed: %s", e)
        return FAILED


if __name__ == "__main__":
    # offline smoke: render only (no send), so nothing leaves the machine.
    t, h = render_digest()
    print(t or "(nothing outstanding)")
