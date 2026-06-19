"""
AUTO SUPPLIER-MASTER MAINTENANCE from captured invoices.

The supplier on a VAT refund claim must be the REAL legal entity with COMPLETE,
legally-correct company details. This module keeps `suppliers.db` (the admin-curated
supplier master) up to date from what a captured + AI-verified invoice actually shows:

  * an UNKNOWN supplier (no code matches) → create a PROVISIONAL supplier with the full
    legal details (legal name, company registration number, address, home country) + a
    per-country VAT registration + the captured IBAN — but ONLY when verification passed
    (AI-verified) or the capture confidence is 'high'. Admin-visible (status='provisional').
  * a KNOWN supplier whose details CHANGED → auto-apply the SAFE fields (legal/entity name,
    registered address, company registration number, contact) with a full audit trail.

★ HARD FRAUD-SAFETY INVARIANT (non-negotiable). AI verification confirms the capture
matches the INVOICE — NOT that the invoice is legitimate. A fraudulent invoice with a
swapped IBAN or VAT number would PASS verification. Therefore the HIGH-RISK fields —
bank account / IBAN, and VAT number — are NEVER silently auto-updated on an EXISTING
supplier. A detected change to them is recorded as a PENDING CHANGE REQUEST
(`supplier_change_requests`) for an admin to approve/reject; the stored bank account /
VAT registration is NOT touched until an admin approves. (A brand-NEW supplier MAY be
created with its captured IBAN/VAT — there is nothing to fraudulently overwrite — but it
lands PROVISIONAL.)

This module is the WRITABLE engine/in-request path: it goes through
`supplier_master.connect()` (the app's admin-curated master-data write path), is fully
audited (the suppliers / supplier_vat_registrations / supplier_bank_accounts tables carry
audit triggers; the caller binds the actor via audit.set_actor), and is best-effort —
`plan()` is pure and `apply()` NEVER raises into the caller (it logs and returns).

VERIFICATION GATE (documented choice):
  * NEW supplier   → created only when `verified` is True (ai_verify ran + passed) OR the
                     draft confidence is 'high'. A low-confidence, unverified capture
                     never invents master data.
  * EXISTING safe  → SAFE-field auto-updates require `verified` is True OR confidence
                     'high' (conservative — same bar as a new supplier).
  * EXISTING high-risk → NEVER auto-applied regardless of verification; always pending.
The caller passes `verified` from the draft's AI-verification result (a 'confirmed' /
'verified_after_correction' verdict) when ai_verify ran; with ai_verify OFF the gate
falls back to the capture confidence ('high').
"""
import os
import re

import applog
import audit
import money  # noqa: F401  (imported for convention parity; amounts not handled here)
import supplier_master as SM
import tenancy

log = applog.get("supplier_sync")

WORKDIR = os.path.dirname(os.path.abspath(__file__))

# SAFE fields auto-update (AI-verified, audited). HIGH-RISK fields (bank/IBAN, VAT) NEVER
# auto-update on an existing supplier — they become pending change requests.
SAFE_FIELDS = ("legal_name", "address", "phone", "email")
# Identity anchors + payment instruction — NEVER auto-applied on an existing supplier; a
# change here always becomes a pending admin confirmation. VAT id + company registration
# number are the supplier's stable identity ("these numbers don't change"); bank/IBAN is the
# payment destination (the invoice-fraud target).
HIGH_RISK_FIELDS = ("iban", "vat", "company_reg")


# ─────────────────────────────────────────────────────────── normalisation helpers
def norm_iban(v):
    """Canonicalise an IBAN for comparison: strip ALL whitespace + punctuation, upper.
    Returns '' for an empty/None value (never raises)."""
    if not v:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(v)).upper()


def norm_vat(v):
    """Canonicalise a VAT number for comparison: strip whitespace + punctuation, upper."""
    if not v:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(v)).upper()


def _norm_text(v):
    """Collapse whitespace + casefold a free-text field for SAFE-field change detection
    (so a pure whitespace/case reshuffle is NOT treated as a change)."""
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip().casefold()


