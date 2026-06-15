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
    python3 customer_master.py            -> (re)build + seed, print all profiles
    python3 customer_master.py JUPITER    -> one profile
API: get_customer(name_or_code) -> dict incl. payout account; portal(name)
"""
import sqlite3, sys
import audit
import db_tuning, db_migrate
import tenancy

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
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS customer_fees (
    customer TEXT, country TEXT, fee_pct REAL DEFAULT 0, fee_min REAL DEFAULT 0,
    PRIMARY KEY (customer, country));
CREATE TABLE IF NOT EXISTS customer_countries (
    customer TEXT, country TEXT, status TEXT DEFAULT 'pending',
    requested_at TEXT, activated_at TEXT,
    PRIMARY KEY (customer, country));
CREATE TABLE IF NOT EXISTS country_requirements (
    country TEXT, kind TEXT, PRIMARY KEY (country, kind));
CREATE TABLE IF NOT EXISTS checklist_rules (
    key TEXT PRIMARY KEY, label TEXT, scope TEXT DEFAULT 'customer',
    check_type TEXT DEFAULT 'document', ref TEXT,
    active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS doc_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, kind TEXT, ext TEXT, filename TEXT, body BLOB,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS document_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT NOT NULL,                 -- customers.code
    kind TEXT NOT NULL,                     -- 'contract' | 'power_of_attorney'
    refund_country TEXT,                    -- NULL for a global contract
    template_id INTEGER,                    -- doc_templates.id used
    status TEXT NOT NULL DEFAULT 'requested',
    requested_by TEXT, requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    generated_at TEXT, sent_at TEXT, signed_at TEXT, received_at TEXT,
    generated_sha256 TEXT,
    generated_doc_id INTEGER,               -- customer_documents.id of the generated file
    signed_doc_id INTEGER,                  -- customer_documents.id of the uploaded signed original
    note TEXT);
CREATE INDEX IF NOT EXISTS ix_docreq_cust ON document_requests(customer, status);
CREATE INDEX IF NOT EXISTS ix_docreq_status ON document_requests(status);
"""

# The claim-submission checklist is ADJUSTABLE (rules change): each rule is a
# requirement the SYSTEM verifies before a claim can be submitted. `scope` is
# 'customer' (checked once) or 'country' (per refund country, e.g. power of
# attorney). `check_type` is 'document' (a customer_documents row of `ref` kind
# exists) or 'data' (a built-in verifier named `ref` passes). Admins edit these
# on the Customers page; these are only the defaults seeded into an empty table.
DEFAULT_CHECKLIST = [
    ("contract",          "Contract",                  "customer", "document", "signed_contract",  1),
    ("customer_data",     "Customer data",             "customer", "data",     "customer_data",    2),
    ("bank_account",      "Bank account",              "customer", "data",     "bank_account",     3),
    ("nace",              "NACE business activity",    "customer", "data",     "nace",             4),
    ("trade_register",    "Trade register / company register form", "customer", "document", "trade_registry", 5),
    ("power_of_attorney", "Power of attorney",         "country",  "document", "power_of_attorney", 6),
]

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
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)   # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        # versioned migrations: each runs ONCE per database (db_migrate). Append only.
        db_migrate.apply(con, "customer_master", [
            # fee model: % of refunded VAT, floored at a per-declaration minimum (EUR)
            "ALTER TABLE customers ADD COLUMN fee_pct REAL DEFAULT 0",
            "ALTER TABLE customers ADD COLUMN fee_min REAL DEFAULT 0",
            # where the refund is paid: 'customer' (we invoice the fee) or
            # 'us' (we receive it, deduct the fee, remit the net to the customer)
            "ALTER TABLE customers ADD COLUMN payout_route TEXT DEFAULT 'customer'",
            # documents can be scoped to a refund country (NULL = customer-level)
            "ALTER TABLE customer_documents ADD COLUMN country TEXT",
            # NACE business-activity code (checklist 'NACE' requirement)
            "ALTER TABLE customers ADD COLUMN nace_code TEXT",
            # documents can expire (power of attorney, VAT certificate) — an
            # expired document no longer satisfies the checklist
            "ALTER TABLE customer_documents ADD COLUMN valid_until TEXT",
            # authorised signatory for generated contracts / powers of attorney
            # (no signatory ID stored by owner decision; merge-only CRM data)
            "ALTER TABLE customers ADD COLUMN signatory_name TEXT",
            "ALTER TABLE customers ADD COLUMN signatory_title TEXT",
            # P1 multi-tenancy (schema plumbing only): stamp every CRM table with a
            # tenant_id; existing rows backfill to DEFAULT_TENANT_ID via the column
            # DEFAULT, new rows default too. NO query reads this column yet (the
            # `multitenant` switch is OFF and scope_clause is unwired until P2), so
            # this is a pure no-behavior-change addition. APPEND-ONLY — keep at END.
            *tenancy.tenant_column_ddls([
                "customers", "customer_bank_accounts", "customer_supplier_accounts",
                "customer_documents", "customer_fees", "customer_countries",
                "country_requirements", "checklist_rules", "doc_templates",
                "document_requests",
            ]),
        ])
        # seed the adjustable submission checklist once (empty table -> defaults)
        if not con.execute("SELECT 1 FROM checklist_rules LIMIT 1").fetchone():
            con.executemany("""INSERT OR IGNORE INTO checklist_rules
                (key, label, scope, check_type, ref, active, sort)
                VALUES (?,?,?,?,?,1,?)""", DEFAULT_CHECKLIST)
            con.commit()
        audit.install_audit(con, ['customers', 'customer_bank_accounts',
                                  'customer_supplier_accounts', 'customer_documents',
                                  'customer_fees', 'customer_countries', 'country_requirements',
                                  'checklist_rules', 'document_requests'])
        _SCHEMA_READY.add(DB)
    return con

