"""
AI DOCUMENT ASSISTANT (chat-with-document) — an OPT-IN, ADVISORY-ONLY assistant that lets
an operator ask free-form questions ABOUT a document's already-extracted, DERIVED data.

This is the conversational sibling of `ai_review.py` (the advisory AI REVIEW assistant) and
honours the SAME strict privacy posture:

  * DEFAULT OFF (`enabled()` below). The assistant is hidden / inert unless BOTH an admin
    setting `ai_doc_chat_enabled` is ON *and* an AI backend is actually configured (the same
    backend the extractor/reviewer use, `ai_review.resolve_backend() != "none"`). With either
    missing NO network call is made — `ask()` returns a graceful "disabled" message.

  * DERIVED DATA ONLY. `build_context()` ships ONLY the document's DERIVED/structured data —
    a handful of draft header fields + per-line {invoice_no,date,country,currency,net,vat},
    or, for a REGISTERED invoice, its transaction summary rows. It NEVER ships the raw PDF
    bytes, NEVER an IBAN / bank account, NEVER a stored secret. We reuse ai_review's recursive
    redactor (`ai_review._redact` + `ai_review.REDACT_FIELDS`), which strips every bank/secret
    key (iban, swift, account, beneficiary, password, secret, api_key, …) AND every key whose
    name starts with '_' (so `_pdf_bytes` / any private field can never leak). The payload is a
    minimized derived dict — there is no path here that even READS the PDF or a credential.

  * ADVISORY ONLY — by construction. Everything in this module READS derived data and RETURNS
    text. There is NO write to a product DB, NO figure / status / lock / fee / payment mutation.
    The only DB writes are the chat-history rows in this module's OWN app-owned `ai_chat.db`.

  * Never log a plaintext secret. Transient backend errors are handled like extract.py /
    ai_review (mapped via `is_transient_error`, never crashing the caller); a hard failure
    returns a graceful message and is logged to applog (the app route also mirrors handled
    failures to the admin error log per the project's error conventions).

Public surface (all best-effort / never-raise to the caller):
  * `enabled()`                         -> bool (setting AND a configured backend)
  * `build_context(subject)`            -> the DERIVED, secret-stripped payload dict
  * `ask(context, question, history)`   -> answer text via the existing AI backend seam
  * `start_chat` / `record_message` / `get_chat` / `messages_for` — chat-history helpers.
"""
import os
import json
import sqlite3
import datetime

import applog
import db_migrate
import tenancy
import audit
# Reuse ai_review's redactor + secret allow/deny list and the extractor's transient taxonomy
# + per-backend HTTP shapes. We DO NOT reuse its reviewer PROMPT — this module is a free-form
# Q&A assistant, so it has its own ASSISTANT_PROMPT below.
import ai_review
from extract import TransientExtractionError, is_transient_error

log = applog.get("ai_assistant")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned runtime DB (a module attribute so tests can monkeypatch the path).
DB = f"{WORKDIR}/ai_chat.db"

# The setting that turns the assistant ON (admin-togglable, default OFF). It REUSES the
# extractor/reviewer backend selection (`ai_review_backend`) for the actual model + key, so
# there is no second backend to configure — this flag only opts the chat surface in.
SETTING = "ai_doc_chat_enabled"


ASSISTANT_PROMPT = (
    "You are an ADVISORY assistant answering an operator's questions ABOUT one already-"
    "extracted invoice/transaction document. You are given ONLY DERIVED, structured data "
    "(header fields + line items) — never the original PDF, never any bank account or secret. "
    "Answer plainly and concisely from that data. You CANNOT and MUST NOT change any figure, "
    "status, lock, fee, or payment — you are read-only commentary. If a question needs data "
    "you were not given, say so rather than guessing. Do not invent VAT IDs, IBANs, or totals."
)


# ---------------------------------------------------------------- enablement
def backend(setting=None):
    """The configured backend ('none' when unset). Delegates to ai_review.resolve_backend so
    the chat assistant shares the extractor/reviewer backend selection (one key set)."""
    if setting:
        return setting
    return ai_review.resolve_backend()


def enabled():
    """True only when BOTH the admin opt-in setting is ON and an AI backend is configured.
    Never raises -> False (fail toward OFF / no network call)."""
    try:
        import auth
        on = str(auth.get_setting(SETTING, "off") or "off").lower() in ("on", "1", "true", "yes")
        return bool(on) and backend() != "none"
    except Exception as e:
        log.warning("enabled() check failed — defaulting to OFF: %s", e)
        return False


# ---------------------------------------------------------------- derived context
def _num(x):
    try:
        import money
        return money.f2(x)
    except Exception:
        return None


