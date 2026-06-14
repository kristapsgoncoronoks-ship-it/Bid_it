"""
INVOICE RECEIPT CONTROL - did we receive every invoice the suppliers issued?

Logic:
  1. Each supplier has an invoicing CADENCE (stored in suppliers.db):
       semi-monthly        -> one invoice per half-month (every ~14 days): E100, DKV
       monthly             -> one invoice per month (every ~30 days): MOEVE, BP, TFC, PORTONE
       monthly-per-country -> one invoice per month PER COUNTRY with activity: Q8
  2. EXPECTATION = cadence x ACTIVITY. An invoice is expected for a slot only if
     transactions exist in that slot (and country, for per-country suppliers).
     No activity in a country/half-month -> "NO ACTIVITY" (no invoice expected) - OK.
  3. CROSS-CONTROL against what we hold:
       RECEIVED + DOC   invoice registered in suppliers.db AND original PDF/scan in vault
       RECEIVED no doc  registered but physical document missing
       MISSING          activity exists but no invoice registered -> chase the supplier
     Plus an orphan check: every transaction must be covered by a registered invoice.
  4. Results persist in fuel_history.db table invoice_receipt_control (audited);
     manual overrides (waived / note) survive re-runs.

Usage:  python3 invoice_control.py [2026-05]
"""
import sqlite3, sys, collections
import db_migrate

import db_tuning
import applog

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))
FUEL_HISTORY_DB = f"{WORKDIR}/fuel_history.db"
log = applog.get("invoice_control")