def seed(con):
    con.executemany("""INSERT OR REPLACE INTO customers
        (code, company_name, reg_number, vat_number, legal_address, country,
         home_portal, phone, email, status, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", CUSTOMERS)
    # Name columns explicitly so a trailing schema column (e.g. the P1 tenant_id,
    # which takes its DEFAULT) never breaks these positional seeds.
    con.executemany("""INSERT OR REPLACE INTO customer_bank_accounts
        (customer, iban, swift, bank, currency, purpose, notes)
        VALUES (?,?,?,?,?,?,?)""", BANKS)
    con.executemany("""INSERT OR REPLACE INTO customer_supplier_accounts
        (customer, supplier, account_no, notes) VALUES (?,?,?,?)""", SUPPLIER_ACCOUNTS)
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

# Columns the CRM (and the external CRM-sync API) may write on `customers`. Anything
# outside this allowlist is silently ignored — never let a caller set status/fee/route
# or any audited workflow column through the generic editor.
EDITABLE_FIELDS = ("company_name", "reg_number", "vat_number", "legal_address",
                   "home_portal", "phone", "email", "nace_code",
                   "signatory_name", "signatory_title")

def update_customer(code, **fields):
    """Update an allowlist of editable columns on a customer. Returns (ok, msg).

    Shared writer for the in-app CRM and the external CRM-sync API (/api/v1). Only
    EDITABLE_FIELDS are written; unknown keys are skipped (not an error). A value that
    is empty/whitespace for a field being set is rejected. The customer must exist.
    The bound audit triggers record `changed_by` from the current actor. Never raises —
    returns (False, msg) on any error."""
    try:
        code = (code or "").strip().upper()
        if not code:
            return False, "customer code is required"
        sets, vals = [], []
        for k, v in fields.items():
            if k not in EDITABLE_FIELDS:
                continue  # ignore unknown / non-editable keys
            if v is None or not str(v).strip():
                return False, f"{k} cannot be empty"
            sets.append(f"{k}=?")
            vals.append(str(v).strip())
        if not sets:
            return False, "no editable fields supplied"
        con = connect()
        try:
            if not con.execute("SELECT 1 FROM customers WHERE code=?", (code,)).fetchone():
                return False, f"customer {code} not found"
            vals.append(code)
            con.execute(f"UPDATE customers SET {', '.join(sets)} WHERE code=?", vals)
            con.commit()
        finally:
            con.close()
        return True, "updated"
    except Exception as e:
        import applog
        applog.get("customer_master").exception("update_customer failed")
        return False, f"update failed: {e}"

def add_document(con, code, kind, filename, file_bytes, country=None, valid_until=None):
    """Vault a customer document (hash-verified). country=None for customer-level
    docs (trade registry, contract); a country for country-specific docs (POA).
    `valid_until` (ISO date, optional) marks when the document expires — an expired
    document stops satisfying the checklist."""
    import hashlib
    import document_vault
    sha = hashlib.sha256(file_bytes).hexdigest()
    # archive under <Customer> <RegNo>/customer-documents/<country|general>/<kind>/<file>
    try:
        c = get_customer(code)
        cust_name, reg = c.get("company_name", code), c.get("reg_number")
    except Exception:
        cust_name, reg = code, None
    safe = document_vault.customer_vault_path(cust_name, reg, country, kind, filename)
    be = document_vault.backend(DOCDIR)
    stored, web_url = be.put(safe, file_bytes)
    con.execute("""INSERT INTO customer_documents
        (customer, kind, filename, stored_path, sha256, size, backend, web_url, country, valid_until)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (code, kind, filename, stored, sha, len(file_bytes), be.name, web_url, country,
         valid_until or None))
    con.commit()
    return sha