def _s(v):
    """Trimmed string or '' (never None) for storage/compare."""
    return ("" if v is None else str(v)).strip()


# ─────────────────────────────────────────────────────────── schema (pending changes)
# The pending-change queue lives in suppliers.db's app-curated space — it is admin-curated
# master-data governance, kept next to the supplier master it gates. APPEND-ONLY migration.
_SCHEMA_READY = set()


def _ensure_schema(con):
    """Create the supplier_change_requests table once per process per DB file. The table is
    NOT audited via triggers (it IS the audit trail for the pending change) but every write
    here is recorded in the application audit log by the caller's action context."""
    import db_migrate
    dbf = SM._db()
    if dbf in _SCHEMA_READY:
        return
    db_migrate.apply(con, "supplier_sync", [
        # supplier_change_requests: a HIGH-RISK (bank/IBAN, VAT) change detected from a
        # capture that an admin must approve before it touches the supplier master.
        # tenant_id carried for parity with the rest of suppliers.db (P1 scaffolding).
        """CREATE TABLE IF NOT EXISTS supplier_change_requests (
            id INTEGER PRIMARY KEY,
            supplier TEXT, field TEXT, old_value TEXT, new_value TEXT,
            source TEXT DEFAULT 'capture', invoice_ref TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')), created_by TEXT,
            resolved_at TEXT, resolved_by TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default')""",
    ])
    _SCHEMA_READY.add(dbf)


# ─────────────────────────────────────────────────────────── plan (pure)
def plan(captured, existing):
    """PURE structured diff between a CAPTURED legal entity and the matched supplier row
    (or None). No I/O, never raises.

    `captured` keys (any may be missing/None): legal_name, reg_no, address, country, vat,
    iban (and optionally bank, phone, email, code).
    `existing` is the matched supplier as a dict/Row enriched with the stored vat number
    (`vat`) and stored IBANs (`ibans`: a list) for the same supplier, or None.

    Returns: {
        "new": bool,                       # no existing match → create
        "code": str|None,                  # the matched supplier code (None when new)
        "safe_updates": {field: (old,new)},      # auto-apply (verified)
        "high_risk_changes": {field: (old,new)}, # NEVER auto-apply → pending
    }
    """
    captured = captured or {}
    is_new = existing is None
    code = None if is_new else (existing.get("code") if isinstance(existing, dict)
                                else existing["code"])

    safe = {}
    high = {}

    # captured values
    c_name = _s(captured.get("legal_name"))
    c_addr = _s(captured.get("address"))
    c_reg = _s(captured.get("reg_no"))
    c_phone = _s(captured.get("phone"))
    c_email = _s(captured.get("email"))
    c_vat = _s(captured.get("vat"))
    c_iban = norm_iban(captured.get("iban"))

    if is_new:
        return {"new": True, "code": None, "safe_updates": {}, "high_risk_changes": {}}

    ex = existing if isinstance(existing, dict) else dict(existing)

    # SAFE fields: a captured non-empty value that DIFFERS from the stored one (case/ws
    # insensitive) is an update. A captured-empty field is never an update (we never blank
    # a stored value from a capture that didn't read it).
    for field, cval in (("legal_name", c_name), ("address", c_addr),
                        ("phone", c_phone), ("email", c_email)):
        if not cval:
            continue
        old = _s(ex.get(field))
        if _norm_text(cval) != _norm_text(old):
            safe[field] = (old or None, cval)

    # HIGH-RISK — VAT number AND company registration number are the STABLE IDENTITY of the
    # supplier ("these numbers don't change"); a change means it may be a different entity (or
    # fraud), so it is NEVER auto-applied — an admin confirms it.
    if c_vat:
        old_vat = _s(ex.get("vat"))
        if norm_vat(c_vat) != norm_vat(old_vat):
            high["vat"] = (old_vat or None, c_vat)
    if c_reg:
        old_reg = _s(ex.get("company_reg"))
        if _norm_text(c_reg) != _norm_text(old_reg):
            high["company_reg"] = (old_reg or None, c_reg)

    # HIGH-RISK — IBAN. A captured IBAN that does NOT match ANY stored bank account for the
    # supplier is a change (a brand-new IBAN on an existing supplier is exactly the swap we
    # guard against). When the supplier has no stored IBAN at all, a captured one is still a
    # high-risk ADD for an admin to confirm (never silently trust a payment instruction).
    if c_iban:
        stored = {norm_iban(x) for x in (ex.get("ibans") or []) if norm_iban(x)}
        if c_iban not in stored:
            # show the first stored IBAN as the "old" for the admin's context, else None
            old_iban = next(iter(ex.get("ibans") or []), None)
            high["iban"] = (old_iban, _s(captured.get("iban")))

    return {"new": False, "code": code, "safe_updates": safe, "high_risk_changes": high}


