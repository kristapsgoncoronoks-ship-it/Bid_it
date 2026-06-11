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

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))

def _half(date):                      # ISO date -> 'H1' / 'H2'
    try:
        return "H1" if int((date or "")[8:10]) <= 15 else "H2"
    except (ValueError, TypeError):
        return "H1"

def run_control(period):
    import supplier_db, vat_refund, audit
    scon = supplier_db.connect()
    fcon = vat_refund.connect()
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
    for d in fcon.execute("SELECT supplier, invoice_ref FROM invoice_documents"):
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

    # persist (keep manual waived/note overrides)
    for r in rows:
        fcon.execute("""INSERT INTO invoice_receipt_control
            (period, supplier, country, slot, expected, invoice_no, status, note, checked_at)
            VALUES (?,?,?,?,?,?,?,?, datetime('now'))
            ON CONFLICT(period, supplier, country, slot) DO UPDATE SET
            expected=excluded.expected, invoice_no=excluded.invoice_no,
            status=CASE WHEN invoice_receipt_control.waived=1
                        THEN invoice_receipt_control.status ELSE excluded.status END,
            checked_at=excluded.checked_at""", tuple(r.values()))
    fcon.commit()
    scon.close(); fcon.close()
    return rows, orphans

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
    import customer_db
    ccon = customer_db.connect()
    r = ccon.execute("""SELECT c.company_name, c.country FROM customer_supplier_accounts a
                        JOIN customers c ON c.code=a.customer WHERE a.supplier=?""",
                     (supplier,)).fetchone()
    ccon.close()
    return (r["company_name"], r["country"]) if r else (None, None)

def register_statement(supplier, statement_ref, period, statement_date, lines,
                       notes=None, customer=None):
    """lines: iterable of (invoice_no, invoice_date, country, currency, net, vat)."""
    import supplier_db
    con = supplier_db.connect()
    try: con.execute("ALTER TABLE supplier_statements ADD COLUMN customer TEXT")
    except sqlite3.OperationalError: pass  # column already exists (safe)
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
        if vat > 0 and not con.execute("""SELECT 1 FROM supplier_invoices WHERE supplier=?
                AND invoice_no=?""", (supplier, inv_no)).fetchone():
            con.execute("""INSERT INTO supplier_invoices VALUES (?,?,?,?,?,?,?,?)""",
                        (supplier, ctry, inv_no, inv_date, period, ccy, net + vat,
                         f"auto-synced from statement {statement_ref}"))
            synced += 1
    con.commit(); con.close()
    return synced

def reconcile_statements(period):
    """For each statement of the period: verdict per issued invoice."""
    import supplier_db, vat_refund
    scon = supplier_db.connect(); fcon = vat_refund.connect()
    docs = {(d["supplier"], d["invoice_ref"]) for d in
            fcon.execute("SELECT supplier, invoice_ref FROM invoice_documents")}
    out = []
    for st in scon.execute("SELECT * FROM supplier_statements WHERE period=?", (period,)):
        cust_name, cust_country = (None, None)
        try:
            cust_name = st["customer"]
        except (KeyError, IndexError):
            pass
        if cust_name:
            import customer_db
            cc = customer_db.connect()
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