def documents(con, code, country=None):
    if country is None:
        return con.execute("SELECT * FROM customer_documents WHERE customer=? AND "
                           "(country IS NULL OR country='') ORDER BY id", (code,)).fetchall()
    return con.execute("SELECT * FROM customer_documents WHERE customer=? AND country=? ORDER BY id",
                       (code, country)).fetchall()

def _has_doc(con, code, kind, country=None):
    """A document of `kind` is on file AND still valid — a document past its
    `valid_until` (e.g. an expired power of attorney) no longer satisfies the
    checklist, exactly like a missing one."""
    valid = "(valid_until IS NULL OR valid_until='' OR valid_until >= CURRENT_DATE)"
    if country is None:
        return con.execute(f"SELECT 1 FROM customer_documents WHERE customer=? AND kind=? AND "
                           f"(country IS NULL OR country='') AND {valid} LIMIT 1",
                           (code, kind)).fetchone() is not None
    return con.execute(f"SELECT 1 FROM customer_documents WHERE customer=? AND kind=? AND country=? "
                       f"AND {valid} LIMIT 1", (code, kind, country)).fetchone() is not None

def expiring_documents(con, within_days=60):
    """Documents that are expired or expiring within `within_days` — for alerts."""
    return [dict(r) for r in con.execute(
        """SELECT customer, kind, filename, country, valid_until,
                  CAST(julianday(valid_until) - julianday('now') AS INTEGER) days_left
           FROM customer_documents
           WHERE valid_until IS NOT NULL AND valid_until != ''
             AND julianday(valid_until) - julianday('now') <= ?
           ORDER BY valid_until""", (within_days,))]

def _bank_ok(con, code):
    return con.execute("""SELECT 1 FROM customer_bank_accounts
        WHERE customer=? AND iban IS NOT NULL AND iban NOT LIKE '%INPUT%' LIMIT 1""",
        (code,)).fetchone() is not None

def _field_ok(con, code, field):
    """A customer column is present (non-empty, not a placeholder 'INPUT:' stub)."""
    r = con.execute(f"SELECT {field} AS v FROM customers WHERE code=?", (code,)).fetchone()
    v = (r["v"] if r else None) or ""
    return bool(str(v).strip()) and "INPUT" not in str(v).upper()

def _customer_data_ok(con, code):
    """Core customer data is on file (registration, VAT, legal address)."""
    return all(_field_ok(con, code, f) for f in ("reg_number", "vat_number", "legal_address"))

# Built-in data verifiers a 'data' checklist rule can reference by `ref`.
DATA_VERIFIERS = {
    "customer_data": lambda con, code, country: _customer_data_ok(con, code),
    "bank_account":  lambda con, code, country: _bank_ok(con, code),
    "nace":          lambda con, code, country: _field_ok(con, code, "nace_code"),
}

# ---------------------------------------------------------------- adjustable checklist
def list_checklist_rules(con, active_only=False):
    """The submission checklist rules, in display order. Adjustable by admins."""
    q = "SELECT * FROM checklist_rules"
    if active_only:
        q += " WHERE active=1"
    return con.execute(q + " ORDER BY sort, key").fetchall()