# ─────────────────────────────────────────────────────────── existing-supplier loader
def load_existing(code, con=None):
    """Read the matched supplier as a plan()-ready dict: the suppliers row fields PLUS the
    stored VAT number for the supplier's home country (`vat`) and the list of stored IBANs
    (`ibans`). Returns None when the code is unknown. Tenant-scoped. Never raises -> None."""
    code = (code or "").strip().upper()
    if not code:
        return None
    own = con is None
    if own:
        con = SM.connect()
    try:
        frag, params = tenancy.scope_clause()
        s = con.execute("SELECT * FROM suppliers WHERE code=?" + frag,
                        [code, *params]).fetchone()
        if not s:
            return None
        out = dict(s)
        # the supplier's home-country VAT registration (the one that lands on a same-country
        # claim) — used as the "current" VAT for change detection
        home = (out.get("home_country") or "").strip()
        vrow = con.execute(
            "SELECT vat_number FROM supplier_vat_registrations WHERE supplier=? AND country=?"
            + frag, [code, home, *params]).fetchone() if home else None
        out["vat"] = (vrow["vat_number"] if vrow and vrow["vat_number"] else "")
        ibans = [r["iban"] for r in con.execute(
            "SELECT iban FROM supplier_bank_accounts WHERE supplier=?" + frag,
            [code, *params]).fetchall() if r["iban"]]
        out["ibans"] = ibans
        return out
    except Exception as e:
        log.warning("load_existing(%s) failed: %s", code, e)
        return None
    finally:
        if own:
            con.close()


# ─────────────────────────────────────────────────────────── apply
def apply(captured, code, actor, verified=False, invoice_ref=None, confidence=None):
    """Apply the auto-maintenance plan for a captured legal entity. NEVER raises into the
    caller (best-effort: logs + returns a result dict). The caller MUST have bound the audit
    actor (audit.set_actor) so the supplier-master writes are attributed.

    Args:
      captured : {legal_name, reg_no, address, country, vat, iban, bank, phone, email, code}
      code     : the matched existing supplier code, or None/'' for a NEW supplier
      actor    : the confirming user (audit actor + created_by on a pending request)
      verified : True when AI verification ran and passed; gates NEW + SAFE updates
      confidence: the draft confidence ('high' is treated as the gate when verify is OFF)
      invoice_ref: the source statement ref (recorded on a pending change / provisional note)

    Returns one of:
      {"created": code, "ibans": n, "note": ...}                       (NEW)
      {"updated": [field,...], "pending": [field,...]}                 (EXISTING)
      {"skipped": reason}                                             (gate not met / error)
    """
    captured = captured or {}
    gate_ok = bool(verified) or (str(confidence or "").lower() == "high")
    code = (code or "").strip().upper()

    try:
        if not code:
            return _apply_new(captured, actor, gate_ok, invoice_ref)
        return _apply_existing(captured, code, actor, gate_ok, invoice_ref)
    except Exception as e:
        # NEVER raises into the confirm path — log and skip.
        log.warning("supplier_sync.apply failed for %r (best-effort, skipped): %s",
                    code or "(new)", e)
        return {"skipped": f"error: {e}"}


