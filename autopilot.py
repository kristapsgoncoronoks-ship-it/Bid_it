"""
AUTO-PILOT INTAKE — an OPT-IN, default-OFF gate that AUTO-FILES (registers) an extracted
document into the VAT pipeline WITHOUT human review, but ONLY when it is high-confidence,
passes AI verification (when verification is enabled), AND passes the deterministic
validation gate. Everything doubtful stays in the review queue exactly as today.

This is the "Auto, review exceptions" behaviour: reading/verifying/storing is always
automatic (that is the existing extract path); this module adds the optional final step
of registering the statement when — and only when — every conservative condition holds.

By construction it is:

  * DEFAULT OFF (`enabled()`, mirroring `vision_capture.enabled()`'s setting pattern).
    With the setting OFF the worker behaves byte-identically to today: nothing is ever
    auto-filed and every job lands in 'ready' for human review.
  * NEVER bypasses the legal gate. `autofile()` runs `validate.validate_batch(...)` and
    REFUSES to file unless `can_commit` is True — the same deterministic gate the human
    confirm screen enforces. A synthetic / placeholder line (INPUT / ALL: / UNMATCHED)
    fails the gate and is left for review.
  * CONSERVATIVE — `evaluate()` is a PURE gate: ALL conditions must hold or it returns
    ok=False with a human-readable reason. Any failure / exception in the worker wiring
    falls through to the existing 'ready' path. Best-effort; never crashes the worker.
  * The registration / PDF-vaulting it performs MIRRORS app.py's `extract_confirm` route
    EXACTLY and reuses the SAME helpers (validate, waiting_room.enqueue_registration,
    vat_refund.attach_document) — it does not re-implement registration differently.
  * The audit actor for everything auto-filed is "autopilot" (carried into the enqueued
    registration job and the import_log entry).
"""
import applog

log = applog.get("autopilot")

# The admin opt-in setting that turns auto-pilot intake ON (default OFF).
SETTING = "intake_autopilot_enabled"

# invoice_no values that mark a synthetic / placeholder line that can NEVER be filed
# (mirrors vat_refund._synthetic's INPUT/ALL:/UNMATCHED markers).
_SYNTHETIC = {"INPUT", "ALL:", "UNMATCHED"}


def enabled():
    """True only when the admin opt-in setting is ON. Never raises -> False (fail toward
    OFF / no auto-filing). Mirrors vision_capture.enabled()'s setting read."""
    try:
        import auth
        return str(auth.get_setting(SETTING, "off") or "off").lower() in ("on", "1", "true", "yes")
    except Exception as e:
        log.warning("enabled() check failed — defaulting to OFF: %s", e)
        return False


def _nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def _looks_period(v):
    """True when `v` is a YYYY-MM or YYYY-MM-DD string (a derivable claim period)."""
    if not isinstance(v, str):
        return False
    v = v.strip()
    if len(v) < 7:
        return False
    return v[:4].isdigit() and v[4] == "-" and v[5:7].isdigit()


def _line_period(line):
    """The YYYY-MM period derivable from a single draft line's date, or None."""
    d = line.get("date") if isinstance(line, dict) else None
    return d.strip()[:7] if _looks_period(d) else None


def evaluate(draft, verify_ok=None):
    """PURE gate (no I/O beyond reading the draft). Returns {"ok": bool, "reasons": [str]}.

    ALL of the following must hold to auto-file; each failure adds a reason and ok=False:
      * confidence == "high"
      * supplier is a non-empty string
      * statement_ref is a non-empty string
      * lines is a non-empty list
      * a period is derivable (statement_date looks like YYYY-MM(-DD), OR at least one
        line has a date with YYYY-MM)
      * no synthetic / placeholder line (invoice_no empty / INPUT / ALL: / UNMATCHED / ALL:*)
      * if verify_ok is not None it must be True (verification ran and passed). verify_ok
        is None means verification was not run/available — the gate may still pass on the
        other criteria (the caller decides WHETHER to run verify; see waiting_room)."""
    reasons = []
    draft = draft or {}

    if draft.get("confidence") != "high":
        reasons.append(f"confidence is {draft.get('confidence')!r}, need 'high'")
    if not _nonempty_str(draft.get("supplier")):
        reasons.append("supplier is missing")
    if not _nonempty_str(draft.get("statement_ref")):
        reasons.append("statement_ref is missing")

    lines = draft.get("lines")
    if not (isinstance(lines, list) and lines):
        reasons.append("no lines")
        lines = []

    # period derivable from the statement date or any line date
    has_period = _looks_period(draft.get("statement_date")) or any(
        _line_period(l) for l in lines if isinstance(l, dict))
    if not has_period:
        reasons.append("no derivable period (statement_date / line dates)")

    # synthetic / placeholder line refusal
    for l in lines:
        if not isinstance(l, dict):
            reasons.append("malformed line")
            break
        inv = l.get("invoice_no")
        inv_s = inv.strip() if isinstance(inv, str) else ""
        if not inv_s or inv_s in _SYNTHETIC or inv_s.startswith("ALL:"):
            reasons.append(f"synthetic / placeholder line invoice_no {inv!r}")
            break

    if verify_ok is not None and verify_ok is not True:
        reasons.append("AI verification did not pass")

    return {"ok": not reasons, "reasons": reasons}


