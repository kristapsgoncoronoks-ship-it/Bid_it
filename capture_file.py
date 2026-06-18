"""
CAPTURE FILE — persist the AI-vision CAPTURE DOCUMENT as a permanent SECOND FILE next to
the original invoice PDF.

When a draft is produced by the vision-capture path (`vision_capture.to_draft`, draft
carries a `capture` document) the captured data is, today, only a transient review panel +
an on-screen download. This module turns it into a DURABLE artifact in the app-owned
``data_lake`` (kind=``capture_document``), LINKED to the ORIGINAL upload by that upload's
SHA-256 — the same sha the upload archived as its ``raw_upload`` data-lake entry — so the
original PDF and its captured-data file are clearly a PAIR.

Two renderings are stored per capture: a structured ``.json`` and a human-readable
``.txt`` (both binary-stripped; nothing under a leading ``_`` key, never the PDF bytes).
It is saved at CAPTURE time (on the worker, after a vision draft) and RE-SAVED after AI
corrections (``ai_verify.apply_corrections``) so the persisted file reflects the verified /
corrected result. Re-saving is a NEW content-addressed artifact (data_lake dedups by sha)
linked to the same upload sha; ``latest_for_upload`` returns the most recent, i.e. the
versioning is "newest wins".

By construction this module:
  * is APP-OWNED — it only writes the app-owned data lake; it opens NO writable product-DB
    handle (no suppliers.db / fuel_history.db).
  * is BEST-EFFORT / NEVER raises — a data_lake failure is logged and swallowed so it can
    never break capture or review.
  * is IDEMPOTENT — identical capture content de-dups to the same artifact (sha-keyed).
  * stores ONLY the captured invoice fields (that is the whole point) — never a secret,
    never the PDF/image bytes, and it never LOGS the payload bytes or keys.
"""
import os
import json

import applog

log = applog.get("capture_file")

# The data-lake `kind` used for the persisted captured-data file (JSON + text).
KIND = "capture_document"


def build_text(cap):
    """A readable plain-text rendering of a capture document (header + per-transaction
    lines + totals). Pure; reads the validated capture dict and never raises. This is the
    single reusable text builder shared by the persisted .txt artifact AND the app's
    transient capture .txt download."""
    cap = cap or {}
    hdr = cap.get("header") or {}
    sup = hdr.get("supplier") or {}
    cust = hdr.get("customer") or {}
    inv = hdr.get("invoice") or {}
    tot = cap.get("totals") or {}

    def v(x):
        return "" if x is None else str(x)

    out = ["AI VISION CAPTURE DOCUMENT (advisory — verify against the PDF before confirming)",
           "=" * 78, "",
           "SUPPLIER",
           f"  name        : {v(sup.get('name'))}",
           f"  VAT number  : {v(sup.get('vat_number'))}",
           f"  address     : {v(sup.get('address'))}",
           f"  country     : {v(sup.get('country'))}", "",
           "CUSTOMER",
           f"  name        : {v(cust.get('name'))}",
           f"  VAT number  : {v(cust.get('vat_number'))}",
           f"  account/card: {v(cust.get('account_or_card_no'))}", "",
           "INVOICE",
           f"  number      : {v(inv.get('number'))}",
           f"  issue date  : {v(inv.get('issue_date'))}",
           f"  due date    : {v(inv.get('due_date'))}",
           f"  currency    : {v(inv.get('currency'))}",
           f"  exch. rate  : {v(inv.get('exchange_rate'))}", "",
           "TRANSACTIONS", "-" * 78]
    for i, ln in enumerate(cap.get("lines") or [], 1):
        out.append(f"  [{i}] {v(ln.get('date'))} {v(ln.get('time'))} "
                   f"{v(ln.get('station_name'))} / {v(ln.get('city'))} / {v(ln.get('country'))}")
        # entity of supply for THIS line — only shown when the capture marked it per-country
        # specific (it can differ by country on cross-border statements).
        if ln.get("supplier_name") or ln.get("supplier_vat"):
            out.append(f"      supply entity={v(ln.get('supplier_name'))} "
                       f"supply VAT={v(ln.get('supplier_vat'))}")
        out.append(f"      product={v(ln.get('product'))} qty={v(ln.get('quantity'))}"
                   f"{v(ln.get('unit'))} unit_price={v(ln.get('unit_price'))} "
                   f"discount={v(ln.get('discount'))}")
        out.append(f"      net={v(ln.get('net'))} vat_rate={v(ln.get('vat_rate'))} "
                   f"vat={v(ln.get('vat'))} gross={v(ln.get('gross'))} "
                   f"card={v(ln.get('card_no'))} receipt={v(ln.get('receipt_no'))}")
    out += ["", "TOTALS",
            f"  net total     : {v(tot.get('net_total'))}",
            f"  discount total: {v(tot.get('discount_total'))}",
            f"  VAT total     : {v(tot.get('vat_total'))}",
            f"  gross total   : {v(tot.get('gross_total'))}"]
    return "\n".join(out) + "\n"


