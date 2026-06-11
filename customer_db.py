"""
CUSTOMER MASTER DATABASE - customers.db (SEPARATE from suppliers.db and fuel_history.db)

Profiles of OUR entities (the clients / VAT refund applicants): company name,
registration number, VAT number, legal address, bank account details (refund
payout account), home tax portal, and account numbers held at each supplier.

Three-database architecture:
    customers.db    - who WE are        (this file)
    suppliers.db    - who THEY are      (supplier master)
    fuel_history.db - what happened     (transactions, claims, documents)

Usage:
    python3 customer_db.py            -> (re)build + seed, print all profiles
    python3 customer_db.py JUPITER    -> one profile
API: get_customer(name_or_code) -> dict incl. payout account; portal(name)
"""
import sqlite3, sys
import audit

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/customers.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    code TEXT PRIMARY KEY, company_name TEXT UNIQUE,
    reg_number TEXT, vat_number TEXT, legal_address TEXT, country TEXT,
    home_portal TEXT, phone TEXT, email TEXT,
    status TEXT DEFAULT 'active', notes TEXT);
CREATE TABLE IF NOT EXISTS customer_bank_accounts (
    customer TEXT, iban TEXT PRIMARY KEY, swift TEXT, bank TEXT,
    currency TEXT, purpose TEXT DEFAULT 'refund payout', notes TEXT);
CREATE TABLE IF NOT EXISTS customer_supplier_accounts (
    customer TEXT, supplier TEXT, account_no TEXT, notes TEXT,
    PRIMARY KEY (customer, supplier));
"""

CUSTOMERS = [
 ("JUPITER","Jupiter Plus AS","INPUT: EE company reg code","EE100127540",
  "Savi 26, 80040 Parnu, Estonia","EE","Estonian e-MTA (emta.ee)",None,None,"active",
  "Address per DKV invoice header. Suppliers: Q8/Port One, DKV."),
 ("OMUSS","SIA OMUSS","INPUT: LV reg. nr.","INPUT: LV VAT number",
  "INPUT: legal address, Latvia","LV","Latvian EDS (eds.vid.gov.lv)",None,None,"active",
  "Supplier: BP Poland (split-payment invoices)."),
 ("MOTIEJAUSKO","UAB Motiejausko Transportas","INPUT: LT imones kodas","INPUT: LT PVM kodas",
  "INPUT: legal address, Lithuania","LT","Lithuanian Mano VMI / EPRIS",None,None,"active",
  "Supplier: TFC by Moya (Belgium)."),
 ("VESTROIDAS","UAB Vestroidas","INPUT: LT imones kodas","LT100006205817",
  "INPUT: legal address, Lithuania","LT","Lithuanian Mano VMI / EPRIS",None,None,"active",
  "VAT number per E100 invoices. Supplier: E100 (Belgium)."),
 ("ZAUKOS","UAB Zaukos Transportas","INPUT: LT imones kodas","LT714494413",
  "Dariaus ir Gireno g. 138, 82-0141 Radviliskis, Lithuania","LT",
  "Lithuanian Mano VMI / EPRIS",None,None,"active",
  "Legal addr. per Moeve invoice; mailing addr. Pininines Street 6-67, Vilnius. Supplier: Moeve Pro."),
]

BANKS = [
 ("JUPITER","INPUT: EE IBAN",None,"INPUT","EUR","refund payout","required for 2008/9/EC applications"),
 ("OMUSS","INPUT: LV IBAN",None,"INPUT","EUR","refund payout","required for 2008/9/EC applications"),
 ("MOTIEJAUSKO","INPUT: LT IBAN (M)",None,"INPUT","EUR","refund payout","required for 2008/9/EC applications"),
 ("VESTROIDAS","INPUT: LT IBAN (V)",None,"INPUT","EUR","refund payout","required for 2008/9/EC applications"),
 ("ZAUKOS","INPUT: LT IBAN (Z)",None,"INPUT","EUR","refund payout","required for 2008/9/EC applications"),
]

SUPPLIER_ACCOUNTS = [
 ("JUPITER","DKV","4100100516","customer number per DKV e-invoices"),
 ("JUPITER","Q8","DE00752298 (payment summary ref)","Q8/Port One scheme"),
 ("ZAUKOS","MOEVE","1301891","client number per Moeve invoice"),
 ("VESTROIDAS","E100","LT100006205817","client id = VAT number on E100 invoices"),
 ("OMUSS","BP","INPUT: BP account no","from BP portal"),
 ("MOTIEJAUSKO","TFC","INPUT: TFC account no","from TFC portal"),
]

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    audit.install_audit(con, ['customers', 'customer_bank_accounts', 'customer_supplier_accounts'])
    return con

def seed(con):
    con.executemany("INSERT OR REPLACE INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?)", CUSTOMERS)
    con.executemany("INSERT OR REPLACE INTO customer_bank_accounts VALUES (?,?,?,?,?,?,?)", BANKS)
    con.executemany("INSERT OR REPLACE INTO customer_supplier_accounts VALUES (?,?,?,?)", SUPPLIER_ACCOUNTS)
    con.commit()

def get_customer(name_or_code):
    con = connect()
    c = con.execute("SELECT * FROM customers WHERE company_name=? OR code=?",
                    (name_or_code, name_or_code)).fetchone()
    if not c:
        con.close()
        return dict(company_name=name_or_code, reg_number="INPUT", vat_number="INPUT",
                    legal_address="INPUT", country="", home_portal="INPUT", payout="INPUT")
    b = con.execute("""SELECT iban, COALESCE(swift,''), bank, currency FROM customer_bank_accounts
                       WHERE customer=? AND purpose='refund payout'""", (c["code"],)).fetchone()
    payout = (f"{b['iban']} ({b['bank']}, {b['currency']})" if b else "INPUT: payout IBAN")
    out = dict(c)
    out["payout"] = payout
    con.close()
    return out

def portal(name_or_code):
    return get_customer(name_or_code)["home_portal"]

def card(con, code):
    c = con.execute("SELECT * FROM customers WHERE code=?", (code,)).fetchone()
    print(f"\n=== {c['code']}: {c['company_name']} [{c['status']}] ===")
    for k in ("reg_number","vat_number","legal_address","country","home_portal","notes"):
        if c[k]: print(f"  {k:13}: {c[k]}")
    for r in con.execute("SELECT iban, bank, currency, purpose FROM customer_bank_accounts WHERE customer=?", (code,)):
        print(f"  bank         : {r['iban']} | {r['bank']} | {r['currency']} | {r['purpose']}")
    for r in con.execute("SELECT supplier, account_no, notes FROM customer_supplier_accounts WHERE customer=?", (code,)):
        print(f"  supplier acct: {r['supplier']} -> {r['account_no']} ({r['notes']})")

if __name__ == "__main__":
    con = connect(); seed(con)
    codes = [sys.argv[1]] if len(sys.argv) > 1 else \
            [r[0] for r in con.execute("SELECT code FROM customers ORDER BY code")]
    for c in codes: card(con, c)
    con.close()