def set_checklist_rule(con, key, label, scope="customer", check_type="document",
                       ref=None, active=1, sort=None):
    """Add or update a checklist rule (admin). `key` is the stable identifier."""
    key = (key or "").strip()
    if not key:
        return False, "a rule needs a key"
    if check_type not in ("document", "data"):
        return False, "check_type must be 'document' or 'data'"
    if scope not in ("customer", "country"):
        return False, "scope must be 'customer' or 'country'"
    if check_type == "data" and (ref not in DATA_VERIFIERS):
        return False, f"unknown data verifier '{ref}' (have: {', '.join(DATA_VERIFIERS)})"
    if sort is None:
        sort = (con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM checklist_rules").fetchone()[0])
    con.execute("""INSERT INTO checklist_rules (key, label, scope, check_type, ref, active, sort)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET label=excluded.label, scope=excluded.scope,
                     check_type=excluded.check_type, ref=excluded.ref, active=excluded.active,
                     sort=excluded.sort""",
                (key, (label or key).strip(), scope, check_type, (ref or key).strip(),
                 1 if active else 0, int(sort)))
    con.commit()
    return True, f"checklist rule '{key}' saved"

def toggle_checklist_rule(con, key, active):
    con.execute("UPDATE checklist_rules SET active=? WHERE key=?", (1 if active else 0, key))
    con.commit()

def delete_checklist_rule(con, key):
    con.execute("DELETE FROM checklist_rules WHERE key=?", (key,))
    con.commit()

def evaluate_checklist(con, code, country=None):
    """SYSTEM-CONTROLLED evaluation of the adjustable checklist for a customer (and a
    refund country, for country-scoped rules). Returns [(key, label, scope, ok)] over
    the ACTIVE rules — the user cannot tick these; the system verifies each one."""
    out = []
    for r in list_checklist_rules(con, active_only=True):
        if r["scope"] == "country" and not country:
            continue                                   # country rule needs a country
        if r["check_type"] == "data":
            fn = DATA_VERIFIERS.get(r["ref"])
            ok = bool(fn(con, code, country)) if fn else False
        else:
            ok = _has_doc(con, code, r["ref"], country if r["scope"] == "country" else None)
        out.append((r["key"], r["label"], r["scope"], ok))
    return out

def checklist_ready(con, code, country=None):
    items = evaluate_checklist(con, code, country)
    return all(ok for _, _, _, ok in items)

# ---------------------------------------------------------------- mini-CRM: documents
# Generate documents (contract, power of attorney, …) by filling {{placeholders}} in an
# uploaded template with the customer's own data. The user prepares the template; the
# system only substitutes the fields. Supported templates: .txt/.html/.md (full) and
# .docx (best-effort text replacement inside the Word XML).
import re as _re

# Refund-country -> national tax authority name, for {{tax_authority}} in generated
# powers of attorney / cover letters. Covers the system's refund countries; an
# unknown country yields "" (the merge never raw-substitutes a guess).
TAX_AUTHORITY = {
    "Belgium": "Federale Overheidsdienst Financiën",
    "France":  "Direction générale des Finances publiques",
    "Germany": "Bundeszentralamt für Steuern",
    "Spain":   "Agencia Estatal de Administración Tributaria",
    "Denmark": "Skattestyrelsen",
    "Poland":  "Krajowa Administracja Skarbowa",
    "Austria": "Bundesministerium für Finanzen",
    "Sweden":  "Skatteverket",
}

def merge_fields(con, code, country=None):
    """The data available to a template for one customer (+ refund country). Returns a
    {placeholder: value} dict — every value a string. Use as {{company_name}} etc."""
    import datetime
    c = con.execute("SELECT * FROM customers WHERE code=?", (code,)).fetchone()
    f = {}
    if c:
        for k in c.keys():
            # tenant_id is internal multi-tenancy plumbing, not a template
            # placeholder — keep it out of the merge fields (and template_fields()).
            if k == tenancy.TENANT_COLUMN:
                continue
            f[k] = c[k]
    # prefer the dedicated refund-payout account; fall back to any account on file
    bank = con.execute("""SELECT iban, swift, bank FROM customer_bank_accounts
                          WHERE customer=?
                          ORDER BY (purpose='refund payout') DESC, iban LIMIT 1""",
                       (code,)).fetchone()
    f["bank_iban"] = bank["iban"] if bank else ""
    f["bank_swift"] = bank["swift"] if bank else ""
    f["bank_name"] = bank["bank"] if bank else ""
    f["refund_country"] = country or ""
    f["tax_authority"] = TAX_AUTHORITY.get(country or "", "")
    # service fee for this (customer, refund country): per-country override else default
    fee_pct, fee_min = fee_for(code, country)
    f["fee_pct"] = fee_pct
    f["fee_min"] = fee_min
    f["fee_pct_fmt"] = f"{fee_pct:g}%"
    # supplier account numbers held by this customer, one "<supplier>: <account_no>" / line
    sa = con.execute("""SELECT supplier, account_no FROM customer_supplier_accounts
                        WHERE customer=? ORDER BY supplier""", (code,)).fetchall()
    f["supplier_accounts"] = "\n".join(f"{r['supplier']}: {r['account_no']}" for r in sa)
    today = datetime.date.today()
    f["today"] = today.isoformat()
    f["today_fmt"] = today.strftime("%-d %B %Y") if os.name != "nt" else today.strftime("%#d %B %Y")
    return {k: ("" if v is None else str(v)) for k, v in f.items()}

def template_fields():
    """The placeholder names a template may use (for the on-screen hint). Must match the
    keys merge_fields() actually emits (a test guards against drift)."""
    base = ["company_name", "code", "reg_number", "vat_number", "legal_address", "country",
            "nace_code", "home_portal", "phone", "email", "status", "notes", "payout_route",
            "signatory_name", "signatory_title",
            "bank_iban", "bank_swift", "bank_name", "refund_country", "tax_authority",
            "fee_pct", "fee_min", "fee_pct_fmt", "supplier_accounts", "today", "today_fmt"]
    return base

def _fill_text(raw, fields):
    text = raw.decode("utf-8", "replace")
    for k, v in fields.items():
        text = text.replace("{{" + k + "}}", v)
    return text.encode("utf-8")

def _xml_esc(v):
    """XML-escape a substituted value so it stays valid markup."""
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# Which parts of a .docx carry visible body/header/footer text we should fill.
def _is_fillable_part(n):
    return (n == "word/document.xml"
            or (n.startswith("word/header") and n.endswith(".xml"))
            or (n.startswith("word/footer") and n.endswith(".xml")))

# Match a single <w:t ...>...</w:t> run-text element (capturing the inner text).
_WT_RE = _re.compile(r"(<w:t\b[^>]*>)(.*?)(</w:t>)", _re.DOTALL)
# Split a part into <w:p>...</w:p> paragraphs while keeping the delimiters in place so
# the document can be reassembled byte-for-byte outside the paragraphs we rewrite.
_WP_RE = _re.compile(r"(<w:p\b[^>]*>.*?</w:p>)", _re.DOTALL)

def _sub_whole(text, fields):
    """Per-<w:t> substitution: only fills placeholders already whole inside one run, so
    every other run's formatting/markup is preserved untouched."""
    def repl(m):
        inner = m.group(2)
        for k, v in fields.items():
            inner = inner.replace("{{" + k + "}}", _xml_esc(v))
        return m.group(1) + inner + m.group(3)
    return _WT_RE.sub(repl, text)

def _fill_paragraph(para, fields):
    """Fill one <w:p> paragraph. Placeholders already whole inside a single <w:t> are
    filled per-run (formatting preserved). If a {{...}} placeholder is SPLIT across runs,
    that paragraph's run text is merged: the substitution runs on the concatenation, the
    result is written into the FIRST <w:t>, and the remaining <w:t> runs are blanked.

    FORMATTING TRADEOFF: merging collapses the split placeholder's runs onto the first
    run's formatting — but ONLY for paragraphs that actually contain a split placeholder.
    Paragraphs with whole placeholders (or none) keep every run's formatting intact."""
    runs = list(_WT_RE.finditer(para))
    if not runs:
        return para
    concat = "".join(m.group(2) for m in runs)
    # Is there a placeholder that is NOT wholly contained in a single <w:t>? Compare the
    # placeholders found across the whole-paragraph text vs. those found per individual run.
    para_ph = set(_re.findall(r"\{\{(\w+)\}\}", concat))
    whole_ph = set()
    for m in runs:
        whole_ph |= set(_re.findall(r"\{\{(\w+)\}\}", m.group(2)))
    split_ph = para_ph - whole_ph
    if not split_ph:
        # nothing split here — safe per-run substitution keeps all formatting
        return _sub_whole(para, fields)
    # merge: substitute on the concatenation, then redistribute (all into run 0)
    merged = concat
    for k, v in fields.items():
        merged = merged.replace("{{" + k + "}}", _xml_esc(v))
    pieces = [merged] + [""] * (len(runs) - 1)
    out, last = [], 0
    for m, piece in zip(runs, pieces):
        out.append(para[last:m.start()])
        out.append(m.group(1) + piece + m.group(3))
        last = m.end()
    out.append(para[last:])
    return "".join(out)

def _fill_docx(raw, fields):
    """Replace {{placeholders}} inside a .docx (a zip of XML), tolerant of placeholders
    Word has SPLIT across formatting runs. We work per <w:p> paragraph: whole placeholders
    are filled per-run (formatting preserved); only paragraphs with a split placeholder are
    run-merged (see _fill_paragraph). xml:space="preserve" and run attributes are kept; the
    output is a valid OOXML zip."""
    import io, zipfile
    src = io.BytesIO(raw); out = io.BytesIO()
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                data = zin.read(n)
                if _is_fillable_part(n):
                    text = data.decode("utf-8", "replace")
                    text = _WP_RE.sub(lambda m: _fill_paragraph(m.group(1), fields), text)
                    # also catch any <w:t> text that lives OUTSIDE a <w:p> (rare, e.g.
                    # some header/footer constructs) with the formatting-safe per-run pass
                    text = _sub_whole(text, fields)
                    data = text.encode("utf-8")
                zout.writestr(n, data)
    return out.getvalue()

def _docx_leftovers(filled):
    """Re-read the body/header/footer XML from a PRODUCED .docx, concatenate their decoded
    <w:t> text, and report any {{field}} that survived (split or whole)."""
    import io, zipfile
    chunks = []
    try:
        with zipfile.ZipFile(io.BytesIO(filled)) as zin:
            for n in zin.namelist():
                if _is_fillable_part(n):
                    text = zin.read(n).decode("utf-8", "replace")
                    chunks.append("".join(m.group(2) for m in _WT_RE.finditer(text)))
    except (zipfile.BadZipFile, OSError):
        return []
    joined = "".join(chunks)
    return sorted(set(_re.findall(r"\{\{(\w+)\}\}", joined)))

def fill_template(raw, ext, fields):
    """Fill a template's bytes with `fields`. Returns (bytes, ext, leftover_placeholders)."""
    ext = (ext or "txt").lower().lstrip(".")
    if ext == "docx":
        filled = _fill_docx(raw, fields)
        # re-read the produced docx's <w:t> text (concatenated, so SPLIT/unfilled
        # placeholders are detected too) rather than scanning raw zip bytes.
        leftover = _docx_leftovers(filled)
    else:
        filled = _fill_text(raw, fields)
        leftover = sorted(set(_re.findall(r"\{\{(\w+)\}\}", filled.decode("utf-8", "replace"))))
    return filled, ext, leftover

def text_to_pdf(text, title="document"):
    """Minimal, dependency-free text→PDF (A4, Helvetica 11pt, word-wrapped, paginated).
    Good enough for a contract/POA draft; no images or rich styling."""
    import textwrap
    lines = []
    for para in text.split("\n"):
        lines.extend(textwrap.wrap(para, width=92) or [""])
    per_page, leading, x, y0 = 54, 14, 50, 800
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[""]]

    def pdf_escape(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    objs = []                                   # object bodies, 1-indexed
    page_ids = [4 + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")                       # 1
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")  # 2
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")  # 3
    for i, pg in enumerate(pages):                                         # 4,6,8…
        content = "BT /F1 11 Tf {} {} Td {} ET".format(
            x, y0, f" 0 -{leading} Td ".join(f"({pdf_escape(l)}) Tj" for l in pg))
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_ids[i]+1} 0 R >>")
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    out, offsets = ["%PDF-1.4"], []
    pos = len(out[0]) + 1
    for n, body in enumerate(objs, 1):
        obj = f"{n} 0 obj\n{body}\nendobj"
        offsets.append(pos); pos += len(obj) + 1
        out.append(obj)
    xref_pos = pos
    xref = ["xref", f"0 {len(objs)+1}", "0000000000 65535 f "]
    xref += [f"{o:010d} 00000 n " for o in offsets]
    out.append("\n".join(xref))
    out.append(f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF")
    return "\n".join(out).encode("latin-1", "replace")

def add_template(con, name, kind, filename, body):
    ext = (filename.rsplit(".", 1)[-1] if "." in (filename or "") else "txt").lower()
    cur = con.execute("""INSERT INTO doc_templates (name, kind, ext, filename, body)
                         VALUES (?,?,?,?,?)""",
                      ((name or "template").strip(), (kind or "other").strip(), ext,
                       filename, sqlite3.Binary(body)))
    con.commit()
    return cur.lastrowid

def list_templates(con):
    return con.execute("""SELECT id, name, kind, ext, filename, uploaded_at
                          FROM doc_templates ORDER BY name""").fetchall()

def get_template(con, tid):
    return con.execute("SELECT * FROM doc_templates WHERE id=?", (tid,)).fetchone()

def delete_template(con, tid):
    con.execute("DELETE FROM doc_templates WHERE id=?", (tid,))
    con.commit()

def generate_document(con, tid, code, country=None, as_pdf=False):
    """Fill template `tid` with customer `code`'s data. Returns (bytes, out_filename, ext,
    leftover_placeholders) or (None, …) if the template is missing.

    `as_pdf=True` converts a text-based template (.txt/.md/.html) to a simple PDF, and a
    .docx template to PDF via LibreOffice (doc_render.docx_to_pdf). If the .docx->PDF
    conversion is unavailable (no soffice / failure), it falls back to delivering the
    prefilled .docx (ext stays 'docx') — so a PDF was requested but a .docx is returned;
    the caller can detect that (`as_pdf and ext=='docx'`) to message the user."""
    t = get_template(con, tid)
    if not t:
        return None, None, None, []
    fields = merge_fields(con, code, country)
    filled, ext, leftover = fill_template(t["body"], t["ext"], fields)
    if as_pdf and ext == "docx":
        import doc_render
        pdf = doc_render.docx_to_pdf(filled)
        if pdf is not None:
            filled, ext = pdf, "pdf"
        # else: graceful fallback — keep the prefilled .docx (ext stays 'docx')
    elif as_pdf and ext != "docx":
        text = filled.decode("utf-8", "replace")
        if ext == "html":
            text = _re.sub(r"<[^>]+>", "", text)        # strip tags for the PDF draft
        filled, ext = text_to_pdf(text, t["name"] or "document"), "pdf"
    base = (t["name"] or "document").strip().replace(" ", "_")
    out_name = f"{base}_{code}" + (f"_{country}" if country else "") + f".{ext}"
    return filled, out_name, ext, leftover

# ---------------------------------------------------------------- document requests
# A document_request tracks ONE generate-sign-receive lifecycle for a contract or a
# power of attorney (per refund country). The states form a strict linear pipeline with
# a single escape hatch (cancel); each *_at column timestamps entering that state. The
# generated draft AND the uploaded signed original both AUTO-VAULT into customer_documents
# (stable id + backup-manifest scope) via add_document — the canonical vaulting path.
DOC_REQUEST_KINDS = ("contract", "power_of_attorney")
DOC_REQUEST_STATES = ("requested", "generated", "sent_for_signature",
                      "signed", "received", "cancelled")
DOC_REQUEST_TRANSITIONS = {
    "requested": {"generated", "cancelled"},
    "generated": {"sent_for_signature", "cancelled"},
    "sent_for_signature": {"signed", "cancelled"},
    "signed": {"received", "cancelled"},
    "received": set(),
    "cancelled": set(),
}
# state -> the *_at column stamped on entering it (requested_at is set at INSERT)
_DOC_REQUEST_STAMP = {
    "generated": "generated_at",
    "sent_for_signature": "sent_at",
    "signed": "signed_at",
    "received": "received_at",
}
# How long a request may sit in a state before pending_document_requests flags it
# overdue (days). sent_for_signature is the slow leg (waiting on the client to sign).
DOC_REQUEST_OVERDUE_DAYS = 14


def _docreq_log():
    import applog
    return applog.get("customer_master")


def create_document_request(con, code, kind, template_id, country=None,
                            requested_by=None, note=None):
    """Open a NEW document request in status 'requested'. Validates `kind` and that the
    customer exists. The bound INSERT trigger audits it. Self-commits (matches the other
    customer_master writers). Returns the new request id."""
    code = (code or "").strip().upper()
    if kind not in DOC_REQUEST_KINDS:
        raise ValueError(f"unknown document-request kind '{kind}' "
                         f"(have: {', '.join(DOC_REQUEST_KINDS)})")
    if not con.execute("SELECT 1 FROM customers WHERE code=?", (code,)).fetchone():
        raise ValueError(f"customer {code} not found")
    cur = con.execute("""INSERT INTO document_requests
        (customer, kind, refund_country, template_id, status, requested_by, note)
        VALUES (?,?,?,?, 'requested', ?, ?)""",
        (code, kind, (country or None), template_id, requested_by, note))
    con.commit()
    return cur.lastrowid


def get_document_request(con, req_id):
    r = con.execute("SELECT * FROM document_requests WHERE id=?", (req_id,)).fetchone()
    return dict(r) if r else None


def list_document_requests(con, code=None, status=None):
    q = "SELECT * FROM document_requests"
    where, params = [], []
    if code:
        where.append("customer=?"); params.append((code or "").strip().upper())
    if status:
        where.append("status=?"); params.append(status)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id"
    return [dict(r) for r in con.execute(q, params).fetchall()]


def generate_request_document(con, req_id):
    """Generate (or RE-generate) the draft for a document request and auto-vault it.

    Allowed only while the request is still in 'requested' or 'generated' (re-generate);
    a cancelled/sent/signed/received request is refused. Fills the request's template via
    generate_document(..., as_pdf=True) — which may fall back to a .docx when soffice is
    unavailable, so we store WHATEVER bytes/ext were produced (not necessarily a PDF).

    The produced bytes are vaulted via add_document under the request's kind/country, the
    new customer_documents.id + sha256 are recorded, generated_at is stamped and the status
    set to 'generated'. On RE-generate the PRIOR generated document row is deleted from the
    vault first so a stale draft is never left orphaned (the signed original is untouched).
    Self-commits. Returns (bytes, out_name, ext)."""
    r = get_document_request(con, req_id)
    if not r:
        raise ValueError(f"document request {req_id} not found")
    if r["status"] not in ("requested", "generated"):
        raise ValueError(f"cannot generate in status '{r['status']}' "
                         f"(only 'requested' or 'generated')")
    if not r["template_id"]:
        raise ValueError("document request has no template_id to generate from")
    code, country = r["customer"], r["refund_country"]
    filled, out_name, ext, _leftover = generate_document(
        con, r["template_id"], code, country, as_pdf=True)
    if filled is None:
        raise ValueError(f"template {r['template_id']} not found")
    # RE-generate: drop the previous generated draft so we never orphan a stale file.
    old_doc_id = r["generated_doc_id"]
    if old_doc_id:
        _delete_vault_document(con, old_doc_id)
    import hashlib
    sha = hashlib.sha256(filled).hexdigest()
    add_document(con, code, r["kind"], out_name, filled, country=country)
    doc_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("""UPDATE document_requests
        SET status='generated', generated_at=CURRENT_TIMESTAMP,
            generated_sha256=?, generated_doc_id=?
        WHERE id=?""", (sha, doc_id, req_id))
    con.commit()
    return filled, out_name, ext


def _delete_vault_document(con, doc_id):
    """Best-effort removal of a customer_documents row + its stored bytes (used when a
    re-generate supersedes an earlier draft). Never raises — a vault hiccup must not block
    the regenerate; it is logged. The audited DELETE keeps an audit trail of the removal."""
    try:
        row = con.execute("SELECT stored_path, backend FROM customer_documents WHERE id=?",
                          (doc_id,)).fetchone()
        if not row:
            return
        try:
            import document_vault
            be = document_vault.backend(DOCDIR)
            if hasattr(be, "delete") and row["stored_path"]:
                be.delete(row["stored_path"])
        except Exception:
            _docreq_log().exception("vault delete failed for doc %s", doc_id)
        con.execute("DELETE FROM customer_documents WHERE id=?", (doc_id,))
    except Exception:
        _docreq_log().exception("_delete_vault_document failed for doc %s", doc_id)


def advance_document_request(con, req_id, new_status, *, signed_file=None,
                             signed_filename=None, by=None, note=None):
    """Move a request along its lifecycle. Returns (ok, msg).

    Rejects (without changing anything) any transition not in
    DOC_REQUEST_TRANSITIONS[current]. Advancing to the SAME status is treated as an
    idempotent no-op (ok). On entering a state its matching *_at column is stamped.
    On 'received' with a `signed_file`, the uploaded signed ORIGINAL is vaulted via
    add_document and its customer_documents.id stored as signed_doc_id. `note`, when
    given, is appended to the request note. The audited UPDATE records the actor.
    Self-commits. Never raises — returns (False, msg) on any handled error."""
    try:
        r = get_document_request(con, req_id)
        if not r:
            return False, f"document request {req_id} not found"
        cur = r["status"]
        if new_status == cur:
            return True, f"already in '{cur}'"
        if new_status not in DOC_REQUEST_STATES:
            return False, f"unknown status '{new_status}'"
        if new_status not in DOC_REQUEST_TRANSITIONS.get(cur, set()):
            return False, f"illegal transition {cur} -> {new_status}"
        sets, vals = ["status=?"], [new_status]
        stamp = _DOC_REQUEST_STAMP.get(new_status)
        if stamp:
            sets.append(f"{stamp}=CURRENT_TIMESTAMP")
        if note:
            sets.append("note=COALESCE(note || char(10), '') || ?")
            vals.append(note)
        if new_status == "received" and signed_file is not None:
            add_document(con, r["customer"], r["kind"],
                         signed_filename or f"{r['kind']}_signed",
                         signed_file, country=r["refund_country"])
            doc_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            sets.append("signed_doc_id=?"); vals.append(doc_id)
        vals.append(req_id)
        con.execute(f"UPDATE document_requests SET {', '.join(sets)} WHERE id=?", vals)
        con.commit()
        return True, f"{cur} -> {new_status}"
    except Exception as e:
        _docreq_log().exception("advance_document_request failed")
        return False, f"advance failed: {e}"


# open states whose age the worklist tracks (everything pre-terminal)
_DOC_REQUEST_OPEN = ("requested", "generated", "sent_for_signature", "signed")


def pending_document_requests(con, overdue_days=DOC_REQUEST_OVERDUE_DAYS):
    """Open document requests (not received/cancelled) for a worklist. Each row gets an
    integer `age_days` (since requested_at) and an `overdue` flag — True when it has sat
    in 'sent_for_signature' (the leg waiting on the client) longer than `overdue_days`."""
    qmarks = ",".join("?" * len(_DOC_REQUEST_OPEN))
    rows = con.execute(
        f"""SELECT *,
                  CAST(julianday('now') - julianday(requested_at) AS INTEGER) AS age_days,
                  CAST(julianday('now') - julianday(sent_at)       AS INTEGER) AS sent_age_days
           FROM document_requests
           WHERE status IN ({qmarks})
           ORDER BY requested_at""", _DOC_REQUEST_OPEN).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        sent_age = d.pop("sent_age_days", None)
        d["overdue"] = (d["status"] == "sent_for_signature"
                        and sent_age is not None and sent_age > overdue_days)
        out.append(d)
    return out


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
                   VALUES (?,?, 'requested', CURRENT_TIMESTAMP)
                   ON CONFLICT(customer, country) DO UPDATE SET
                     status=CASE WHEN customer_countries.status='active' THEN 'active' ELSE 'requested' END,
                     requested_at=COALESCE(customer_countries.requested_at, CURRENT_TIMESTAMP)""",
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

def country_ready_to_activate(con, code, country):
    """True when every document a refund country requires is on file (and still valid),
    so the UI can offer "country X now ready to activate" — e.g. after a received PoA.
    This is INFORMATIONAL only: it does NOT activate the country (that stays the admin's
    explicit `activate_country` click) and is NOT a gate."""
    _items, ready = country_doc_checklist(con, code, country)
    return ready

def activate_country(con, code, country, active):
    if active:
        con.execute("""INSERT INTO customer_countries (customer, country, status, activated_at)
                       VALUES (?,?, 'active', CURRENT_TIMESTAMP)
                       ON CONFLICT(customer, country) DO UPDATE SET
                         status='active', activated_at=CURRENT_TIMESTAMP""", (code, country.strip()))
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
