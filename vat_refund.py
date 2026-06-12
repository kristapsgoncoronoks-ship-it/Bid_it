"""
VAT REFUND MODULE (EU 2008/9/EC)
Builds refund applications per ENTITY x REFUND COUNTRY x PERIOD (Q1-Q4, YEAR)
from fuel_history.db, applies minimum thresholds, tracks application status,
and exports an Excel claim workbook with invoice-level detail per stream.

Monthly/quarterly use:
    python3 vat_refund.py                       -> matrix + VAT_Refund_Claims_<year>.xlsx
    python3 vat_refund.py --set-status "Jupiter Plus AS" Sweden 2026-Q2 submitted
Statuses: draft -> ready -> submitted -> approved -> paid (free text allowed)
"""
import sqlite3, sys, collections
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
import supplier_db, customer_db, audit, money
import dbtune
from vat_config import (GOODS_CODE,
                        MIN_QUARTER, MIN_ANNUAL, DEADLINE_FMT,
                        LOCAL_CCY_INPUT, COMPLIANCE_NOTES)

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"

def quarter(period):           # '2026-05' -> '2026-Q2'
    y, m = period.split("-")
    return f"{y}-Q{(int(m)-1)//3+1}"

def q_months(per):             # '2026-Q2' -> Apr-Jun; '2026-YEAR' -> all 12 months
    if per.endswith("-YEAR"):
        y = per.split("-")[0]
        return [f"{y}-{m:02d}" for m in range(1, 13)]
    y, q = per.split("-Q")
    return [f"{y}-{m:02d}" for m in range((int(q)-1)*3+1, (int(q)-1)*3+4)]

_SCHEMA_READY = set()   # DB files whose schema is set up this process

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    dbtune.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)   # audit triggers call ffs_actor(); register it every connect
    if DB != ":memory:" and DB in _SCHEMA_READY:
        return con
    con.execute("""CREATE TABLE IF NOT EXISTS vat_applications (
        entity TEXT, refund_country TEXT, ref_period TEXT,
        vat_eur REAL, vat_local REAL, currency TEXT,
        status TEXT DEFAULT 'draft', updated TEXT DEFAULT CURRENT_TIMESTAMP,
        submitted_date TEXT, approved_date TEXT, paid_date TEXT, paid_amount REAL,
        PRIMARY KEY (entity, refund_country, ref_period))""")
    con.execute("""CREATE TABLE IF NOT EXISTS vat_claimed_invoices (
        entity TEXT, refund_country TEXT, supplier TEXT, invoice_ref TEXT,
        ref_period TEXT, locked_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (entity, refund_country, supplier, invoice_ref))""")
    con.execute("""CREATE TABLE IF NOT EXISTS invoice_documents (
        id INTEGER PRIMARY KEY,
        entity TEXT, supplier TEXT, invoice_ref TEXT,
        filename TEXT, stored_path TEXT, sha256 TEXT, size INTEGER,
        kind TEXT DEFAULT 'original_pdf', uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (entity, supplier, invoice_ref, sha256))""")
    audit.install_audit(con, ["vat_applications", "vat_claimed_invoices", "invoice_documents"])
    for ddl in ("ALTER TABLE invoice_documents ADD COLUMN backend TEXT DEFAULT 'local'",
                "ALTER TABLE invoice_documents ADD COLUMN web_url TEXT",
                # our fee: rate FROZEN at submission, fee CHARGED when refund is paid
                "ALTER TABLE vat_applications ADD COLUMN fee_eur REAL",
                "ALTER TABLE vat_applications ADD COLUMN fee_pct REAL",
                "ALTER TABLE vat_applications ADD COLUMN fee_min REAL",
                "ALTER TABLE vat_applications ADD COLUMN fee_billed_date TEXT",
                # settlement: where the refund landed + the fee invoice once issued
                "ALTER TABLE vat_applications ADD COLUMN payout_to TEXT",
                "ALTER TABLE vat_applications ADD COLUMN fee_invoice_no TEXT",
                "ALTER TABLE vat_applications ADD COLUMN fee_invoice_date TEXT"):
        try: con.execute(ddl)
        except Exception: pass
    if DB != ":memory:":
        _SCHEMA_READY.add(DB)
    return con

DOCDIR = f"{WORKDIR}/documents"