def _vlines(draft):
    """The validate-shaped line dicts (invoice_no/date/country/currency/net/vat) built
    from the draft lines — exactly the shape extract_confirm builds before validate."""
    out = []
    for l in (draft.get("lines") or []):
        if not isinstance(l, dict):
            continue
        out.append({"invoice_no": l.get("invoice_no"), "date": l.get("date"),
                    "country": l.get("country"), "currency": l.get("currency") or "EUR",
                    "net": l.get("net"), "vat": l.get("vat")})
    return out


def _period(draft, vlines):
    """statement_date[:7] when valid YYYY-MM(-DD); else the EARLIEST line date[:7]; else
    None. Mirrors the period the confirm route carries into registration."""
    sd = draft.get("statement_date")
    if _looks_period(sd):
        return sd.strip()[:7]
    cands = sorted(p for p in (_line_period({"date": v.get("date")}) for v in vlines) if p)
    return cands[0] if cands else None


def autofile(con, row, draft, actor="autopilot"):
    """Auto-file (register) an extracted draft into the VAT pipeline, MIRRORING app.py's
    extract_confirm route (validate -> enqueue_registration -> save_baseline -> vault PDFs).

    Returns (status, info):
      * ("ready", {...}) — the deterministic validation gate failed, or no period could be
        derived: the job is LEFT for human review (NOT filed).
      * ("done", {...}) — registration enqueued + baseline saved + PDFs vaulted (best-effort).

    `con` is the worker's intake-queue connection (unused for the product-DB writes, which
    are enqueued / use vat_refund.connect — kept for signature symmetry with the workflow).
    NEVER bypasses the legal gate: registration is enqueued only on can_commit."""
    import validate as VAL
    vlines = _vlines(draft)
    # Enforce the SAME invoice tie-out the human-confirm and bulk-confirm paths use: thread
    # the parsed document total so a line-sum mismatch fails can_commit here too (the
    # authoritative file step), not only in the callers. No total -> no tie (unchanged).
    ct = draft.get("coversheet_total")
    try:
        ct = float(ct) if ct is not None else None
    except (TypeError, ValueError):
        ct = None
    vr = (VAL.validate_batch(vlines, coversheet_total=ct) if ct is not None
          else VAL.validate_batch(vlines))
    if not vr["can_commit"]:
        tie = vr.get("tie")
        reason = ("tie-out mismatch" if tie is not None and not tie.get("ok")
                  else "validation: %d error(s)" % vr["errors"])
        return ("ready", {"filed": False, "reason": reason})

    period = _period(draft, vlines)
    if not period:
        return ("ready", {"filed": False, "reason": "no period"})

    supplier = (draft.get("supplier") or "").strip()
    statement_ref = (draft.get("statement_ref") or "").strip()
    customer = draft.get("customer") or None

    # lines as TUPLES (inv, date, country, ccy, net, vat) — the SAME shape the confirm
    # route's `lines` carries into the registration payload.
    tuples = [(v["invoice_no"], v["date"], v["country"], v["currency"], v["net"], v["vat"])
              for v in vlines]
    reg_payload = {
        "supplier": supplier, "statement_ref": statement_ref, "period": period,
        "statement_date": draft.get("statement_date") or "",
        "lines": tuples, "customer": customer,
        "notes": "auto-filed via autopilot (high-confidence, verified)", "draft": None,
    }
    import waiting_room as IQ
    job_id, _job_st = IQ.enqueue_registration(reg_payload, user=actor)
    VAL.save_baseline(supplier, statement_ref, vlines)

    # Vault the source PDFs against their invoice refs — replicate the route's vaulting.
    # draft["_pdf_bytes"] is a list of (name, bytes); the draft still carries it at this
    # point (it is stripped only when stored on the row). Best-effort: per-PDF errors are
    # swallowed + logged; never break the auto-file.
    attached = 0
    try:
        import vat_refund as VR
        pdfs = draft.get("_pdf_bytes") or []
        if pdfs:
            fcon = VR.connect()
            try:
                ent = customer or supplier
                for v in vlines:
                    inv = (v.get("invoice_no") or "").strip()
                    cand = next((b for nm, b in pdfs if inv and inv[:8] in nm), None)
                    if inv and cand:
                        ok, _ = VR.attach_document(
                            fcon, ent, supplier, inv, file_bytes=cand,
                            filename=f"{inv}.pdf", kind="original_pdf",
                            country=v.get("country") or None, period=period)
                        if ok:
                            attached += 1
                # no per-line PDF matched: attach all to the first invoice ref
                if attached == 0 and vlines:
                    first_inv = (vlines[0].get("invoice_no") or "").strip()
                    for nm, b in pdfs:
                        try:
                            ok, _ = VR.attach_document(
                                fcon, ent, supplier, first_inv, file_bytes=b,
                                filename=nm or f"{first_inv}.pdf", kind="original_pdf",
                                country=vlines[0].get("country") or None, period=period)
                            if ok:
                                attached += 1
                        except Exception as e:
                            log.warning("autopilot PDF vault (fallback) failed for %s: %s", nm, e)
            finally:
                fcon.close()
    except Exception as e:
        log.warning("autopilot PDF vaulting failed (registration unaffected): %s", e)

    # AUTO SUPPLIER-MASTER MAINTENANCE (best-effort). Autopilot only fires on a HIGH-confidence,
    # verified draft (evaluate()), so the supplier_sync gate is satisfied: a brand-new supplier
    # is created PROVISIONAL with full legal details; a recognised supplier's SAFE fields auto-
    # update; HIGH-RISK (IBAN/VAT) changes become admin-pending requests (NEVER auto-applied).
    # NEVER raises into the auto-file — registration is already done above.
    try:
        import supplier_sync as SS
        captured = {
            "legal_name": supplier or draft.get("supplier"),
            "reg_no": draft.get("supplier_reg_no"),
            "address": draft.get("supplier_address"),
            "country": draft.get("supplier_country"),
            "vat": draft.get("supplier_vat"),
            "iban": draft.get("supplier_iban"),
            "bank": draft.get("supplier_bank"),
        }
        if captured["legal_name"] or captured["vat"]:
            existing_code = None
            try:
                import dataproduct
                scon = dataproduct.connect("suppliers")
                try:
                    if SS.norm_vat(captured["vat"]):
                        r = scon.execute(
                            "SELECT supplier FROM supplier_vat_registrations "
                            "WHERE REPLACE(UPPER(vat_number),' ','')=?",
                            (SS.norm_vat(captured["vat"]),)).fetchone()
                        if r:
                            existing_code = r["supplier"]
                    if not existing_code and supplier:
                        r = scon.execute("SELECT code FROM suppliers WHERE UPPER(code)=?",
                                         (supplier.upper(),)).fetchone()
                        if r:
                            existing_code = r["code"]
                finally:
                    scon.close()
            except Exception as e:
                log.warning("autopilot supplier resolve failed: %s", e)
            SS.apply(captured, existing_code, actor=actor, verified=True,
                     invoice_ref=statement_ref, confidence=draft.get("confidence"))
    except Exception as e:
        log.warning("autopilot supplier-master sync failed (registration unaffected): %s", e)

    try:
        import import_log as _IL
        _IL.log("statement", statement_ref, "success", actor=actor,
                client=customer, supplier=supplier, period=period, records=len(vlines),
                message=(f"auto-filed via autopilot: {len(vlines)} invoices validated, "
                         f"{attached} PDFs vaulted; registration queued (job {job_id})"))
    except Exception as e:
        log.warning("autopilot import_log write failed (registration unaffected): %s", e)

    return ("done", {"filed": True, "period": period, "lines": len(vlines),
                     "attached": attached, "reg_job": job_id})