def _apply_new(captured, actor, gate_ok, invoice_ref):
    """Create a PROVISIONAL supplier with the FULL legal details + VAT reg + IBAN, only when
    the verification/confidence gate is met. A brand-new supplier MAY carry its captured
    IBAN/VAT (nothing to fraudulently overwrite); it lands status='provisional'."""
    if not gate_ok:
        return {"skipped": "not verified / not high-confidence — new supplier NOT created"}
    legal_name = _s(captured.get("legal_name"))
    country = _country(captured)
    if not legal_name:
        return {"skipped": "no captured legal name — new supplier NOT created"}
    if not country:
        return {"skipped": "no derivable country — new supplier NOT created"}
    code = _derive_code(legal_name)
    con = SM.connect()
    audit.set_actor(con, actor or "system")
    try:
        if SM.supplier_exists(code, con=con):
            # extremely unlikely (caller resolved to None), but never clobber a known one
            return {"skipped": f"code {code} already exists"}
        tid = tenancy.queue_tenant()
        note = (f"auto-captured from invoice {invoice_ref} — confirm details"
                if invoice_ref else "auto-captured from invoice — confirm details")
        con.execute("""INSERT INTO suppliers
            (code, legal_name, group_name, address, home_country, company_reg, phone,
             email, portal, payment_terms, payment_notes, status, notes, tenant_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, legal_name, None, _s(captured.get("address")) or None, country,
             _s(captured.get("reg_no")) or None, _s(captured.get("phone")) or None,
             _s(captured.get("email")) or None, None, None, None,
             "provisional", note, tid))
        con.commit()
        # per-country VAT registration (entity_name = the captured legal entity, source
        # 'capture' so set_vat_registration's manual>capture precedence governs later edits)
        vat = _s(captured.get("vat"))
        SM.set_vat_registration(code, country, vat or None, source="capture",
                                entity_name=legal_name)
        # IBAN → supplier_bank_accounts (a new supplier may carry its captured IBAN)
        n_ibans = 0
        iban = norm_iban(captured.get("iban"))
        if iban:
            con.execute("""INSERT OR IGNORE INTO supplier_bank_accounts
                (supplier, beneficiary, iban, swift, bank, currency, notes, tenant_id)
                VALUES (?,?,?,?,?,?,?,?)""",
                (code, legal_name, _s(captured.get("iban")), None,
                 _s(captured.get("bank")) or None, None,
                 f"captured from invoice {invoice_ref}" if invoice_ref
                 else "captured from invoice", tid))
            con.commit()
            n_ibans = 1
        log.info("supplier_sync: provisional supplier %s created from capture (%d IBAN)",
                 code, n_ibans)
        return {"created": code, "ibans": n_ibans, "note": note}
    finally:
        audit.reset_actor(con)
        con.close()


def _apply_existing(captured, code, actor, gate_ok, invoice_ref):
    """Auto-apply SAFE field updates (when the gate is met) and queue HIGH-RISK changes
    (IBAN/VAT) as pending change requests — NEVER touching the stored bank account / VAT."""
    con = SM.connect()
    audit.set_actor(con, actor or "system")
    try:
        _ensure_schema(con)
        existing = load_existing(code, con=con)
        if existing is None:
            return {"skipped": f"supplier {code} not found"}
        p = plan(captured, existing)
        updated = []
        # SAFE fields: auto-apply only when verified / high-confidence.
        if gate_ok and p["safe_updates"]:
            frag, params = tenancy.scope_clause()
            for field, (_old, new) in p["safe_updates"].items():
                con.execute(f"UPDATE suppliers SET {field}=? WHERE code=?" + frag,
                            [new, code, *params])
                updated.append(field)
            con.commit()
            # keep the per-country entity_name in step with a legal_name change (capture
            # source; manual edits still win via set_vat_registration precedence)
            if "legal_name" in p["safe_updates"]:
                home = (existing.get("home_country") or "").strip()
                if home:
                    SM.set_vat_registration(
                        code, home,
                        existing.get("vat") or None, source="capture",
                        entity_name=p["safe_updates"]["legal_name"][1])
        # HIGH-RISK changes: NEVER applied — record a pending change request per field.
        pending = []
        for field, (old, new) in p["high_risk_changes"].items():
            _queue_change(con, code, field, old, new, invoice_ref, actor)
            pending.append(field)
        if updated or pending:
            log.info("supplier_sync: %s — %d safe update(s), %d pending high-risk",
                     code, len(updated), len(pending))
        return {"updated": updated, "pending": pending}
    finally:
        audit.reset_actor(con)
        con.close()


def _queue_change(con, supplier, field, old, new, invoice_ref, actor):
    """Insert (or refresh) a PENDING high-risk change request. De-duplicates: an identical
    still-pending (supplier, field, new_value) is not duplicated."""
    tid = tenancy.queue_tenant()
    frag, params = tenancy.scope_clause()
    dup = con.execute(
        "SELECT id FROM supplier_change_requests WHERE supplier=? AND field=? "
        "AND COALESCE(new_value,'')=? AND status='pending'" + frag,
        [supplier, field, _s(new), *params]).fetchone()
    if dup:
        return
    con.execute("""INSERT INTO supplier_change_requests
        (supplier, field, old_value, new_value, source, invoice_ref, status, created_by, tenant_id)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (supplier, field, _s(old) or None, _s(new) or None, "capture",
         _s(invoice_ref) or None, "pending", actor or "system", tid))
    con.commit()