def _control_writer():
    """Read-WRITE handle to fuel_history.db for the engine-side control writer.
    invoice_receipt_control is an engine-owned table this module persists into during
    the CLI / monthly-close (never from a web request — the render path uses the
    read-only dataproduct accessor). Tuned to WAL like the canonical engine writer."""
    con = sqlite3.connect(FUEL_HISTORY_DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    return con

def _half(date):                      # ISO date -> 'H1' / 'H2'
    try:
        return "H1" if int((date or "")[8:10]) <= 15 else "H2"
    except (ValueError, TypeError):
        return "H1"

def run_control(period, persist=True):
    """Compute receipt-control rows + orphans for the period.

    persist=True (default, used by the CLI / monthly-close) writes the recomputed
    rows into invoice_receipt_control, keeping manual waived/note overrides, and
    fires the audit triggers. persist=False is a read-only render path: it computes
    and returns the SAME (rows, orphans) but writes nothing and logs no audit churn.
    Use control_summary(period) for the read-only path.
    """
    import supplier_master, vat_refund, audit
    scon = supplier_master.connect()
    # transactions + receipt-control live in fuel_history.db; invoice DOCUMENTS live
    # in the separate claims DB (so claim records are isolated from the monthly rebuild).
    # The render path (persist=False) only READS, so it uses the app's read-only product
    # accessor; the CLI / monthly-close writer (persist=True) is an engine-side writer of
    # the invoice_receipt_control table and needs a read-write handle to fuel_history.db.
    if persist:
        fcon = _control_writer()
        audit.bind(fcon)
    else:
        import dataproduct
        fcon = dataproduct.connect("fuel_history")
    ccon = vat_refund.connect()
    # invoice_receipt_control is engine-owned: only the persist (read-write) writer
    # creates/migrates/audits it. The render path (persist=False) holds a READ-ONLY
    # handle to fuel_history.db, so a CREATE TABLE / install_audit here would raise
    # "attempt to write a readonly database" on a fresh post-history deployment where
    # the CLI has never created the table (the demo DB ships with it, hence green).
    # The read path tolerates the table being absent below: the returned rows/orphans
    # are computed from transactions/cadence, not from the control table.
    if persist:
        fcon.execute("""CREATE TABLE IF NOT EXISTS invoice_receipt_control (
            period TEXT, supplier TEXT, country TEXT, slot TEXT,
            expected TEXT, invoice_no TEXT, status TEXT, note TEXT,
            waived INTEGER DEFAULT 0, checked_at TEXT,
            PRIMARY KEY (period, supplier, country, slot))""")
        audit.install_audit(fcon, ["invoice_receipt_control"])

    cadence = {r["code"]: (r["invoice_cadence"] or "monthly")
               for r in scon.execute("SELECT code, invoice_cadence FROM suppliers")}
    scopes = {r["code"]: (r["scope"] or "") for r in
              scon.execute("SELECT code, '' AS scope FROM suppliers")}  # scope text lives in specs
    # activity from transactions: (supplier, country) -> litres per half / per month
    act = collections.defaultdict(lambda: {"H1": 0.0, "H2": 0.0, "M": 0.0, "n": 0})
    for r in fcon.execute("""SELECT supplier, country, date, qty FROM transactions
                             WHERE period=?""", (period,)):
        a = act[(r["supplier"], r["country"])]
        a[_half(r["date"])] += r["qty"] or 0
        a["M"] += r["qty"] or 0
        a["n"] += 1
    # received invoices from the registry + their documents
    received = collections.defaultdict(list)   # (supplier, country_or_None, slot) -> invoice rows
    docs_by_inv = {}
    for d in ccon.execute("SELECT supplier, invoice_ref FROM invoice_documents"):
        docs_by_inv.setdefault((d["supplier"], d["invoice_ref"]), True)
    invs = scon.execute("""SELECT supplier, country, invoice_no, invoice_date
                           FROM supplier_invoices WHERE period=?""", (period,)).fetchall()

    rows = []
    def add(sup, ctry, slot, expected, inv_no, status, note):
        rows.append(dict(period=period, supplier=sup, country=ctry, slot=slot,
                         expected=expected, invoice_no=inv_no, status=status, note=note))

    # PORT ONE rebate expected whenever Q8 had any activity
    q8_active = any(s == "Q8" for (s, c) in act)

    for sup, cad in cadence.items():
        sup_act = {c: a for (s, c), a in act.items() if s == sup}
        sup_inv = [i for i in invs if i["supplier"] == sup]
        if cad == "semi-monthly":
            for slot, label in (("H1", "1-15"), ("H2", "16-31")):
                litres = sum(a[slot] for a in sup_act.values())
                match = [i for i in sup_inv if _half(i["invoice_date"]) == slot]
                if litres <= 0 and not match:
                    add(sup, "(all)", slot, f"every 14 days ({label})", None,
                        "NO ACTIVITY", "no transactions in this half-month - no invoice expected")
                elif match:
                    inv = match[0]
                    has_doc = (sup, inv["invoice_no"]) in docs_by_inv
                    add(sup, "(all)", slot, f"every 14 days ({label})", inv["invoice_no"],
                        "RECEIVED + DOC" if has_doc else "RECEIVED - DOC MISSING",
                        f"{litres:,.0f} L covered")
                else:
                    add(sup, "(all)", slot, f"every 14 days ({label})", None,
                        "MISSING", f"{litres:,.0f} L of activity but no invoice registered - CHASE")
        elif cad == "monthly-per-country":
            for ctry, a in sorted(sup_act.items()):
                match = [i for i in sup_inv if i["country"] == ctry]
                if match:
                    inv = match[0]
                    has_doc = (sup, inv["invoice_no"]) in docs_by_inv
                    add(sup, ctry, "M", "every 30 days, per country", inv["invoice_no"],
                        "RECEIVED + DOC" if has_doc else "RECEIVED - DOC MISSING",
                        f"{a['M']:,.0f} L / {a['n']} txns")
                else:
                    add(sup, ctry, "M", "every 30 days, per country", None,
                        "MISSING", f"{a['M']:,.0f} L / {a['n']} txns with VAT - request country original")
            # summary documents (country='(multi)') are supplementary - report as info
            for i in sup_inv:
                if i["country"] == "(multi)":
                    add(sup, "(multi)", "M", "summary document", i["invoice_no"],
                        "RECEIVED (supplementary)", "payment summary - not a per-country original")
        else:  # monthly
            litres = sum(a["M"] for a in sup_act.values())
            n = sum(a["n"] for a in sup_act.values())
            expected_anyway = (sup == "PORTONE" and q8_active)
            if litres <= 0 and not expected_anyway and not sup_inv:
                add(sup, "(all)", "M", "every 30 days", None,
                    "NO ACTIVITY", "no transactions this month - no invoice expected")
            elif sup_inv:
                inv = sup_inv[0]
                has_doc = (sup, inv["invoice_no"]) in docs_by_inv
                add(sup, inv["country"], "M", "every 30 days", inv["invoice_no"],
                    "RECEIVED + DOC" if has_doc else "RECEIVED - DOC MISSING",
                    (f"{litres:,.0f} L / {n} txns" if litres else "rebate/settlement invoice"))
            else:
                add(sup, "(all)", "M", "every 30 days", None,
                    "MISSING", f"{litres:,.0f} L of activity but no invoice registered - CHASE")

    # orphan check: transactions not covered by any registered invoice of their supplier
    covered = {(i["supplier"], i["country"]) for i in invs} | \
              {(i["supplier"], None) for i in invs}
    orphans = []
    for (sup, ctry), a in act.items():
        sup_invs = [i for i in invs if i["supplier"] == sup]
        if not sup_invs:
            continue  # already MISSING above
        if cadence.get(sup) == "monthly-per-country" and (sup, ctry) not in covered:
            orphans.append(f"{sup}/{ctry}: {a['n']} txns ({a['M']:,.0f} L) not covered by a country invoice")

    # persist (keep manual waived/note overrides). The render path (persist=False)
    # skips this entirely so a page GET never writes the recomputed rows or churns
    # the audit log; the explicit monthly-close / CLI keeps the rows fresh.
    if persist:
        for r in rows:
            fcon.execute("""INSERT INTO invoice_receipt_control
                (period, supplier, country, slot, expected, invoice_no, status, note, checked_at)
                VALUES (?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
                ON CONFLICT(period, supplier, country, slot) DO UPDATE SET
                expected=excluded.expected, invoice_no=excluded.invoice_no,
                status=CASE WHEN invoice_receipt_control.waived=1
                            THEN invoice_receipt_control.status ELSE excluded.status END,
                checked_at=excluded.checked_at""", tuple(r.values()))
        fcon.commit()
    scon.close(); fcon.close(); ccon.close()
    return rows, orphans

def control_summary(period):
    """Read-only entry for render paths: SAME (rows, orphans) as run_control but
    writes nothing to invoice_receipt_control and fires no audit triggers."""
    return run_control(period, persist=False)

if __name__ == "__main__":
    period = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    rows, orphans = run_control(period)
    print(f"INVOICE RECEIPT CONTROL - {period}")
    print(f"{'Supplier':9}{'Country':10}{'Slot':5}{'Expected':28}{'Invoice':22}{'Status':24}Note")
    order = {"MISSING": 0, "RECEIVED - DOC MISSING": 1}
    for r in sorted(rows, key=lambda x: (order.get(x["status"], 2), x["supplier"])):
        print(f"{r['supplier']:9}{(r['country'] or ''):10}{r['slot']:5}{r['expected']:28}"
              f"{(r['invoice_no'] or '-'):22}{r['status']:24}{r['note']}")
    if orphans:
        print("\nORPHAN TRANSACTIONS (covered-by-invoice check):")
        for o in orphans: print("  -", o)
    miss = sum(1 for r in rows if r["status"] == "MISSING")
    print(f"\n{miss} MISSING invoice(s) to chase; "
          f"{sum(1 for r in rows if r['status'].startswith('RECEIVED'))} received; "
          f"{sum(1 for r in rows if r['status']=='NO ACTIVITY')} slots with no activity (OK).")


# ====================================================================
# SUMMARY STATEMENT RECONCILIATION
# A supplier statement PDF lists ALL invoices issued in the batch. Register its
# lines, then triage: VAT > 0 -> PROCESS (must hold original, feeds refund claim);
# VAT = 0 -> DISCARD (archive only, nothing reclaimable). VAT-bearing lines are
# auto-synced into the supplier_invoices registry so the VAT module sees them.
# ====================================================================
COUNTRY_CODES = {"Austria":"AT","Belgium":"BE","Czechia":"CZ","Denmark":"DK","Estonia":"EE",
 "France":"FR","Germany":"DE","Italy":"IT","Latvia":"LV","Lithuania":"LT","Luxembourg":"LU",
 "Netherlands":"NL","Poland":"PL","Slovakia":"SK","Slovenia":"SI","Spain":"ES","Sweden":"SE"}

def _statement_customer(con, supplier):
    """Resolve which of our entities a supplier statement belongs to."""
    import customer_master
    ccon = customer_master.connect()
    r = ccon.execute("""SELECT c.company_name, c.country FROM customer_supplier_accounts a
                        JOIN customers c ON c.code=a.customer WHERE a.supplier=?""",
                     (supplier,)).fetchone()
    ccon.close()
    return (r["company_name"], r["country"]) if r else (None, None)

def register_statement(supplier, statement_ref, period, statement_date, lines,
                       notes=None, customer=None):
    """lines: iterable of (invoice_no, invoice_date, country, currency, net, vat)."""
    import supplier_master
    con = supplier_master.connect()
    db_migrate.apply(con, "invoice_control",
                     ["ALTER TABLE supplier_statements ADD COLUMN customer TEXT"])
    if not customer:
        customer, _ = _statement_customer(con, supplier)
    con.execute("""INSERT OR REPLACE INTO supplier_statements
                   (supplier, statement_ref, period, statement_date, notes, customer)
                   VALUES (?,?,?,?,?,?)""",
                (supplier, statement_ref, period, statement_date, notes, customer))
    synced = 0
    for inv_no, inv_date, ctry, ccy, net, vat in lines:
        net, vat = float(net or 0), float(vat or 0)
        con.execute("""INSERT OR REPLACE INTO statement_invoices VALUES (?,?,?,?,?,?,?,?,?)""",
                    (supplier, statement_ref, inv_no, inv_date, ctry, ccy, net, vat, net + vat))
        if vat > 0:
            note = f"auto-synced from statement {statement_ref}"
            existing = con.execute("""SELECT notes FROM supplier_invoices WHERE supplier=?
                    AND invoice_no=?""", (supplier, inv_no)).fetchone()
            if existing is None:
                con.execute("""INSERT INTO supplier_invoices VALUES (?,?,?,?,?,?,?,?)""",
                            (supplier, ctry, inv_no, inv_date, period, ccy, net + vat, note))
                synced += 1
            elif (existing["notes"] or "").startswith("auto-synced from statement"):
                # Previously auto-synced row: re-sync it so a CORRECTED statement line
                # is reflected (otherwise gross_total drifts from the statement). A
                # manually-curated row (any other note) is left untouched.
                con.execute("""UPDATE supplier_invoices SET country=?, invoice_date=?,
                        period=?, currency=?, gross_total=?, notes=?
                        WHERE supplier=? AND invoice_no=?""",
                            (ctry, inv_date, period, ccy, net + vat, note, supplier, inv_no))
                synced += 1
    con.commit(); con.close()
    return synced

def reconcile_statements(period):
    """For each statement of the period: verdict per issued invoice."""
    import supplier_master, vat_refund
    scon = supplier_master.connect(); fcon = vat_refund.connect()
    docs = {(d["supplier"], d["invoice_ref"]) for d in
            fcon.execute("SELECT supplier, invoice_ref FROM invoice_documents")}
    out = []
    for st in scon.execute("SELECT * FROM supplier_statements WHERE period=?", (period,)):
        cust_name, cust_country = (None, None)
        try:
            cust_name = st["customer"]
        except (KeyError, IndexError) as e:
            log.debug("statement has no customer column: %s", e)
        if cust_name:
            import customer_master
            cc = customer_master.connect()
            r = cc.execute("SELECT country FROM customers WHERE company_name=?", (cust_name,)).fetchone()
            cust_country = r["country"] if r else None
            cc.close()
        else:
            cust_name, cust_country = _statement_customer(scon, st["supplier"])
        for L in scon.execute("""SELECT * FROM statement_invoices WHERE supplier=?
                                 AND statement_ref=? ORDER BY country""",
                              (st["supplier"], st["statement_ref"])):
            line_code = COUNTRY_CODES.get(L["country"], L["country"])
            if cust_country and line_code == cust_country:
                verdict = "DISCARD - DOMESTIC"
                action = (f"domestic invoice ({L['country']} = home country of {cust_name}) - "
                          f"VAT goes into the regular {cust_country} VAT return, NOT the 2008/9 refund - auto-discarded")
            elif (L["vat"] or 0) <= 0:
                verdict, action = "DISCARD", "no VAT in this invoice - archive only, nothing to reclaim"
            else:
                registered = scon.execute("""SELECT 1 FROM supplier_invoices WHERE supplier=?
                        AND invoice_no=?""", (L["supplier"], L["invoice_no"])).fetchone()
                has_doc = (L["supplier"], L["invoice_no"]) in docs
                unidentified = "INPUT" in (L["invoice_no"] or "")
                if unidentified:
                    verdict, action = "PROCESS - IDENTIFY", "read exact invoice number from the statement PDF, then obtain original"
                elif registered and has_doc:
                    verdict, action = "PROCESS - COMPLETE", "registered + original on file -> flows to VAT claim"
                elif registered:
                    verdict, action = "PROCESS - DOC MISSING", "registered; obtain and attach the original PDF"
                else:
                    verdict, action = "PROCESS - NOT REGISTERED", "issued per statement but absent from registry - investigate"
            out.append(dict(supplier=L["supplier"], statement=st["statement_ref"],
                            invoice=L["invoice_no"], country=L["country"], currency=L["currency"],
                            net=L["net"], vat=L["vat"], verdict=verdict, action=action))
    scon.close(); fcon.close()
    return out


def unregistered_vaulted_documents():
    """Register-failure reconcile (D4 split-brain): vaulted documents with NO
    registered invoice.

    On statement confirm the source PDFs are attached to the document vault
    IN-REQUEST (invoice_documents in vat_claims.db), but the registry WRITE to
    suppliers.db is ENQUEUED to the engine worker (kind='register'). If that
    register job fails / is held / is discarded, the documents are vaulted but the
    invoices are NEVER registered — an orphaned document nothing flags directly.
    This is an ADDITIVE, read-only reconcile sweep that surfaces those orphans.

    Basis: the orphan signal is checked against `statement_invoices` (the COMPLETE
    registry written for EVERY statement line, incl. vat=0), NOT `supplier_invoices`
    (only the vat>0 subset is auto-synced there) — otherwise every legitimately
    vaulted vat=0 invoice would false-positive.

    Key match: exact `(supplier, invoice_ref)` vs `(supplier, invoice_no)`, the
    SAME comparison `reconcile_statements`/`run_control` use (no normalization
    invented here).

    Pure and never-raises: returns [] on any error (logged via the module logger).

    Returns a list of dicts, one per orphaned (supplier, invoice_ref):
        {"supplier": str, "invoice_ref": str, "entity": str | None,
         "n_docs": int, "filename": str | None}
    where entity/filename are taken from one representative vaulted row and n_docs
    is the count of vaulted document rows for that (supplier, invoice_ref).
    """
    import supplier_master, vat_refund
    scon = fcon = None
    try:
        fcon = vat_refund.connect()
        # all vaulted docs, grouped to one row per (supplier, invoice_ref) with a
        # doc count and one representative entity/filename to action it.
        vaulted = {}
        for d in fcon.execute("""SELECT supplier, invoice_ref, entity, filename
                                 FROM invoice_documents"""):
            key = (d["supplier"], d["invoice_ref"])
            v = vaulted.get(key)
            if v is None:
                vaulted[key] = dict(supplier=d["supplier"], invoice_ref=d["invoice_ref"],
                                    entity=d["entity"], filename=d["filename"], n_docs=1)
            else:
                v["n_docs"] += 1
                # backfill a representative entity/filename if the first row lacked one
                v["entity"] = v["entity"] or d["entity"]
                v["filename"] = v["filename"] or d["filename"]
        scon = supplier_master.connect()
        registered = {(r["supplier"], r["invoice_no"]) for r in
                      scon.execute("SELECT supplier, invoice_no FROM statement_invoices")}
        return [v for key, v in vaulted.items() if key not in registered]
    except Exception as e:
        log.warning("unregistered_vaulted_documents reconcile failed: %s", e)
        return []
    finally:
        if fcon is not None:
            fcon.close()
        if scon is not None:
            scon.close()
