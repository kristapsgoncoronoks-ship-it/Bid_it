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
import dbtune

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
CREATE TABLE IF NOT EXISTS customer_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT, kind TEXT, filename TEXT, stored_path TEXT,
    sha256 TEXT, size INTEGER, backend TEXT DEFAULT 'local', web_url TEXT,
    uploaded_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS customer_fees (
    customer TEXT, country TEXT, fee_pct REAL DEFAULT 0, fee_min REAL DEFAULT 0,
    PRIMARY KEY (customer, country));
CREATE TABLE IF NOT EXISTS customer_countries (
    customer TEXT, country TEXT, status TEXT DEFAULT 'pending',
    requested_at TEXT, activated_at TEXT,
    PRIMARY KEY (customer, country));
CREATE TABLE IF NOT EXISTS country_requirements (
    country TEXT, kind TEXT, PRIMARY KEY (country, kind));
"""

# Documents a new VAT-refund customer must provide before activation.
REQUIRED_DOCS = {
    "trade_registry":  "Trade registry extract (verify client data)",
    "signed_contract": "Signed service contract",
}
# Catalogue of country-activation document kinds. Which of these a given refund
# country requires is configured per country (country_requirements); some need
# only a power of attorney, others several. Default = power of attorney only.
DOC_KINDS = {
    "power_of_attorney": "Power of attorney / authorisation to file",
    "vat_certificate":   "Local VAT registration certificate",
    "tax_mandate":       "Tax representative / fiscal mandate",
    "fleet_list":        "Vehicle fleet list",
    "company_extract":   "Company registry extract (local language)",
    "id_signatory":      "ID copy of the authorised signatory",
}
DEFAULT_COUNTRY_DOCS = ["power_of_attorney"]
DOCDIR = f"{WORKDIR}/documents"

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

_SCHEMA_READY = set()   # DB files whose schema is set up this process

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    dbtune.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)   # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        # fee model: % of refunded VAT, floored at a per-declaration minimum (EUR)
        for ddl in ("ALTER TABLE customers ADD COLUMN fee_pct REAL DEFAULT 0",
                    "ALTER TABLE customers ADD COLUMN fee_min REAL DEFAULT 0",
                    # where the refund is paid: 'customer' (we invoice the fee) or
                    # 'us' (we receive it, deduct the fee, remit the net to the customer)
                    "ALTER TABLE customers ADD COLUMN payout_route TEXT DEFAULT 'customer'",
                    # documents can be scoped to a refund country (NULL = customer-level)
                    "ALTER TABLE customer_documents ADD COLUMN country TEXT"):
            try: con.execute(ddl)
            except sqlite3.OperationalError: pass  # column already exists (safe)
        audit.install_audit(con, ['customers', 'customer_bank_accounts',
                                  'customer_supplier_accounts', 'customer_documents',
                                  'customer_fees', 'customer_countries', 'country_requirements'])
        _SCHEMA_READY.add(DB)
    return con

def seed(con):
    con.executemany("""INSERT OR REPLACE INTO customers
        (code, company_name, reg_number, vat_number, legal_address, country,
         home_portal, phone, email, status, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", CUSTOMERS)
    con.executemany("INSERT OR REPLACE INTO customer_bank_accounts VALUES (?,?,?,?,?,?,?)", BANKS)
    con.executemany("INSERT OR REPLACE INTO customer_supplier_accounts VALUES (?,?,?,?)", SUPPLIER_ACCOUNTS)
    con.commit()

# ---------------------------------------------------------------- onboarding
def add_customer(code, company_name, country="", reg_number="", vat_number="",
                 home_portal="", notes=""):
    """Create a NEW customer in 'pending' status (must be activated after the
    required documents and bank account are on file)."""
    con = connect()
    con.execute("""INSERT INTO customers
        (code, company_name, reg_number, vat_number, legal_address, country,
         home_portal, phone, email, status, notes)
        VALUES (?,?,?,?,?,?,?,?,?, 'pending', ?)""",
        (code.strip().upper(), company_name.strip(), reg_number or "INPUT: reg number",
         vat_number or "INPUT: VAT number", "INPUT: legal address", country.strip(),
         home_portal or "INPUT: home portal", None, None, notes))
    con.commit(); con.close()

def add_document(con, code, kind, filename, file_bytes, country=None):
    """Vault a customer document (hash-verified). country=None for customer-level
    docs (trade registry, contract); a country for country-specific docs (POA)."""
    import hashlib
    import doc_storage
    sha = hashlib.sha256(file_bytes).hexdigest()
    # archive under <Customer> <RegNo>/customer-documents/<country|general>/<kind>/<file>
    try:
        c = get_customer(code)
        cust_name, reg = c.get("company_name", code), c.get("reg_number")
    except Exception:
        cust_name, reg = code, None
    safe = doc_storage.customer_vault_path(cust_name, reg, country, kind, filename)
    be = doc_storage.backend(DOCDIR)
    stored, web_url = be.put(safe, file_bytes)
    con.execute("""INSERT INTO customer_documents
        (customer, kind, filename, stored_path, sha256, size, backend, web_url, country)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (code, kind, filename, stored, sha, len(file_bytes), be.name, web_url, country))
    con.commit()
    return sha

def documents(con, code, country=None):
    if country is None:
        return con.execute("SELECT * FROM customer_documents WHERE customer=? AND "
                           "(country IS NULL OR country='') ORDER BY id", (code,)).fetchall()
    return con.execute("SELECT * FROM customer_documents WHERE customer=? AND country=? ORDER BY id",
                       (code, country)).fetchall()

def _has_doc(con, code, kind, country=None):
    if country is None:
        return con.execute("SELECT 1 FROM customer_documents WHERE customer=? AND kind=? AND "
                           "(country IS NULL OR country='') LIMIT 1", (code, kind)).fetchone() is not None
    return con.execute("SELECT 1 FROM customer_documents WHERE customer=? AND kind=? AND country=? "
                       "LIMIT 1", (code, kind, country)).fetchone() is not None

def _bank_ok(con, code):
    return con.execute("""SELECT 1 FROM customer_bank_accounts
        WHERE customer=? AND iban IS NOT NULL AND iban NOT LIKE '%INPUT%' LIMIT 1""",
        (code,)).fetchone() is not None

def activation_checklist(con, code):
    """Returns ([(label, ok), ...], ready_bool) for the activation requirements."""
    items = [("Trade registry extract", _has_doc(con, code, "trade_registry")),
             ("Bank account (IBAN) on file", _bank_ok(con, code)),
             ("Signed contract", _has_doc(con, code, "signed_contract"))]
    return items, all(ok for _, ok in items)

def set_activation(con, code, active):
    con.execute("UPDATE customers SET status=? WHERE code=?",
                ("active" if active else "pending", code))
    con.commit()

def is_active(name_or_code):
    """True/False if the (tracked) customer is activated; None if not tracked."""
    con = connect()
    r = con.execute("SELECT status FROM customers WHERE company_name=? OR code=?",
                    (name_or_code, name_or_code)).fetchone()
    con.close()
    return None if r is None else (r["status"] == "active")

# ---------------------------------------------------------------- per refund country
def _code_of(con, name_or_code):
    r = con.execute("SELECT code FROM customers WHERE company_name=? OR code=?",
                    (name_or_code, name_or_code)).fetchone()
    return r["code"] if r else None

def request_country(con, code, country):
    """Start activation for a refund country: mark its documents as requested."""
    con.execute("""INSERT INTO customer_countries (customer, country, status, requested_at)
                   VALUES (?,?, 'requested', datetime('now'))
                   ON CONFLICT(customer, country) DO UPDATE SET
                     status=CASE WHEN customer_countries.status='active' THEN 'active' ELSE 'requested' END,
                     requested_at=COALESCE(customer_countries.requested_at, datetime('now'))""",
                (code, country.strip()))
    con.commit()

def add_country_document(con, code, country, kind, filename, file_bytes):
    return add_document(con, code, kind, filename, file_bytes, country=country.strip())

def set_country_requirements(con, country, kinds):
    """Replace the set of documents a refund country requires for activation."""
    country = country.strip()
    con.execute("DELETE FROM country_requirements WHERE country=?", (country,))
    for k in kinds:
        if k in DOC_KINDS:
            con.execute("INSERT OR IGNORE INTO country_requirements (country, kind) VALUES (?,?)",
                        (country, k))
    con.commit()

def required_docs_for_country(con, country):
    """List of required document kinds for a country (default: power of attorney)."""
    rows = [r["kind"] for r in con.execute(
        "SELECT kind FROM country_requirements WHERE country=?", (country.strip(),))]
    return rows or list(DEFAULT_COUNTRY_DOCS)

def all_country_requirements(con):
    out = {}
    for r in con.execute("SELECT country, kind FROM country_requirements ORDER BY country, kind"):
        out.setdefault(r["country"], []).append(r["kind"])
    return out

def country_doc_checklist(con, code, country):
    """([(label, ok), ...], ready) for the documents required to activate a country."""
    kinds = required_docs_for_country(con, country)
    items = [(DOC_KINDS.get(k, k), _has_doc(con, code, k, country)) for k in kinds]
    return items, all(ok for _, ok in items)

def activate_country(con, code, country, active):
    if active:
        con.execute("""INSERT INTO customer_countries (customer, country, status, activated_at)
                       VALUES (?,?, 'active', datetime('now'))
                       ON CONFLICT(customer, country) DO UPDATE SET
                         status='active', activated_at=datetime('now')""", (code, country.strip()))
    else:
        con.execute("UPDATE customer_countries SET status='pending' WHERE customer=? AND country=?",
                    (code, country.strip()))
    con.commit()

def country_rows(con, code):
    return con.execute("SELECT * FROM customer_countries WHERE customer=? ORDER BY country",
                       (code,)).fetchall()

def country_active(name_or_code, country):
    """True/False if a (customer, country) activation row exists; None if no row
    (country activation not started — not gated, for backward compatibility)."""
    con = connect()
    code = _code_of(con, name_or_code)
    if code is None:
        con.close(); return None
    r = con.execute("SELECT status FROM customer_countries WHERE customer=? AND country=?",
                    (code, country)).fetchone()
    con.close()
    return None if r is None else (r["status"] == "active")

# ---------------------------------------------------------------- fees
def set_fee(con, code, fee_pct, fee_min):
    """Set the customer's DEFAULT fee (applied to refund countries with no override)."""
    con.execute("UPDATE customers SET fee_pct=?, fee_min=? WHERE code=?",
                (float(fee_pct or 0), float(fee_min or 0), code))
    con.commit()

def set_payout_route(con, code, route):
    con.execute("UPDATE customers SET payout_route=? WHERE code=?",
                ("us" if route == "us" else "customer", code))
    con.commit()

def payout_route(name_or_code):
    """'customer' (refund to client, we invoice the fee) or 'us' (refund to us, we
    deduct the fee and remit the net)."""
    con = connect()
    r = con.execute("SELECT payout_route FROM customers WHERE company_name=? OR code=?",
                    (name_or_code, name_or_code)).fetchone()
    con.close()
    return (r["payout_route"] or "customer") if r else "customer"

def set_country_fee(con, code, country, fee_pct, fee_min):
    """Per-country override of the % / minimum fee for one customer."""
    con.execute("""INSERT INTO customer_fees (customer, country, fee_pct, fee_min)
                   VALUES (?,?,?,?) ON CONFLICT(customer, country)
                   DO UPDATE SET fee_pct=excluded.fee_pct, fee_min=excluded.fee_min""",
                (code, country.strip(), float(fee_pct or 0), float(fee_min or 0)))
    con.commit()

def country_fees(con, code):
    return con.execute("SELECT country, fee_pct, fee_min FROM customer_fees "
                       "WHERE customer=? ORDER BY country", (code,)).fetchall()

def fee_for(name_or_code, country=None):
    """(fee_pct, fee_min) for a customer + refund country: the per-country override
    if one exists, otherwise the customer's default fee, else (0, 0)."""
    con = connect()
    c = con.execute("SELECT code, fee_pct, fee_min FROM customers WHERE company_name=? OR code=?",
                    (name_or_code, name_or_code)).fetchone()
    if not c:
        con.close()
        return (0.0, 0.0)
    pct, mn = float(c["fee_pct"] or 0), float(c["fee_min"] or 0)
    if country:
        o = con.execute("SELECT fee_pct, fee_min FROM customer_fees WHERE customer=? AND country=?",
                        (c["code"], country)).fetchone()
        if o:
            pct, mn = float(o["fee_pct"] or 0), float(o["fee_min"] or 0)
    con.close()
    return (pct, mn)

def compute_fee(refund_eur, fee_pct, fee_min):
    """Our fee on a refunded VAT amount. Priority is the % fee; if it falls below
    the per-declaration minimum, the minimum is charged. Returns (fee, basis)."""
    import money
    pct_fee = money.f2((float(fee_pct or 0) / 100.0) * float(refund_eur or 0))
    minimum = money.f2(fee_min or 0)
    if pct_fee >= minimum:
        return pct_fee, "percent"
    return minimum, "minimum"

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