def build_context(subject):
    """Build the DERIVED, secret-stripped payload for the assistant from a `subject` dict.

    `subject` is a small descriptor the caller assembles, e.g.::

        {"kind": "draft"|"invoice", "supplier":..., "supplier_vat":...,
         "statement_ref":..., "statement_date":..., "currency":..., "customer":...,
         "lines": [{"invoice_no","date","country","currency","net","vat"}, ...]}

    We project ONLY the safe, derived fields and then run ai_review's recursive redactor as a
    BELT-AND-BRACES final guard: it drops every bank/secret key (REDACT_FIELDS) and any key
    starting '_' — so even if a caller hands us a dict carrying an IBAN/account/secret, it can
    never reach the model. Pure: does not mutate `subject`. Never raises -> a minimal dict."""
    try:
        subject = subject or {}
        lines = []
        for ln in (subject.get("lines") or []):
            lines.append({
                "invoice_no": ln.get("invoice_no"),
                "date": ln.get("date"),
                "country": ln.get("country"),
                "currency": ln.get("currency"),
                "net": _num(ln.get("net")),
                "vat": _num(ln.get("vat")),
                # product/qty summaries for a registered transaction (advisory display basis)
                "product": ln.get("product"),
                "qty": ln.get("qty"),
            })
        payload = {
            "kind": subject.get("kind") or "document",
            "supplier": subject.get("supplier"),
            "supplier_vat": subject.get("supplier_vat"),
            "statement_ref": subject.get("statement_ref"),
            "statement_date": subject.get("statement_date"),
            "currency": subject.get("currency"),
            "customer": subject.get("customer"),
            "lines": lines,
        }
        # FINAL GUARD: strip every secret/bank key + '_'-prefixed key (no `needs` allowlist:
        # the chat assistant NEVER needs an IBAN/secret, so nothing is allow-listed back in).
        return ai_review._redact(payload, needs=())
    except Exception as e:
        log.warning("build_context failed — returning empty derived context: %s", e)
        return {"kind": "document", "lines": []}


# ---------------------------------------------------------------- the AI call
def ask(context, question, history=None, be=None):
    """Answer `question` about the DERIVED `context`, optionally given prior `history`
    (a list of {"role","content"} turns). Returns answer TEXT. Best-effort — NEVER raises:
    when disabled or on a hard backend failure it returns a graceful message and makes (or
    completes) no mutation. With no configured backend it makes ZERO network calls.

    `context` is expected to already be a build_context() result (derived + redacted); we run
    the redactor again here defensively so a hand-built context still cannot leak a secret."""
    question = (question or "").strip()
    if not question:
        return "Ask a question about this document's extracted data."
    chosen = backend(be)
    if chosen == "none":
        return ("The AI document assistant is disabled — configure an AI backend and enable "
                "it in Admin. (No request was sent.)")
    # Defensive re-redaction: the context handed in is treated as untrusted.
    safe_ctx = ai_review._redact(context or {}, needs=())
    parts = []
    for turn in (history or []):
        role = (turn.get("role") or "user").strip()
        content = (turn.get("content") or "").strip()
        if content:
            parts.append(f"{role.upper()}: {content}")
    convo = ("\n\nPRIOR CONVERSATION:\n" + "\n".join(parts)) if parts else ""
    content_str = (json.dumps(safe_ctx, ensure_ascii=False, default=str)
                   + convo + "\n\nQUESTION: " + question)
    try:
        return _call_text(chosen, ASSISTANT_PROMPT, content_str)
    except TransientExtractionError as e:
        log.warning("AI assistant transient error (%s) — advisory only: %s", chosen, e)
        return ("The AI assistant is busy right now — please try again in a moment. "
                "(Advisory only; nothing was changed.)")
    except Exception as e:
        if is_transient_error(e):
            log.warning("AI assistant transient error (%s) — advisory only: %s", chosen, e)
            return ("The AI assistant is busy right now — please try again in a moment. "
                    "(Advisory only; nothing was changed.)")
        log.warning("AI assistant failed (%s: %s) — advisory only, returning notice", chosen, e)
        return ("The AI assistant is unavailable right now. It is advisory only — nothing was "
                "changed; you can keep working.")