def _base_name(source_name, upload_sha256):
    """A stable display base for the stored filenames. Prefer the upload's filename stem;
    fall back to a short sha tag so the artifact is still identifiable."""
    stem = os.path.splitext(os.path.basename(source_name or ""))[0].strip()
    if stem:
        return stem
    if upload_sha256:
        return f"capture-{str(upload_sha256)[:12]}"
    return "capture"


def persist(draft, upload_sha256, source_name=None, backend=None, actor=None):
    """Persist a vision draft's CAPTURE DOCUMENT as a durable JSON + text pair in the data
    lake, linked to the ORIGINAL upload by ``upload_sha256`` (the upload's raw_upload sha).

    Returns ``{"json": locator, "text": locator, "upload_sha256": sha}`` on success, or
    None when there was nothing to persist (no `capture` doc / no upload sha) or on any
    failure. BEST-EFFORT — NEVER raises: a data_lake error is logged and swallowed so it
    can never break capture or review. Idempotent (data_lake dedups by content sha)."""
    try:
        cap = (draft or {}).get("capture") if isinstance(draft, dict) else None
        if not isinstance(cap, dict) or not cap:
            return None
        sha = (str(upload_sha256) or "").strip()
        if not sha:
            # Without the upload sha the artifact cannot be paired with the original PDF;
            # the whole point is the link, so skip rather than store an orphan.
            log.debug("capture_file.persist: no upload sha256 — skipping (nothing to link)")
            return None

        import data_lake

        base = _base_name(source_name, sha)
        supplier = (draft or {}).get("supplier")
        period = ((draft or {}).get("statement_date") or "")[:7] or None
        meta = {
            "upload_sha256": sha,                  # the LINK to the original PDF (raw_upload)
            "supplier": supplier,
            "statement_ref": (draft or {}).get("statement_ref"),
            "backend": backend or (draft or {}).get("backend"),
            "lines": len(cap.get("lines") or []),
            "corrected": bool((draft or {}).get("corrections")),
            "correction_status": (draft or {}).get("correction_status"),
        }

        json_blob = json.dumps(cap, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        text_blob = build_text(cap).encode("utf-8")

        json_loc = data_lake.put(json_blob, base + ".capture.json", kind=KIND,
                                 supplier=supplier, period=period,
                                 source_name=source_name, meta=dict(meta, format="json"))
        text_loc = data_lake.put(text_blob, base + ".capture.txt", kind=KIND,
                                 supplier=supplier, period=period,
                                 source_name=source_name, meta=dict(meta, format="text"))

        # Audit: a capture artifact was saved — field COUNTS + the link sha only, NEVER the
        # payload bytes/keys or any secret. Best-effort; an audit failure never breaks save.
        try:
            import audit, auth
            scon = auth.connect()
            try:
                audit.record_event(scon, "capture_file", sha, "CAPTURE_FILE_SAVED",
                                   {"upload_sha256": sha, "supplier": supplier,
                                    "statement_ref": meta["statement_ref"],
                                    "lines": meta["lines"],
                                    "correction_status": meta["correction_status"],
                                    "actor": actor})
            finally:
                scon.close()
        except Exception as e:
            log.warning("capture_file audit failed (artifact saved) for %s: %s", sha[:12], e)

        return {"json": json_loc, "text": text_loc, "upload_sha256": sha}
    except Exception as e:
        # NEVER let persisting the second file break capture/review.
        log.warning("capture_file.persist failed — capture/review unaffected: %s", e)
        return None


def for_upload(upload_sha256):
    """All persisted capture artifacts (metadata rows) linked to an upload sha256, newest
    first. Read-only; NEVER raises -> []. Each row is a data_lake metadata dict with the
    link in its ``meta`` JSON."""
    sha = (str(upload_sha256) or "").strip()
    if not sha:
        return []
    try:
        import data_lake
        out = []
        for r in data_lake.query(kind=KIND, limit=2000):
            try:
                m = json.loads(r["meta"]) if r.get("meta") else {}
            except Exception:
                m = {}
            if isinstance(m, dict) and m.get("upload_sha256") == sha:
                r = dict(r)
                r["_meta"] = m
                out.append(r)
        return out                                 # data_lake.query already returns id DESC
    except Exception as e:
        log.warning("capture_file.for_upload failed for %s: %s", sha[:12], e)
        return []


def latest_for_upload(upload_sha256):
    """The newest persisted JSON + text artifact locators linked to an upload sha256, as
    ``{"json": {...row}, "text": {...row}}`` (each value is the data_lake metadata row, or
    absent when that format is missing). Returns {} when none. NEVER raises."""
    rows = for_upload(upload_sha256)
    out = {}
    for r in rows:                                 # newest first -> first seen wins per format
        fmt = (r.get("_meta") or {}).get("format")
        if fmt in ("json", "text") and fmt not in out:
            out[fmt] = r
    return out


def has_capture(upload_sha256):
    """True when at least one persisted capture artifact is linked to this upload sha256."""
    return bool(for_upload(upload_sha256))