# ─────────────────────────────────────────────────────────── pending-change admin flow
def pending_changes(status="pending", con=None):
    """List supplier_change_requests with the given status (default 'pending'), newest
    first. Tenant-scoped. Returns a list of dicts. Never raises -> []."""
    own = con is None
    if own:
        con = SM.connect()
    try:
        _ensure_schema(con)
        frag, params = tenancy.scope_clause()
        rows = con.execute(
            "SELECT * FROM supplier_change_requests WHERE status=?" + frag
            + " ORDER BY id DESC", [status, *params]).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("pending_changes(%s) failed: %s", status, e)
        return []
    finally:
        if own:
            con.close()


def pending_count(con=None):
    """Count of pending change requests (for a nav badge). Never raises -> 0."""
    try:
        return len(pending_changes("pending", con=con))
    except Exception:
        return 0


def approve_change(req_id, actor, con=None):
    """APPROVE a pending high-risk change: APPLY it to the supplier master (audited) and mark
    the request approved. Only acts on a 'pending' row. Returns {"applied": field} or
    {"skipped": reason}. NEVER raises -> {"skipped": error}. The caller (admin route) has
    already enforced the admin gate + CSRF."""
    own = con is None
    if own:
        con = SM.connect()
    audit.set_actor(con, actor or "system")
    try:
        _ensure_schema(con)
        frag, params = tenancy.scope_clause()
        row = con.execute(
            "SELECT * FROM supplier_change_requests WHERE id=? AND status='pending'" + frag,
            [req_id, *params]).fetchone()
        if not row:
            return {"skipped": "no pending change with that id"}
        supplier = row["supplier"]
        field = row["field"]
        new = row["new_value"]
        if field == "vat":
            # apply the VAT number to the supplier's home-country registration (source
            # 'manual' — an admin approved it, so it now WINS over a later capture)
            s = con.execute("SELECT home_country FROM suppliers WHERE code=?" + frag,
                            [supplier, *params]).fetchone()
            home = (s["home_country"] if s else "") or ""
            if not home:
                return {"skipped": f"{supplier} has no home country for the VAT registration"}
            SM.set_vat_registration(supplier, home, new, source="manual")
        elif field == "iban":
            # add the approved IBAN as a bank account (admin-confirmed payment instruction)
            tid = tenancy.queue_tenant()
            legal = con.execute("SELECT legal_name FROM suppliers WHERE code=?" + frag,
                                [supplier, *params]).fetchone()
            beneficiary = (legal["legal_name"] if legal else None) or supplier
            con.execute("""INSERT OR IGNORE INTO supplier_bank_accounts
                (supplier, beneficiary, iban, swift, bank, currency, notes, tenant_id)
                VALUES (?,?,?,?,?,?,?,?)""",
                (supplier, beneficiary, new, None, None, None,
                 "admin-approved from capture change request", tid))
        elif field == "company_reg":
            # registration number is an identity anchor — an admin approved the change, so
            # write it to the supplier's company_reg (audited).
            con.execute("UPDATE suppliers SET company_reg=? WHERE code=?" + frag,
                        [new, supplier, *params])
        else:
            # a high-risk field we don't auto-apply for — should not happen, but be safe
            return {"skipped": f"unsupported high-risk field {field!r}"}
        con.execute(
            "UPDATE supplier_change_requests SET status='approved', resolved_at=datetime('now'), "
            "resolved_by=? WHERE id=?" + frag, [actor or "system", req_id, *params])
        con.commit()
        log.info("supplier_sync: change request %s (%s/%s) APPROVED by %s",
                 req_id, supplier, field, actor)
        return {"applied": field, "supplier": supplier}
    except Exception as e:
        log.warning("approve_change(%s) failed: %s", req_id, e)
        return {"skipped": f"error: {e}"}
    finally:
        audit.reset_actor(con)
        if own:
            con.close()