def _call_text(be, prompt, content_str):
    """A thin per-backend HTTP call mirroring extract.py / ai_review._call's backend shapes
    and env vars, but returning FREE TEXT (this is Q&A, not JSON). Sends OUR assistant prompt
    + the minimized derived payload — NEVER a PDF. Raises on transport/HTTP errors (the caller
    maps transient ones)."""
    import requests
    msg = prompt + "\n\nDATA:\n" + content_str
    if be == "claude":
        key = os.environ["ANTHROPIC_API_KEY"]
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
                  "max_tokens": 1200, "messages": [{"role": "user", "content": msg}]},
            timeout=120)
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"]).strip()
    if be == "openai":
        key = os.environ["OPENAI_API_KEY"]
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
            json={"model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
                  "messages": [{"role": "user", "content": msg}]}, timeout=120)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    if be == "azure":
        ep = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        dep = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
        r = requests.post(
            f"{ep}/openai/deployments/{dep}/chat/completions?api-version={ver}",
            headers={"api-key": os.environ["AZURE_OPENAI_KEY"],
                     "content-type": "application/json"},
            json={"messages": [{"role": "user", "content": msg}]}, timeout=120)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    raise ValueError(f"unknown ai_assistant backend: {be!r}")


# ---------------------------------------------------------------- chat history (app-owned DB)
_DDL = [
    """CREATE TABLE IF NOT EXISTS ai_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_ref TEXT,
        created_by TEXT,
        created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS ai_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        role TEXT,
        content TEXT,
        created_at TEXT)""",
    # P1 multi-tenancy (schema plumbing only): stamp both tables with a tenant_id. Existing
    # rows backfill to DEFAULT_TENANT_ID via the column DEFAULT; new rows default too. NO query
    # reads this column yet (the `multitenant` switch is OFF and scope_clause is unwired until
    # P2) — pure no-behaviour-change addition. APPEND-ONLY — keep at the END.
    *tenancy.tenant_column_ddls(["ai_chats", "ai_chat_messages"]),
]


def connect():
    """Read-write handle to the app-owned chat DB. Schema applied once per DB via db_migrate
    (APPEND-ONLY); audit triggers installed on the data change tables."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        import db_tuning
        db_tuning.tune(con)
    except Exception as e:
        log.debug("db_tuning unavailable, continuing untuned: %s", e)
    db_migrate.apply(con, "ai_assistant", _DDL)
    audit.install_audit(con, ["ai_chats", "ai_chat_messages"])
    return con


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def start_chat(subject_ref, created_by=""):
    """Create (or reuse the latest) chat thread for `subject_ref`. Returns the chat id, or
    None on failure. Never raises — best-effort persistence."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            row = con.execute(
                "SELECT id FROM ai_chats WHERE subject_ref=?" + frag
                + " ORDER BY id DESC LIMIT 1", [subject_ref, *tp]).fetchone()
            if row is not None:
                return int(row["id"])
            qt = tenancy.queue_tenant()
            cur = con.execute(
                "INSERT INTO ai_chats (subject_ref, created_by, created_at, tenant_id) "
                "VALUES (?,?,?,?)", (subject_ref, created_by or "", _now(), qt))
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()
    except Exception as e:
        log.warning("start_chat failed for %r — chat not persisted: %s", subject_ref, e)
        return None


def record_message(chat_id, role, content):
    """Append one message to a chat. role is 'user' or 'assistant'. Never raises -> False."""
    if chat_id is None:
        return False
    role = "assistant" if role == "assistant" else "user"
    try:
        con = connect()
        try:
            qt = tenancy.queue_tenant()
            con.execute(
                "INSERT INTO ai_chat_messages (chat_id, role, content, created_at, tenant_id) "
                "VALUES (?,?,?,?,?)", (int(chat_id), role, content or "", _now(), qt))
            con.commit()
            return True
        finally:
            con.close()
    except Exception as e:
        log.warning("record_message failed for chat %r — not persisted: %s", chat_id, e)
        return False


def get_chat(subject_ref):
    """Latest chat id for a subject, or None. Never raises."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            row = con.execute(
                "SELECT id FROM ai_chats WHERE subject_ref=?" + frag
                + " ORDER BY id DESC LIMIT 1", [subject_ref, *tp]).fetchone()
            return int(row["id"]) if row else None
        finally:
            con.close()
    except Exception as e:
        log.warning("get_chat failed for %r: %s", subject_ref, e)
        return None


def messages_for(subject_ref, limit=200):
    """Transcript (oldest first) for a subject's latest chat: list of
    {role, content, created_at}. Never raises -> []."""
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 200
    cid = get_chat(subject_ref)
    if cid is None:
        return []
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            rows = con.execute(
                "SELECT role, content, created_at FROM ai_chat_messages "
                "WHERE chat_id=?" + frag + " ORDER BY id ASC LIMIT ?",
                [cid, *tp, limit]).fetchall()
        finally:
            con.close()
        return [{"role": r["role"] or "user", "content": r["content"] or "",
                 "created_at": r["created_at"] or ""} for r in rows]
    except Exception as e:
        log.warning("messages_for failed for %r: %s", subject_ref, e)
        return []
