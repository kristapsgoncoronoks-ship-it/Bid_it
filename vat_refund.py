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

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
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
                "ALTER TABLE invoice_documents ADD COLUMN web_url TEXT"):
        try: con.execute(ddl)
        except Exception: pass
    return con

DOCDIR = f"{WORKDIR}/documents"

def attach_document(con, ent, sup, ref, src_path=None, file_bytes=None,
                    filename=None, kind="original_pdf"):
    """Attach a physical document (original PDF or scan) to an invoice.
    Returns (ok, message). Hash-verified; duplicate file on the SAME invoice is
    skipped; the SAME file on a DIFFERENT invoice raises a warning (likely a
    wrong attachment) but is allowed with the warning recorded in the message."""
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
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in f"{ent}_{sup}_{ref}_{filename}")
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

def stream_invoices(con, ent, ctry, period):
    """Distinct (supplier, invoice_ref) used by a claim stream."""
    return sorted({(L["supplier"], L["invoice"]) for L in invoice_lines(con, ent, ctry, period)})

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
        if new in LOCKING:
            if cur not in LOCKING:  # entering locked state -> validate & lock invoices
                invs = stream_invoices(con, ent, ctry, period)
                bad = [f"{s}:{r}" for s, r in invs if "INPUT" in r or r.startswith("ALL:")]
                if bad:
                    con.rollback()
                    return False, "BLOCKED - unresolved invoice refs (fill INPUTs first): " + "; ".join(bad)
                conflicts = []
                for s, r in invs:
                    other = lock_state(con, ent, ctry, s, r)
                    if other and other != period:
                        conflicts.append(f"{s} invoice {r} already claimed in {other}")
                if conflicts:
                    con.rollback()
                    return False, "BLOCKED - duplicate submission: " + "; ".join(conflicts)
                nodoc = [f"{s} {r}" for s, r in invs if not docs_for(con, ent, s, r)]
                if nodoc:
                    con.rollback()
                    return False, ("BLOCKED - physical document missing (attach original PDF "
                                   "or scan first): " + "; ".join(nodoc))
                for s, r in invs:
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
        con.commit()
    except Exception:
        con.rollback()
        raise
    return True, f"status -> {new}" + (" (invoices locked)" if new in LOCKING and cur not in LOCKING
                                       else " (locks released)" if new in ("rejected","withdrawn") else "")

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

def invoice_lines(con, ent, ctry, qtr):
    """Invoice-level detail for one claim: prefer per-invoice split via the note column,
    fall back to registry invoice(s) carrying the country aggregate."""
    months = q_months(qtr)
    sups = [r[0] for r in con.execute(
        """SELECT DISTINCT supplier FROM transactions WHERE entity=? AND country=?
           AND period IN (%s)""" % ",".join("?"*len(months)), [ent, ctry]+months)]
    lines = []
    for sup in sups:
        issuer, vatid, vnote = supplier_db.get_issuer(sup, ctry)
        regs = supplier_db.get_invoices(sup, ctry)
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
        submitted_date, approved_date, paid_date, paid_amount
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
                        paid=r["paid_date"], paid_amount=r["paid_amount"], age_days=age))
    con.close()
    summary = {s: money.fsum(o["vat_eur"] or 0 for o in out if o["status"] == s)
               for s in ("submitted", "approved", "paid")}
    summary["outstanding"] = money.fsum(o["vat_eur"] or 0 for o in out
                                        if o["status"] in ("submitted", "approved"))
    return out, summary