def attach_document(con, ent, sup, ref, src_path=None, file_bytes=None,
                    filename=None, kind="original_pdf", country=None, period=None):
    """Attach a physical document (original PDF or scan) to an invoice.
    Returns (ok, message). Hash-verified; duplicate file on the SAME invoice is
    skipped; the SAME file on a DIFFERENT invoice raises a warning (likely a
    wrong attachment) but is allowed with the warning recorded in the message.
    The file is archived under the logical vault tree
    <Customer> <RegNo>/<Year>/<Country>/<Claim period>/<file>; country/period are
    looked up from the invoice registry when not passed in."""
    import hashlib, os
    import doc_storage
    if src_path:
        file_bytes = open(src_path, "rb").read()
        filename = filename or os.path.basename(src_path)
    sha = hashlib.sha256(file_bytes).hexdigest()
    dup_same = con.execute("""SELECT id FROM invoice_documents WHERE entity=? AND supplier=?
                              AND invoice_ref=? AND sha256=?""", (ent, sup, ref, sha)).fetchone()
    if dup_same:
        return True, f"already attached (identical file, sha {sha[:8]}) - skipped"
    elsewhere = con.execute("""SELECT invoice_ref FROM invoice_documents WHERE sha256=?
                               AND NOT (entity=? AND supplier=? AND invoice_ref=?)""",
                            (sha, ent, sup, ref)).fetchone()
    warn = (f" | WARNING: identical file already attached to invoice "
            f"{elsewhere['invoice_ref']} - verify correct document" if elsewhere else "")
    # resolve the metadata that organises the archive (country/period of the
    # invoice, and the customer's registration number) — all best-effort.
    if country is None or period is None:
        try:
            scon = supplier_db.connect()
            inv = scon.execute("""SELECT country, period FROM supplier_invoices
                                  WHERE supplier=? AND invoice_no=?""", (sup, ref)).fetchone()
            scon.close()
            if inv:
                country = country if country is not None else inv["country"]
                period = period if period is not None else inv["period"]
        except Exception:
            pass
    cust_name, reg = ent, None
    try:
        c = customer_db.get_customer(ent) or {}
        cust_name = c.get("company_name") or ent
        reg = c.get("reg_number")
    except Exception:
        pass
    safe = doc_storage.invoice_vault_path(cust_name, reg, country, period, filename)
    be = doc_storage.backend(DOCDIR)
    stored, web_url = be.put(safe, file_bytes)
    con.execute("""INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename,
                   stored_path, sha256, size, kind, backend, web_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ent, sup, ref, filename, stored, sha, len(file_bytes), kind, be.name, web_url))
    con.commit()
    return True, (f"attached {filename} ({len(file_bytes):,} B, sha {sha[:8]}, {kind}, "
                  f"storage: {be.name}" + (f", {web_url}" if web_url else "")) + warn

def docs_for(con, ent, sup, ref):
    return con.execute("""SELECT * FROM invoice_documents WHERE entity=? AND supplier=?
                          AND invoice_ref=? ORDER BY uploaded_at""", (ent, sup, ref)).fetchall()

def file_documents_for_claim(con, ent, ctry, period):
    """Re-file the documents of the invoices ACTUALLY locked into this claim
    (vat_claimed_invoices for this exact ref_period) under the claim's period
    folder, so the vault mirrors the real claim composition — dynamically.

    This is what makes merging dynamic: claims are filed per invoice, not per
    calendar quarter. If Q1/Q2 are low and get pulled into the yearly claim while
    Q3 and Q4 are claimed quarterly, only the Q1/Q2 (and any late) invoices in the
    YEAR claim move to 'Annual'; the Q3/Q4 documents stay under their own quarter
    because they are locked to '<year>-Q3'/'<year>-Q4'. A quarterly claim is
    typically a no-op (its documents are already in the matching quarter folder).

    Re-filed per document in a DB-safe order — write the new copy, point the row at
    it, THEN delete the old copy — so a crash never leaves the database referencing
    a missing file. Idempotent. Returns the number of documents moved."""
    import doc_storage
    try:
        c = customer_db.get_customer(ent) or {}
        cust_name = c.get("company_name") or ent
        reg = c.get("reg_number")
    except Exception:
        cust_name, reg = ent, None
    locked = con.execute("""SELECT supplier, invoice_ref FROM vat_claimed_invoices
                            WHERE entity=? AND refund_country=? AND ref_period=?""",
                         (ent, ctry, period)).fetchall()
    moved = 0
    for lk in locked:
        for d in docs_for(con, ent, lk["supplier"], lk["invoice_ref"]):
            old = d["stored_path"]
            # filing folder follows the CLAIM's period (Annual for a yearly claim),
            # not the invoice's calendar quarter.
            new_name = doc_storage.invoice_vault_path(cust_name, reg, ctry, period, d["filename"])
            data = doc_storage.get_bytes(old, DOCDIR)
            new_loc, web_url = doc_storage.copy_to(new_name, data, DOCDIR)
            if str(new_loc) == str(old):
                continue                                # already in the right folder
            con.execute("UPDATE invoice_documents SET stored_path=?, web_url=? WHERE id=?",
                        (new_loc, web_url, d["id"]))
            con.commit()                                # row now points at the new copy
            try:
                doc_storage.delete(old, DOCDIR)         # safe to drop the old copy
            except Exception:
                pass                                    # orphan at worst, never lost
            moved += 1
    return moved

def verify_documents(con=None):
    """Integrity check for the physical documents (PDF/ZIP files): re-read each
    stored file and compare its SHA-256 to the hash recorded when it was attached.
    Detects corrupted or missing/jeopardised files. Returns (rows, summary)."""
    import hashlib
    import doc_storage
    close = False
    if con is None:
        con = connect(); close = True
    rows, ok, corrupt, missing = [], 0, 0, 0
    for r in con.execute("""SELECT entity, supplier, invoice_ref, filename, stored_path,
                            sha256, size, backend FROM invoice_documents ORDER BY id"""):
        status, detail = "OK", ""
        try:
            data = doc_storage.get_bytes(r["stored_path"], DOCDIR)
            actual = hashlib.sha256(data).hexdigest()
            if actual != r["sha256"]:
                status, detail = "CORRUPT", f"hash {actual[:8]} != recorded {r['sha256'][:8]}"
                corrupt += 1
            else:
                ok += 1
        except Exception as e:
            status, detail = "MISSING", str(e)[:140]
            missing += 1
        rows.append({"entity": r["entity"], "supplier": r["supplier"],
                     "invoice_ref": r["invoice_ref"], "filename": r["filename"],
                     "sha256": r["sha256"], "backend": r["backend"],
                     "status": status, "detail": detail})
    if close:
        con.close()
    return rows, {"total": len(rows), "ok": ok, "corrupt": corrupt, "missing": missing}

LOCKING = ("submitted", "approved", "paid")

def stream_invoices(con, ent, ctry, period, cache=None):
    """Distinct (supplier, invoice_ref) used by a claim stream."""
    return sorted({(L["supplier"], L["invoice"]) for L in invoice_lines(con, ent, ctry, period, cache)})

def docs_index(con):
    """One-query set of (entity, supplier, invoice_ref) that have >=1 document.
    Lets callers check document coverage without an N+1 of docs_for()."""
    return {(r["entity"], r["supplier"], r["invoice_ref"])
            for r in con.execute(
                "SELECT DISTINCT entity, supplier, invoice_ref FROM invoice_documents")}

def lock_state(con, ent, ctry, sup, ref):
    r = con.execute("""SELECT ref_period FROM vat_claimed_invoices WHERE entity=? AND
                       refund_country=? AND supplier=? AND invoice_ref=?""",
                    (ent, ctry, sup, ref)).fetchone()
    return r["ref_period"] if r else None

def set_status(con, ent, ctry, period, new):
    """Guarded status transition enforcing one-invoice-one-submission.
    Returns (ok, message).

    The whole transition (duplicate checks + invoice-lock acquisition + the
    application upsert) runs as ONE transaction. Lock acquisition uses a plain
    INSERT (not INSERT OR IGNORE) so a lost race surfaces as an IntegrityError on
    the UNIQUE(entity, refund_country, supplier, invoice_ref) constraint; on that
    we roll back and abort the status change rather than silently proceeding as if
    we had won the lock."""
    # A tracked customer must be ACTIVATED (onboarding documents complete) before a
    # claim can be submitted on their behalf. Untracked entities are not gated.
    if new in LOCKING and customer_db.is_active(ent) is False:
        return False, (f"customer '{ent}' is not activated — complete the trade registry, "
                       f"bank account and signed contract on the Customers page first")
    # Each refund country is activated separately (request + receive its documents).
    # Once activation has been started for a country it must reach 'active' to submit.
    if new in LOCKING and customer_db.country_active(ent, ctry) is False:
        return False, (f"refund country '{ctry}' is not activated for '{ent}' — request and "
                       f"receive the country documents (power of attorney) on the Customers page")
    try:
        # Open an immediate transaction so concurrent claimants serialize on write.
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        # Already inside a transaction (e.g. autocommit off / nested caller) - fine.
        pass
    try:
        cur = con.execute("""SELECT status FROM vat_applications WHERE entity=? AND
                             refund_country=? AND ref_period=?""", (ent, ctry, period)).fetchone()
        cur = cur["status"] if cur else "draft"
        is_annual = str(period).endswith("-YEAR")
        claim_set = None
        if new in LOCKING:
            if cur not in LOCKING:  # entering locked state -> validate & lock invoices
                invs = stream_invoices(con, ent, ctry, period)
                bad = [f"{s}:{r}" for s, r in invs if "INPUT" in r or r.startswith("ALL:")]
                if bad:
                    con.rollback()
                    return False, "BLOCKED - unresolved invoice refs (fill INPUTs first): " + "; ".join(bad)
                # A YEARLY claim is the mop-up for periods NOT already claimed
                # quarterly: invoices already locked to a quarter are EXCLUDED from
                # the annual claim (not a conflict). A quarterly claim still treats
                # any overlap as a duplicate and blocks.
                conflicts, claim_set = [], []
                for s, r in invs:
                    other = lock_state(con, ent, ctry, s, r)
                    if other and other != period:
                        if is_annual:
                            continue                    # claimed in its own quarter
                        conflicts.append(f"{s} invoice {r} already claimed in {other}")
                    else:
                        claim_set.append((s, r))
                if conflicts:
                    con.rollback()
                    return False, "BLOCKED - duplicate submission: " + "; ".join(conflicts)
                if is_annual and not claim_set:
                    con.rollback()
                    return False, ("BLOCKED - nothing to claim annually: every invoice for this "
                                   "year is already claimed in a quarterly filing")
                nodoc = [f"{s} {r}" for s, r in claim_set if not docs_for(con, ent, s, r)]
                if nodoc:
                    con.rollback()
                    return False, ("BLOCKED - physical document missing (attach original PDF "
                                   "or scan first): " + "; ".join(nodoc))
                for s, r in claim_set:
                    try:
                        con.execute("""INSERT INTO vat_claimed_invoices
                                       (entity, refund_country, supplier, invoice_ref, ref_period)
                                       VALUES (?,?,?,?,?)""", (ent, ctry, s, r, period))
                    except sqlite3.IntegrityError:
                        # Another claim acquired this invoice lock between our check
                        # and our insert. Abort the entire transition.
                        con.rollback()
                        other = lock_state(con, ent, ctry, s, r)
                        return False, ("BLOCKED - duplicate submission (concurrent claim won the "
                                       f"lock): {s} invoice {r} already claimed"
                                       + (f" in {other}" if other else "") + " - retry not needed")
        elif new in ("rejected", "withdrawn"):
            con.execute("""DELETE FROM vat_claimed_invoices WHERE entity=? AND refund_country=?
                           AND ref_period=?""", (ent, ctry, period))
        elif cur in LOCKING:
            con.rollback()
            return False, (f"BLOCKED - application is '{cur}' and holds invoice locks; "
                           "use 'rejected' or 'withdrawn' to release before reverting.")
        stamp = {"submitted": "submitted_date", "approved": "approved_date", "paid": "paid_date"}.get(new)
        con.execute("""INSERT INTO vat_applications (entity, refund_country, ref_period, status)
                       VALUES (?,?,?,?) ON CONFLICT(entity, refund_country, ref_period)
                       DO UPDATE SET status=excluded.status, updated=CURRENT_TIMESTAMP""",
                    (ent, ctry, period, new))
        if stamp:
            con.execute(f"UPDATE vat_applications SET {stamp}=date('now') WHERE entity=? "
                        "AND refund_country=? AND ref_period=?", (ent, ctry, period))
        # Freeze the fee RATE onto the claim the moment it is first submitted; once
        # locked the rate can no longer be adjusted (% / minimum changes only affect
        # un-submitted declarations).
        if new in LOCKING and cur not in LOCKING:
            if is_annual and claim_set is not None:
                # a yearly claim only carries the invoices NOT already claimed
                # quarterly (the deferred quarters + any late invoices), so freeze
                # the VAT from exactly that set rather than the whole calendar year.
                keys = set(claim_set)
                ve = money.f2(sum(L["vat_eur"] for L in invoice_lines(con, ent, ctry, period)
                                  if (L["supplier"], L["invoice"]) in keys))
            else:
                months = q_months(period)
                ph = ",".join("?" * len(months))
                ve = con.execute(f"SELECT ROUND(SUM(vat_eur),2) FROM transactions WHERE entity=? "
                                 f"AND country=? AND period IN ({ph})", [ent, ctry] + months).fetchone()[0] or 0.0
            fpct, fmin = customer_db.fee_for(ent, ctry)
            fee, _basis = customer_db.compute_fee(ve, fpct, fmin)
            con.execute("""UPDATE vat_applications SET vat_eur=?, fee_eur=?, fee_pct=?, fee_min=?
                           WHERE entity=? AND refund_country=? AND ref_period=?""",
                        (ve, fee, fpct, fmin, ent, ctry, period))
        # CHARGE the fee for services only when the money is refunded (status=paid):
        # recompute on the refunded amount (paid_amount, else the claimed VAT) at the
        # frozen rate and stamp the billing date.
        if new == "paid":
            r = con.execute("""SELECT vat_eur, paid_amount, fee_pct, fee_min FROM vat_applications
                               WHERE entity=? AND refund_country=? AND ref_period=?""",
                            (ent, ctry, period)).fetchone()
            base = (r["paid_amount"] if r and r["paid_amount"] else (r["vat_eur"] if r else 0)) or 0
            fee, _b = customer_db.compute_fee(base, (r["fee_pct"] if r else 0) or 0,
                                              (r["fee_min"] if r else 0) or 0)
            con.execute("""UPDATE vat_applications SET fee_eur=?, fee_billed_date=date('now'),
                           payout_to=COALESCE(payout_to, ?)
                           WHERE entity=? AND refund_country=? AND ref_period=?""",
                        (fee, customer_db.payout_route(ent), ent, ctry, period))
        con.commit()
    except Exception:
        con.rollback()
        raise
    # Keep the vault in step with the claim: file the documents of the invoices
    # actually locked into THIS claim under the claim's period folder. A quarterly
    # claim is a no-op (docs already in that quarter); a yearly claim pulls its
    # deferred/late invoices into 'Annual'. Done AFTER commit and outside the
    # transaction (file moves aren't transactional); best-effort so it never blocks
    # the already-recorded status change.
    if new in LOCKING and cur not in LOCKING:
        try:
            file_documents_for_claim(con, ent, ctry, period)
        except Exception:
            pass
    return True, f"status -> {new}" + (" (invoices locked)" if new in LOCKING and cur not in LOCKING
                                       else " (locks released)" if new in ("rejected","withdrawn") else "")

def settlement(payout_to, refund_eur, fee_eur):
    """How the fee is settled. payout_to='customer' -> we invoice the fee (receivable);
    payout_to='us' -> we deduct the fee and remit the net to the customer."""
    refund = float(refund_eur or 0); fee = money.f2(fee_eur or 0)
    if payout_to == "us":
        return {"route": "us", "refund": money.f2(refund), "fee": fee,
                "net_to_customer": money.f2(refund - fee), "fee_receivable": 0.0}
    return {"route": "customer", "refund": money.f2(refund), "fee": fee,
            "net_to_customer": 0.0, "fee_receivable": fee}

def issue_fee_invoice(con, ent, ctry, period):
    """Assign a fee-invoice number/date to a paid claim. Returns (ok, number_or_msg)."""
    r = con.execute("""SELECT fee_billed_date, fee_invoice_no FROM vat_applications
                       WHERE entity=? AND refund_country=? AND ref_period=?""",
                    (ent, ctry, period)).fetchone()
    if not r:
        return (False, "no such claim")
    if not r["fee_billed_date"]:
        return (False, "fee not charged yet — the refund must be paid first")
    if r["fee_invoice_no"]:
        return (True, r["fee_invoice_no"])               # already issued (idempotent)
    yr = str(period).split("-")[0]
    n = con.execute("SELECT COUNT(*) FROM vat_applications WHERE fee_invoice_no IS NOT NULL"
                    ).fetchone()[0] + 1
    inv_no = f"F{yr}-{n:04d}"
    con.execute("""UPDATE vat_applications SET fee_invoice_no=?, fee_invoice_date=date('now')
                   WHERE entity=? AND refund_country=? AND ref_period=?""",
                (inv_no, ent, ctry, period))
    con.commit()
    return (True, inv_no)

def submission_readiness(con, ent, ctry, period):
    """Read-only check of whether a claim CAN be submitted. Returns (ready, [issues]).
    Mirrors the blocking conditions in set_status without writing anything."""
    issues = []
    if customer_db.is_active(ent) is False:
        issues.append("customer not activated")
    if customer_db.country_active(ent, ctry) is False:
        issues.append(f"refund country '{ctry}' not activated")
    invs = stream_invoices(con, ent, ctry, period)
    bad = [r for s, r in invs if "INPUT" in r or r.startswith("ALL:")]
    if bad:
        issues.append(f"{len(bad)} unresolved invoice ref(s)")
    nodoc = [(s, r) for s, r in invs if not docs_for(con, ent, s, r)]
    if nodoc:
        issues.append(f"{len(nodoc)} invoice(s) missing documents")
    conflicts = [(s, r) for s, r in invs
                 if (lock_state(con, ent, ctry, s, r) or period) != period]
    if conflicts:
        issues.append(f"{len(conflicts)} invoice(s) locked by another claim")
    return (len(issues) == 0, issues)

def claims_overview(year):
    """For the VAT-refund 'can we submit?' report: every claimable quarter split
    into TO-SUBMIT (with a readiness verdict) and OPEN (submitted/approved, aging)."""
    import datetime
    con = connect()
    matrix = claim_matrix(con, year)
    sts = {(r["entity"], r["refund_country"], r["ref_period"]): r["status"]
           for r in con.execute("SELECT entity, refund_country, ref_period, status FROM vat_applications")}
    subm = {(r["entity"], r["refund_country"], r["ref_period"]): r["submitted_date"]
            for r in con.execute("SELECT entity, refund_country, ref_period, submitted_date FROM vat_applications")}
    today = datetime.date.today()
    to_submit, open_claims = [], []
    for m in matrix:
        if m["period"].endswith("YEAR") or (m["vat_eur"] or 0) <= 0:
            continue
        key = (m["entity"], m["country"], m["period"])
        status = sts.get(key, "draft")
        if status in ("submitted", "approved"):
            age = ""
            sd = subm.get(key)
            if sd:
                try: age = (today - datetime.date.fromisoformat(sd)).days
                except ValueError: pass
            open_claims.append(dict(entity=m["entity"], country=m["country"], period=m["period"],
                                    vat_eur=m["vat_eur"], status=status, submitted=sd, age_days=age))
        elif status not in ("paid", "rejected", "withdrawn"):
            ready, issues = submission_readiness(con, m["entity"], m["country"], m["period"])
            if not m["verdict"].startswith("READY"):
                issues = issues + [m["verdict"].split(" (")[0].lower()]
            ready = (len(issues) == 0)
            to_submit.append(dict(entity=m["entity"], country=m["country"], period=m["period"],
                                  vat_eur=m["vat_eur"], verdict=m["verdict"],
                                  ready=ready, issues=issues, missing=m["missing"]))
    con.close()
    return {"to_submit": to_submit, "open": open_claims}

def claim_matrix(con, year):
    """All streams for the year: per (entity, country) give Q1..Q4 + YEAR VAT, currency, status."""
    rows = con.execute("""
        SELECT entity, country, currency, period,
               ROUND(SUM(vat_eur),2) ve, ROUND(SUM(vat_local),2) vl,
               COUNT(*) n
        FROM transactions WHERE period LIKE ? GROUP BY entity, country, period""",
        (f"{year}-%",)).fetchall()
    streams = collections.defaultdict(lambda: {"qs": collections.defaultdict(
                                                   lambda: [money.D(0), money.D(0), 0]),
                                               "ccy": "EUR"})
    loaded_periods = set()
    for r in rows:
        s = streams[(r["entity"], r["country"])]
        q = quarter(r["period"])
        # accumulate VAT exactly as Decimal so the EUR-threshold test below never
        # flips on binary-float noise
        s["qs"][q][0] += money.D(r["ve"]); s["qs"][q][1] += money.D(r["vl"]); s["qs"][q][2] += r["n"]
        s["ccy"] = r["currency"]; loaded_periods.add(r["period"])
    out = []
    for (ent, ctry), s in sorted(streams.items()):
        year_ve = money.q2(sum((v[0] for v in s["qs"].values()), money.D(0)))
        year_vl = money.q2(sum((v[1] for v in s["qs"].values()), money.D(0)))
        for q, (ve, vl, n) in sorted(s["qs"].items()):
            ve = money.q2(ve)            # quarterly VAT, exact cents
            missing = [m for m in q_months(q) if m not in loaded_periods]
            if ve >= MIN_QUARTER: verdict = "READY (>= 400 EUR quarterly min)"
            elif year_ve >= MIN_ANNUAL: verdict = "DEFER TO ANNUAL (below 400, year >= 50)"
            else: verdict = "BELOW ANNUAL MIN - accumulate"
            out.append(dict(entity=ent, country=ctry, period=q, vat_eur=money.f2(ve),
                            vat_local=money.f2(vl), currency=s["ccy"], lines=n,
                            verdict=verdict, missing=missing,
                            home=customer_db.portal(ent),
                            deadline=DEADLINE_FMT.format(year_plus1=int(year)+1)))
        out.append(dict(entity=ent, country=ctry, period=f"{year}-YEAR",
                        vat_eur=money.f2(year_ve), vat_local=money.f2(year_vl),
                        currency=s["ccy"], lines=sum(v[2] for v in s["qs"].values()),
                        verdict=("READY (annual >= 50 EUR)" if year_ve >= MIN_ANNUAL
                                 else "BELOW ANNUAL MIN"),
                        missing=[], home=customer_db.portal(ent),
                        deadline=DEADLINE_FMT.format(year_plus1=int(year)+1)))
    return out

def invoice_lines(con, ent, ctry, qtr, cache=None):
    """Invoice-level detail for one claim: prefer per-invoice split via the note column,
    fall back to registry invoice(s) carrying the country aggregate.

    `cache` (optional dict) lets a caller rendering many claims share one
    supplier-DB connection and memoize per-(supplier, country) issuer/invoice
    lookups across calls, avoiding an N+1 of connections/queries."""
    own_cache = cache is None
    cache = cache if cache is not None else {}
    scon = cache.get("_scon")
    if scon is None:
        scon = supplier_db.connect(); cache["_scon"] = scon
    months = q_months(qtr)
    sups = [r[0] for r in con.execute(
        """SELECT DISTINCT supplier FROM transactions WHERE entity=? AND country=?
           AND period IN (%s)""" % ",".join("?"*len(months)), [ent, ctry]+months)]
    lines = []
    for sup in sups:
        ck = (sup, ctry)
        if ck in cache:
            issuer, vatid, vnote, regs = cache[ck]
        else:
            issuer, vatid, vnote = supplier_db.get_issuer(sup, ctry, con=scon)
            regs = supplier_db.get_invoices(sup, ctry, con=scon)
            cache[ck] = (issuer, vatid, vnote, regs)
        refs = [r[0] for r in regs]
        rows = con.execute(
            """SELECT note, product_group, ROUND(SUM(net_eur),2) net, ROUND(SUM(vat_eur),2) vat,
                      ROUND(SUM(net_local),2) netl, ROUND(SUM(vat_local),2) vatl, currency
               FROM transactions WHERE entity=? AND country=? AND supplier=?
               AND period IN (%s) GROUP BY note, product_group""" % ",".join("?"*len(months)),
            [ent, ctry, sup]+months).fetchall()
        by_inv = collections.defaultdict(lambda: collections.defaultdict(lambda: [0,0,0,0,""]))
        for r in rows:
            inv = next((ref for ref in refs if r["note"] and r["note"].split("/")[0] in r["note"]
                        and ref.startswith(r["note"].split(" ")[0])), None)
            inv = inv or next((ref for ref in refs if r["note"] and ref.split("/")[0] in r["note"]), None)
            inv = inv or (refs[0] if len(refs) == 1 else "ALL: " + " + ".join(refs))
            a = by_inv[inv][r["product_group"]]
            a[0] += r["net"]; a[1] += r["vat"]; a[2] += r["netl"]; a[3] += r["vatl"]; a[4] = r["currency"]
        dates = dict(regs)
        for inv, prods in by_inv.items():
            for pg, (net, vat, netl, vatl, ccy) in sorted(prods.items()):
                code, desc = GOODS_CODE.get(pg, ("9", "Other"))
                lines.append(dict(supplier=sup, issuer=issuer,
                                  vat_id=vatid or "INPUT: " + vnote,
                                  invoice=inv, inv_date=dates.get(inv, ""),
                                  code=code, desc=desc, product=pg, currency=ccy,
                                  net_local=money.f2(netl), vat_local=money.f2(vatl),
                                  net_eur=money.f2(net), vat_eur=money.f2(vat)))
    if own_cache and cache.get("_scon") is not None:
        cache["_scon"].close()
    return lines

# ---------------------------------------------------------------- Excel pack
def build_workbook(con, year):
    matrix = claim_matrix(con, year)
    wb = Workbook()
    fillH = PatternFill("solid", start_color="5B3A8E")
    fillY = PatternFill("solid", start_color="FFF2CC")
    bw = Font(bold=True, color="FFFFFF", name="Arial", size=9)
    b10 = Font(bold=True, name="Arial", size=10)
    norm = Font(name="Arial", size=9)
    it8 = Font(italic=True, name="Arial", size=8)
    def head(ws, row):
        for c in ws[row]:
            if c.value: c.font = bw; c.fill = fillH; c.alignment = Alignment(horizontal="center", wrap_text=True)

    ws = wb.active; ws.title = "Overview"
    ws["A1"] = f"VAT REFUND APPLICATIONS {year} - EU Directive 2008/9/EC (deadline {int(year)+1}-09-30)"
    ws["A1"].font = Font(bold=True, size=12, name="Arial")
    ws.append([])
    ws.append(["Entity","Refund country","Period","VAT EUR","VAT local","Ccy","Txn lines",
               "Threshold verdict","Months not yet loaded","Home portal","Status"])
    head(ws, 3)
    r = 4
    for m in matrix:
        st = con.execute("""SELECT status FROM vat_applications WHERE entity=? AND
                            refund_country=? AND ref_period=?""",
                         (m["entity"], m["country"], m["period"])).fetchone()
        status = st["status"] if st else "draft"
        con.execute("""INSERT INTO vat_applications (entity, refund_country, ref_period,
                       vat_eur, vat_local, currency, status) VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(entity, refund_country, ref_period) DO UPDATE SET
                       vat_eur=excluded.vat_eur, vat_local=excluded.vat_local""",
                    (m["entity"], m["country"], m["period"], m["vat_eur"], m["vat_local"],
                     m["currency"], status))
        ws.append([m["entity"], m["country"], m["period"], m["vat_eur"], m["vat_local"],
                   m["currency"], m["lines"], m["verdict"],
                   ", ".join(m["missing"]) if m["missing"] else "", m["home"], status])
        for c in ws[r]: c.font = norm
        ws.cell(row=r, column=4).number_format = "#,##0.00"
        ws.cell(row=r, column=5).number_format = "#,##0.00"
        if m["missing"]:
            ws.cell(row=r, column=9).fill = fillY
        if (m["entity"], m["country"]) in LOCAL_CCY_INPUT and not m["period"].endswith("YEAR"):
            ws.cell(row=r, column=5).fill = fillY
            ws.cell(row=r, column=5).value = f"INPUT {LOCAL_CCY_INPUT[(m['entity'], m['country'])]}"
        r += 1
    con.commit()
    r += 1
    ws[f"A{r}"] = "Compliance notes:"; ws[f"A{r}"].font = b10
    for i, n in enumerate(COMPLIANCE_NOTES):
        ws[f"B{r+1+i}"] = "- " + n; ws[f"B{r+1+i}"].font = it8
        ws[f"B{r+1+i}"].alignment = Alignment(wrap_text=True)
    for col, w in zip("ABCDEFGHIJK",[26,13,10,11,12,6,9,34,18,30,10]):
        ws.column_dimensions[col].width = w

    # one sheet per quarterly claim stream with VAT > 0
    done = set()
    for m in matrix:
        if m["period"].endswith("YEAR") or m["vat_eur"] <= 0: continue
        key = (m["entity"], m["country"], m["period"])
        if key in done: continue
        done.add(key)
        toks = m["entity"].replace("UAB ","").replace("SIA ","").split()
        sheetname = f"{toks[0][:12]}-{m['country'][:8]}-{m['period'][-2:]}"
        ws2 = wb.create_sheet(sheetname[:31])
        ws2["A1"] = f"CLAIM PACK: {m['entity']} -> {m['country']} -> {m['period']}"
        ws2["A1"].font = Font(bold=True, size=11, name="Arial")
        ws2["A2"] = (f"File via {m['home']}; claim currency {m['currency']}; "
                     f"deadline {m['deadline']}; verdict: {m['verdict']}"
                     + ("; QUARTER INCOMPLETE - missing " + ", ".join(m["missing"]) if m["missing"] else ""))
        ws2["A2"].font = it8
        cust = customer_db.get_customer(m["entity"])
        ws2["A3"] = (f"APPLICANT: {cust['company_name']} | Reg. no: {cust['reg_number']} | "
                     f"VAT: {cust['vat_number']} | Legal address: {cust['legal_address']}")
        ws2["A4"] = f"Refund payout account: {cust['payout']}"
        for cell in ("A3","A4"):
            ws2[cell].font = norm
            if "INPUT" in str(ws2[cell].value):
                ws2[cell].fill = fillY
        ws2.append([])
        ws2.append(["Supplier","Issuer (invoice party)","Issuer VAT ID","Invoice ref","Invoice date",
                    "Goods code","Code description","Product","Ccy","Taxable base (local)",
                    "VAT (local)","Net EUR","VAT EUR","Duplicate control","Document(s) attached"])
        head(ws2, 6)
        rr = 7
        for L in invoice_lines(con, m["entity"], m["country"], m["period"]):
            other = lock_state(con, m["entity"], m["country"], L["supplier"], L["invoice"])
            L["lock"] = ("LOCKED here" if other == m["period"]
                         else f"EXCLUDE - claimed in {other}" if other else "free")
            ws2.append([L["supplier"], L["issuer"], L["vat_id"], L["invoice"], L["inv_date"],
                        L["code"], L["desc"], L["product"], L["currency"],
                        L["net_local"], L["vat_local"], L["net_eur"], L["vat_eur"], L["lock"],
                        "; ".join(f"{d['filename']} [sha {d['sha256'][:8]}]"
                                  for d in docs_for(con, m["entity"], L["supplier"], L["invoice"]))
                        or "MISSING"])
            if not docs_for(con, m["entity"], L["supplier"], L["invoice"]):
                ws2.cell(row=rr, column=15).fill = PatternFill("solid", start_color="F8CBAD")
            if L["lock"].startswith("EXCLUDE"):
                ws2.cell(row=rr, column=14).fill = PatternFill("solid", start_color="F8CBAD")
            for c in ws2[rr]: c.font = norm
            for col in (10,11,12,13): ws2.cell(row=rr, column=col).number_format = "#,##0.00"
            if "INPUT" in str(L["vat_id"]) or "INPUT" in str(L["invoice"]):
                ws2.cell(row=rr, column=3 if "INPUT" in str(L["vat_id"]) else 4).fill = fillY
            rr += 1
        ws2.append(["TOTAL","","","","","","","","",
                    f"=SUM(J7:J{rr-1})", f"=SUM(K7:K{rr-1})", f"=SUM(L7:L{rr-1})", f"=SUM(M7:M{rr-1})"])
        for c in ws2[rr]: c.font = b10
        for col in (10,11,12,13): ws2.cell(row=rr, column=col).number_format = "#,##0.00"
        for col, w in zip("ABCDEFGHIJKLMNO",[8,30,22,22,11,7,26,11,5,13,11,11,11,20,40]):
            ws2.column_dimensions[col].width = w

    path = f"{WORKDIR}/VAT_Refund_Claims_{year}.xlsx"
    wb.save(path)
    return path, matrix

if __name__ == "__main__":
    con = connect()
    if len(sys.argv) >= 5 and sys.argv[1] == "--set-status":
        ent, ctry, per, st = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        ok, msg = set_status(con, ent, ctry, per, st)
        print(("OK: " if ok else "") + msg)
    else:
        year = sys.argv[1] if len(sys.argv) > 1 else "2026"
        path, matrix = build_workbook(con, year)
        print(f"{'Entity':28}{'Country':10}{'Period':10}{'VAT EUR':>10}  Verdict")
        for m in matrix:
            print(f"{m['entity'][:26]:28}{m['country'][:9]:10}{m['period']:10}{m['vat_eur']:>10,.2f}  {m['verdict']}"
                  + ("  [missing: " + ",".join(m['missing']) + "]" if m['missing'] else ""))
        print("saved:", path)
    con.close()


def recovery_report(year=None):
    """Submitted vs approved vs paid, with aging of unpaid submitted claims."""
    con = connect()
    rows = con.execute("""SELECT entity, refund_country, ref_period, vat_eur, status,
        submitted_date, approved_date, paid_date, paid_amount,
        fee_eur, fee_pct, fee_min, fee_billed_date, payout_to, fee_invoice_no, fee_invoice_date
        FROM vat_applications WHERE status IN ('submitted','approved','paid')
        AND (? IS NULL OR ref_period LIKE ?) ORDER BY submitted_date""",
        (year, f"{year}-%" if year else None)).fetchall()
    import datetime
    today = datetime.date.today()
    out = []
    for r in rows:
        age = ""
        if r["status"] in ("submitted", "approved") and r["submitted_date"]:
            try:
                age = (today - datetime.date.fromisoformat(r["submitted_date"])).days
            except ValueError: pass
        out.append(dict(entity=r["entity"], country=r["refund_country"], period=r["ref_period"],
                        vat_eur=r["vat_eur"], status=r["status"], submitted=r["submitted_date"],
                        paid=r["paid_date"], paid_amount=r["paid_amount"], age_days=age,
                        fee_eur=r["fee_eur"], fee_pct=r["fee_pct"], fee_min=r["fee_min"],
                        fee_billed_date=r["fee_billed_date"], payout_to=r["payout_to"],
                        fee_invoice_no=r["fee_invoice_no"], fee_invoice_date=r["fee_invoice_date"]))
    con.close()
    summary = {s: money.fsum(o["vat_eur"] or 0 for o in out if o["status"] == s)
               for s in ("submitted", "approved", "paid")}
    summary["outstanding"] = money.fsum(o["vat_eur"] or 0 for o in out
                                        if o["status"] in ("submitted", "approved"))
    return out, summary