def reject_change(req_id, actor, con=None):
    """REJECT (discard) a pending change: mark it rejected, touch NOTHING in the master.
    Returns {"rejected": id} or {"skipped": reason}. NEVER raises."""
    own = con is None
    if own:
        con = SM.connect()
    try:
        _ensure_schema(con)
        frag, params = tenancy.scope_clause()
        cur = con.execute(
            "UPDATE supplier_change_requests SET status='rejected', resolved_at=datetime('now'), "
            "resolved_by=? WHERE id=? AND status='pending'" + frag,
            [actor or "system", req_id, *params])
        con.commit()
        if (cur.rowcount or 0) == 0:
            return {"skipped": "no pending change with that id"}
        log.info("supplier_sync: change request %s REJECTED by %s", req_id, actor)
        return {"rejected": req_id}
    except Exception as e:
        log.warning("reject_change(%s) failed: %s", req_id, e)
        return {"skipped": f"error: {e}"}
    finally:
        if own:
            con.close()


# ─────────────────────────────────────────────────────────── small helpers
def _country(captured):
    """Resolve a 2-letter ISO home country for a NEW supplier: prefer the captured country
    (name or code) mapped to ISO, else derive from the VAT prefix. None when unknown."""
    raw = _s(captured.get("country"))
    if raw:
        iso = _country_to_iso(raw)
        if iso:
            return iso
    return SM.country_from_vat(captured.get("vat"))


# Minimal English-name → ISO map for the EU/EEA fuel-card geographies; anything not here
# falls back to the VAT-prefix derivation (so we never invent a country from a free string).
_NAME_TO_ISO = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR", "cyprus": "CY",
    "czechia": "CZ", "czech republic": "CZ", "denmark": "DK", "estonia": "EE",
    "finland": "FI", "france": "FR", "germany": "DE", "greece": "GR", "hungary": "HU",
    "ireland": "IE", "italy": "IT", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
    "malta": "MT", "netherlands": "NL", "poland": "PL", "portugal": "PT", "romania": "RO",
    "slovakia": "SK", "slovenia": "SI", "spain": "ES", "sweden": "SE",
}


def _country_to_iso(raw):
    """A captured country string → ISO 3166 alpha-2, or None. Accepts a 2-letter code or a
    full English name; never guesses from anything else."""
    v = _s(raw)
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return _NAME_TO_ISO.get(v.casefold())


def _derive_code(legal_name):
    """Derive a stable supplier CODE from a legal name (uppercased alnum, first token-ish,
    capped). Mirrors how a human would short-code a supplier. Best-effort + deterministic."""
    name = re.sub(r"[^0-9A-Za-z ]", " ", _s(legal_name)).strip()
    if not name:
        return "SUP"
    # take the first significant token, drop common company-form suffixes
    tokens = [t for t in name.split()
              if t.upper() not in ("THE", "GMBH", "AG", "SE", "SA", "SAU", "SARL", "BV",
                                   "NV", "OU", "AB", "AS", "SPZOO", "LTD", "PLC", "KG",
                                   "CO", "AND")]
    base = (tokens[0] if tokens else name.split()[0]).upper()
    return re.sub(r"[^0-9A-Z]", "", base)[:12] or "SUP"
