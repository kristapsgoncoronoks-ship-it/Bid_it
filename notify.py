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

    # documents stuck in the intake queue.
    try:
        import waiting_room as WR
        cnt = WR.counts()
        stuck = (cnt.get("failed") or 0) + (cnt.get("held") or 0)
        if stuck:
            sections.append(("Intake queue",
                             [f"{stuck} document(s) stuck in intake "
                              f"(failed={cnt.get('failed', 0)}, held={cnt.get('held', 0)}) "
                              f"— review on the queue page"]))
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
            except Exception:
                pass   # plain server / already TLS — best effort, not fatal
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
# SENT is truthy; NOOP/FAILED are falsy, so the legacy `if send_digest()` /
# `is True`/`is False` contract is preserved for existing callers.
SENT, NOOP, FAILED = True, False, "failed"


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


if __name__ == "__main__":
    # offline smoke: render only (no send), so nothing leaves the machine.
    t, h = render_digest()
    print(t or "(nothing outstanding)")
