"""email_intake.py — AUTOMATED INVOICE INBOUND over an email inbox.

Suppliers email invoices (PDF, structured XML e-invoices, or hybrid Factur-X/ZUGFeRD PDFs, or
ZIPs). This module polls a configured IMAP mailbox, pulls the invoice attachments, and feeds
each into the SAME intake queue the web upload uses (`waiting_room.enqueue`) — so capture,
dedup (SHA-256), the embedded-XML-first probe and human review are byte-identical to a manual
upload. Less manual upload = more value (roadmap #1; ViDA makes structured inbound the norm).

OPT-IN / default-OFF (`email_intake_enabled`). The mailbox password is sealed at rest via
`keyvault` envelope encryption (same custody as portal credentials). The mailbox provider is
PLUGGABLE (real IMAP / a test provider) so the poll logic is testable without a live server.
Runs on the worker tier, never inline in a web request. Best-effort: never raises.
"""
import base64
import os

import applog

log = applog.get("email_intake")

_SETTING_PREFIX = "email_intake_"
_AAD = "email_intake"                                  # keyvault context binding for the password
_ALLOWED_EXT = {".pdf", ".xml", ".zip"}                # the intake formats (e-invoice / PDF / ZIP)
_MAX_BYTES = 25 * 1024 * 1024                          # 25 MiB per attachment cap
_DEFAULT_FOLDER = "INBOX"
_DEFAULT_PORT = 993


def _get(key, default=""):
    try:
        import auth
        v = auth.get_setting(_SETTING_PREFIX + key, default)
        return default if v in (None, "") else v
    except Exception as e:
        log.warning("setting read %s failed: %s", key, e)
        return default


def enabled():
    return str(_get("enabled", "off")).lower() not in ("off", "0", "false", "no", "")


def config():
    """The mailbox config for the UI — NEVER returns the password (only whether one is set)."""
    return {"enabled": enabled(),
            "host": _get("host"), "port": _get("port", str(_DEFAULT_PORT)),
            "user": _get("user"), "folder": _get("folder", _DEFAULT_FOLDER),
            "ssl": str(_get("ssl", "on")).lower() not in ("off", "0", "false", "no"),
            "has_password": bool(_get("pw"))}


def set_config(values, password=None, clear_password=False, actor=None):
    """Persist the mailbox config. `password` (when given non-blank) is SEALED via keyvault and
    stored; a blank password leaves the stored one untouched; clear_password removes it.
    Returns (True,"") or (False,error). Never raises."""
    try:
        import auth
        for k in ("enabled", "host", "port", "user", "folder", "ssl"):
            if k in values:
                auth.set_setting(_SETTING_PREFIX + k, str(values.get(k) or ""))
        if clear_password:
            auth.set_setting(_SETTING_PREFIX + "pw", "")
        elif password:
            import keyvault
            blob = keyvault.seal(password, aad=_AAD)
            auth.set_setting(_SETTING_PREFIX + "pw",
                             base64.b64encode(blob).decode("ascii"))
        return True, ""
    except Exception as e:
        log.warning("set_config failed: %s", e)
        return False, f"could not save the mailbox config ({str(e)[:60]})"


def _password():
    raw = _get("pw")
    if not raw:
        return ""
    try:
        import keyvault
        return keyvault.open(base64.b64decode(raw), aad=_AAD)
    except Exception as e:
        log.warning("password unseal failed: %s", e)
        return ""


# --------------------------------------------------------------------- mailbox provider (IMAP)
class _Msg:
    """A fetched message: a stable uid, the subject, and [(filename, bytes), ...] attachments."""
    __slots__ = ("uid", "subject", "attachments")

    def __init__(self, uid, subject, attachments):
        self.uid = uid
        self.subject = subject
        self.attachments = attachments


class ImapProvider:
    """Reads UNSEEN messages from an IMAP mailbox (does NOT auto-mark seen — uses BODY.PEEK so a
    crashed poll re-reads). `mark_seen` is called by `poll` only after the attachments are
    enqueued, so an attachment is never silently lost."""

    def __init__(self, host, port, user, password, folder=_DEFAULT_FOLDER, ssl=True):
        self.host, self.port, self.user = host, int(port or _DEFAULT_PORT), user
        self.password, self.folder, self.ssl = password, folder or _DEFAULT_FOLDER, ssl
        self._con = None

    def _connect(self):
        import imaplib
        self._con = (imaplib.IMAP4_SSL(self.host, self.port) if self.ssl
                     else imaplib.IMAP4(self.host, self.port))
        self._con.login(self.user, self.password)
        self._con.select(self.folder)
        return self._con

    def fetch(self, max_messages=50):
        import email as _email
        con = self._connect()
        try:
            typ, data = con.uid("search", None, "UNSEEN")
            if typ != "OK":
                return
            uids = (data[0] or b"").split()[:max_messages]
            for uid in uids:
                typ, md = con.uid("fetch", uid, "(BODY.PEEK[])")   # PEEK = don't set \Seen
                if typ != "OK" or not md or not md[0]:
                    continue
                msg = _email.message_from_bytes(md[0][1])
                atts = []
                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    fn = part.get_filename()
                    if not fn:
                        continue
                    payload = part.get_payload(decode=True)
                    if payload:
                        atts.append((fn, payload))
                yield _Msg(uid.decode("ascii", "ignore"),
                           str(msg.get("Subject") or ""), atts)
        finally:
            try:
                con.logout()
            except Exception:
                pass

    def mark_seen(self, uid):
        try:
            self._connect().uid("store", uid.encode() if isinstance(uid, str) else uid,
                                "+FLAGS", "(\\Seen)")
        except Exception as e:
            log.warning("mark_seen(%s) failed: %s", uid, e)


def _imap_provider():
    c = config()
    if not (c["host"] and c["user"]):
        return None
    pw = _password()
    if not pw:
        return None
    return ImapProvider(c["host"], c["port"], c["user"], pw, c["folder"], c["ssl"])


# ------------------------------------------------------------------------------------- the poll
def poll(provider=None, max_messages=50, user="email"):
    """Fetch invoice attachments from the mailbox and ENQUEUE each into the intake queue (same
    path + SHA-256 dedup as a web upload). When `provider` is None, builds the configured IMAP
    provider and requires the feature to be enabled. Returns a summary dict; never raises.
      {messages, enqueued, skipped, errors}  or  {skipped: reason}
    """
    if provider is None:
        if not enabled():
            return {"skipped": "email intake is disabled"}
        provider = _imap_provider()
        if provider is None:
            return {"skipped": "email intake is not configured (host / user / password)"}
    try:
        import waiting_room as IQ
    except Exception as e:
        log.warning("waiting_room import failed: %s", e)
        return {"skipped": f"queue unavailable ({e})"}

    messages = skipped = errors = duplicates = 0
    seen_jobs = set()
    try:
        for msg in provider.fetch(max_messages):
            messages += 1
            for fn, data in (getattr(msg, "attachments", None) or []):
                ext = os.path.splitext(fn or "")[1].lower()
                if ext not in _ALLOWED_EXT or not data or len(data) > _MAX_BYTES:
                    skipped += 1
                    continue
                try:
                    jid, _st = IQ.enqueue(data, fn, user=user)   # SHA-dedups; re-fetch = no-op
                    if jid in seen_jobs:
                        duplicates += 1
                    else:
                        seen_jobs.add(jid)
                except Exception as e:
                    errors += 1
                    log.warning("enqueue %r failed: %s", fn, e)
            try:
                provider.mark_seen(msg.uid)
            except Exception as e:
                log.warning("mark_seen failed: %s", e)
    except Exception as e:
        log.warning("poll failed: %s", e)
        errors += 1
    return {"messages": messages, "enqueued": len(seen_jobs), "duplicates": duplicates,
            "skipped": skipped, "errors": errors}
