"""
SUPPLIER MASTER DATABASE - suppliers.db (SEPARATE from fuel_history.db)

Master data about product suppliers: legal identity, VAT registrations per
country, bank accounts, contacts, product catalogs with codes and discount
terms, payment terms, and the invoice registry. The transactional database
(fuel_history.db: transactions, VAT applications, documents) references
suppliers only by code - clean separation of master data vs. transactions.

Usage:
    python3 supplier_master.py                 -> (re)build schema + seed, print cards
    python3 supplier_master.py Q8              -> print one supplier card
Helpers for other modules:
    get_issuer(code, country) -> (legal_name, vat_id_or_None, note)
    get_invoices(code, country) -> [(invoice_no, date), ...]
"""
import sqlite3, sys, re
import audit
import applog
import db_tuning
import db_migrate
import tenancy
import paths

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = paths.db_path("suppliers.db")   # default-env value (repo); tests may monkeypatch
_DB_DEFAULT = DB                     # import-time default, to detect an explicit override

def _db():
    """Resolve the suppliers.db path FRESH so FFS_DATA_DIR (per-test isolation) is
    honored at call time; an explicit monkeypatch of DB still wins."""
    return paths.db_path("suppliers.db") if DB == _DB_DEFAULT else DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    code TEXT PRIMARY KEY, legal_name TEXT, group_name TEXT,
    address TEXT, home_country TEXT, company_reg TEXT,
    phone TEXT, email TEXT, portal TEXT,
    payment_terms TEXT, payment_notes TEXT, status TEXT DEFAULT 'active', notes TEXT);
CREATE TABLE IF NOT EXISTS supplier_vat_registrations (
    supplier TEXT, country TEXT, vat_number TEXT, source TEXT, entity_name TEXT,
    PRIMARY KEY (supplier, country));
CREATE TABLE IF NOT EXISTS supplier_bank_accounts (
    supplier TEXT, beneficiary TEXT, iban TEXT PRIMARY KEY, swift TEXT,
    bank TEXT, currency TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS supplier_products (
    supplier TEXT, product_code TEXT, product_name TEXT, product_group TEXT,
    unit TEXT, vat_rate TEXT, discount_terms TEXT,
    PRIMARY KEY (supplier, product_code, product_name));
CREATE TABLE IF NOT EXISTS supplier_statements (
    supplier TEXT, statement_ref TEXT, period TEXT, statement_date TEXT,
    notes TEXT, PRIMARY KEY (supplier, statement_ref));
CREATE TABLE IF NOT EXISTS statement_invoices (
    supplier TEXT, statement_ref TEXT, invoice_no TEXT, invoice_date TEXT,
    country TEXT, currency TEXT, net REAL, vat REAL, gross REAL,
    PRIMARY KEY (supplier, statement_ref, invoice_no));
CREATE TABLE IF NOT EXISTS supplier_invoices (
    supplier TEXT, country TEXT, invoice_no TEXT, invoice_date TEXT,
    period TEXT, currency TEXT, gross_total REAL, notes TEXT,
    PRIMARY KEY (supplier, invoice_no));
"""

SUPPLIERS = [
 ("Q8","Kuwait Petroleum (Q8) - per-country entities","Q8 / Kuwait Petroleum International",
  "Per-country invoicing entities (BE, DE, FR, ES, DK, PL, AT)", "BE", None, None, None,
  "Q8 e-invoice portal","Net + country VAT at list price; rebates via partner Port One OU",
  "Payment to Port One per net invoice", "active",
  "Card: Q8 Liberty/IDS. Always reconcile Q8 payment summary WITH Port One rebate invoice."),
 ("PORTONE","Port One OU","Port One",
  "Estonia (rebate partner for Q8 volumes)","EE",None,None,None,
  "Account manager","Net payable ~14th of following month",
  "Country rebate invoice (e.g. EE2605310167) nets against Q8 totals","active",
  "Open item: LU 7.01 EUR rebate with zero LU transactions; diesel/AdBlue rebate split to confirm."),
 ("BP","B2Mobility GmbH / BP Europa SE (Poland invoicing)","BP / Aral",
  "Poland invoicing via B2Mobility","DE/PL",None,None,None,
  "BP/Aral fleet portal (CSV export)","Due 15th of following month",
  "SPLIT PAYMENT (MPP) mandatory in PL","active",
  "A2 toll services carry ~2.5% ORS fee lines. NIP to capture from invoice - INPUT."),
 ("TFC","TFC by Moya","Moya Energy",
  "Belgium (hubs Meer / Meer 2 / Maasmechelen)","BE",None,None,None,
  "TFC customer portal","Per contract",
  None,"active",
  "Discount -0.205/L ONLY at TFC hubs (Meer -0.19); third-party stations (Romac, Texaco, "
  "De Warande) undiscounted. BE VAT number to capture - INPUT."),
 ("E100","E100 International Trade sp. z o.o.","E100",
  "ul. Pory 78/7, 02-757 Warszawa, Poland","PL","KRS 0000636760; 23015/2019 2019-10-11 [A+]",
  None,None,"E100 personal account (XLS export)",
  "Invoice 1-15 due 15th next month; invoice 16-31 due 30th",
  "Pay to UAB 'Europiniu korteliu servisas' (PVM LT100010734410)","active",
  "Two invoices per month. Discount tiers by station color code 0.08-0.23 EUR/L diesel."),
 ("MOEVE","Moeve Pro Services, S.A.U. (ex-Cepsa)","Moeve",
  "Paseo de la Castellana 259 A, 28046 Madrid, Spain","ES",
  "Reg.Merc.Madrid Tomo 6265 Folio 0 Seccion 8 Hoja M-102131",
  "+34 917 288 801","tarjetasmoeve@moeveglobal.com","www.moeve.es card management",
  "Bank transfer due 30th of following month",
  "Cash-at-pump possible (pagado al contado) - nets against transfer amount","active",
  "ALL invoice amounts VAT-inclusive. PRN discounts off pump PVP. 6-decimal internal calc."),
 ("DKV","DKV Euro Service GmbH + Co. KG","DKV Mobility",
  "Balcke-Duerr-Allee 3, D-40882 Ratingen, Germany","DE",
  "Register Court Duesseldorf HRA 4053; GP Verwaltungsgesellschaft EGRIMA mbH HRB 1703",
  "+49 2102 5518-0",None,"DKV eReporting (CSV / API)",
  "Per DKV terms, payable in EUR (FX per transaction date)",
  "Swedish fiscal rep: Deutsch-Schwedische Handelskammer, Valhallavaegen 185, Stockholm","active",
  "Flat 1.30 SEK/L diesel discount in SE; 5.63% service fee on parking/services."),
]

VAT_REGS = [
 ("E100","Belgium","BE0676647155","printed on invoices BE98759/BE99954"),
 ("MOEVE","Spain","ESA25009192","CIF printed on invoice BA72400000187538"),
 ("DKV","Sweden","SE502044770101","printed on e-invoices"),
 ("Q8","Belgium",None,"INPUT from country invoice BEOI00118939"),
 ("Q8","Germany",None,"INPUT from country invoice DEVR00473179"),
 ("Q8","France",None,"INPUT"),("Q8","Spain",None,"INPUT"),("Q8","Denmark",None,"INPUT"),
 ("Q8","Poland",None,"INPUT"),("Q8","Austria",None,"INPUT"),
 ("BP","Poland",None,"NIP - INPUT from invoice 0261167596"),
 ("TFC","Belgium",None,"INPUT from invoice 26056012270"),
 ("PORTONE","Estonia",None,"INPUT from invoice EE2605310167"),
]

BANKS = [
 ("E100","UAB 'Europiniu korteliu servisas' (PVM LT100010734410)",
  "LT814010051003712098","AGBLLT2X","Luminor Bank AB, Vilnius","EUR","primary"),
 ("E100","UAB 'Europiniu korteliu servisas'",
  "LT067300010151868677","HABALT22","Swedbank AB, Vilnius","EUR","alternative"),
 ("MOEVE","Moeve Pro Services, S.A.U.",
  "ES1500495298972616000390",None,"(per invoice)","EUR","transferencia bancaria"),
]

PRODUCTS = [
 ("E100","27","Diesel euro","Diesel","L","BE 21%","per-litre discount 0.08-0.23 by station tier"),
 ("E100","41","AdBlue","AdBlue","L","BE 21%","0.22-0.23/L; up to 0.60/L at La Louviere (late May)"),
 ("E100","63","Parking","Parking","unit","BE 21%","none"),
 ("DKV","0009","DIESEL","Diesel","L","SE 25%","flat 1.30 SEK/L net"),
 ("DKV","0016","ADBLUE (bulk)","AdBlue","L","SE 25%","~1.978 SEK/L net"),
 ("DKV","0024","HVO 100","HVO","L","SE 25%","1.30 SEK/L; ~43% price premium vs diesel"),
 ("DKV","0088","parking service","Parking","pc","SE 25%","no discount; +5.63% service fee"),
 ("MOEVE",None,"DIESEL STAR","Diesel","L","ES 10% (incl.)","PRN off PVP, ~0.17-0.26 EUR/L"),
 ("MOEVE",None,"GASOLEO A","Diesel","L","ES 10% (incl.)","PRN off PVP"),
 ("MOEVE",None,"ECOBLUE","AdBlue","L","ES 21% (incl.)","PRN at most stations; none at Canfranc"),
 ("BP",None,"ON ACT.","Diesel","L","PL 8%","contract net pricing"),
 ("BP",None,"ADBLUE","AdBlue","L","PL 23%","contract net pricing"),
 ("BP",None,"Toll Service / Oplata ORS","Toll/Fees","pc","PL 23%","~2.5% ORS fee on A2 tolls"),
 ("TFC",None,"Diesel","Diesel","L","BE 21%","-0.205/L TFC hubs only (Meer -0.19); 3rd-party 0"),
 ("Q8",None,"DIESEL / GASOIL / Gasoil N","Diesel","L","per country","list price; rebate via Port One"),
 ("Q8",None,"ADBLUE / AdB.","AdBlue","L","per country","rebate via Port One"),
]

INVOICE_REG = [
 ("Q8","Belgium","BEOI00118939","2026-05-31","2026-05","EUR",45926.38,"country invoice"),
 ("Q8","Germany","DEVR00473179","2026-05-31","2026-05","EUR",5108.45,"country invoice"),
 ("Q8","(multi)","DE00752298","2026-05-31","2026-05","EUR",67227.03,"payment summary, 7 countries"),
 ("PORTONE","Estonia","EE2605310167","2026-05-31","2026-05","EUR",54859.20,
  "rebate invoice; rebates total -12,367.83; payable, due 14.06"),
 ("BP","Poland","0261167596","2026-06-01","2026-05","PLN",72215.36,"split payment, due 15.06"),
 ("TFC","Belgium","26056012270","2026-05-31","2026-05","EUR",51369.99,None),
 ("E100","Belgium","BE98759/5539713","2026-05-15","2026-05","EUR",45990.04,"1-15 May, due 15.06"),
 ("E100","Belgium","BE99954/5586443","2026-05-31","2026-05","EUR",48447.10,"16-31 May, due 30.06"),
 ("MOEVE","Spain","BA72400000187538","2026-05-31","2026-05","EUR",61297.15,
  "721.65 paid cash at pump; 60,575.50 by transfer due 30.06"),
 ("DKV","Sweden","26/651689595/011","2026-05-15","2026-05","SEK",214089.51,"EUR 19,779.26"),
 ("DKV","Sweden","26/652169828/011","2026-05-31","2026-05","SEK",148330.66,"EUR 13,757.93"),
]

_SCHEMA_READY = set()   # DB files whose schema is set up this process

def connect():
    DB = _db()   # resolve the on-disk path fresh (honors FFS_DATA_DIR / an override)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)   # audit triggers call ffs_actor(); register it every connect
    # Schema/migration/trigger setup persists in the file; only do it once per
    # process per DB (this connect() is called many times per request).
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        # Structured contract terms for the compliance auditor: the rebate that SHOULD
        # be applied (EUR/L) and/or a NET price ceiling (EUR/L) for matching lines.
        # Created BEFORE db_migrate.apply so the P1 tenant_id ALTER below can target it.
        con.execute("""CREATE TABLE IF NOT EXISTS supplier_discounts (
            id INTEGER PRIMARY KEY,
            supplier TEXT, country TEXT DEFAULT '%', station_like TEXT DEFAULT '%',
            product_group TEXT DEFAULT 'Diesel',
            expected_discount_eur_l REAL, max_net_eur_l REAL,
            note TEXT, active INTEGER DEFAULT 1)""")
        db_migrate.apply(con, "supplier_master", [
            "ALTER TABLE suppliers ADD COLUMN invoice_cadence TEXT DEFAULT 'monthly'",
            # P1 multi-tenancy (schema plumbing only): stamp every tenant-owned table
            # in suppliers.db with a tenant_id; existing rows backfill to
            # DEFAULT_TENANT_ID via the column DEFAULT, new rows default too. NO query
            # reads this column yet (the `multitenant` switch is OFF and scope_clause is
            # unwired until P2), so this is a pure no-behavior-change addition. This runs
            # on the WRITABLE engine path (suppliers.db is engine-owned; the app reads it
            # READ-ONLY via dataproduct). APPEND-ONLY — keep at END.
            *tenancy.tenant_column_ddls([
                "suppliers", "supplier_bank_accounts", "supplier_vat_registrations",
                "supplier_discounts", "supplier_invoices", "supplier_products",
                "supplier_statements", "statement_invoices",
            ]),
            # ── PK RE-KEY (multi-tenant): tenant-qualified PRIMARY KEYs ──────────
            # The engine-owned suppliers.db natural-key tables carry a tenant_id
            # column (P1, the tenant_column_ddls spread above) and tenant-scoped reads /
            # stamped writes (P2), but their PRIMARY KEYs did NOT include tenant_id — so
            # the seeders' INSERT OR REPLACE, set_vat_registration's
            # ON CONFLICT(supplier, country), and register_statement's INSERT OR REPLACE
            # resolve on the NATURAL key only. Under the `multitenant` switch ON, two
            # tenants writing the same code / iban / (supplier, country) / (supplier,
            # invoice_no) / (supplier, statement_ref) would COLLIDE and overwrite each
            # other's supplier-master rows — cross-tenant data loss.
            #
            # SQLite cannot ALTER a PRIMARY KEY in place, so each of the SEVEN natural-key
            # tables is REBUILT: create a __rekey twin with tenant_id LAST in the column
            # list but FIRST in the PRIMARY KEY clause (every other column, type and
            # DEFAULT and the rest of the key order preserved), copy all rows (explicit
            # column list — never rely on column order), drop the old, rename the twin.
            # This runs once per DB (versioned), supersedes the PK on both fresh and
            # existing suppliers.db files. This is the WRITABLE engine path
            # (supplier_master.connect()); the app reads suppliers.db READ-ONLY via
            # dataproduct and gets no writable handle. APPEND-ONLY — keep at END.
            #
            # AUDITED-DB SPECIFICS (suppliers / supplier_vat_registrations /
            # supplier_bank_accounts / supplier_products / supplier_invoices ARE audited
            # — see install_audit just after this apply()):
            #   • AUDIT ROWKEY PRESERVED. audit._cols_pk returns pks[0] = the FIRST
            #     pk-flagged column in COLUMN-DEFINITION order (not PK-clause order), and
            #     the trigger logs NEW.{pk}. We define tenant_id as the LAST column but
            #     FIRST in the PRIMARY KEY clause, so pks[0] stays the original natural
            #     rowkey (suppliers->code, supplier_bank_accounts->iban, and supplier for
            #     supplier_vat_registrations / supplier_products / supplier_invoices) and
            #     the audit rowkey is UNCHANGED — never tenant_id/'default'.
            #   • TRIGGERS REINSTATED. DROP TABLE drops aud_<t>_i/u/d. We do NOT
            #     hand-write CREATE TRIGGER here — connect() calls install_audit RIGHT
            #     AFTER db_migrate.apply (below), which recreates the triggers on the
            #     first connect that runs this migration.
            #   • INDEXES / FKs. The current schema declares NO CREATE INDEX on these 7
            #     tables (the only indexes in the schema are audit_log's, on a table we
            #     don't touch); supplier_discounts is NOT rebuilt (surrogate id PK, no
            #     collision risk) so its triggers stay. The schema uses NO FOREIGN KEY
            #     constraints (references are comment-only), so no FK guard is needed.
            #   • The 2 statement tables (supplier_statements / statement_invoices) are
            #     UNAUDITED (benchmark-style rebuild, no trigger concern).
            #
            # OFF-by-default is byte-identical: a single 'default' tenant behaves exactly
            # as the natural PK did.

            # suppliers: PK (code) -> (tenant_id, code). rowkey stays NEW.code.
            """CREATE TABLE IF NOT EXISTS suppliers__rekey (
                code TEXT, legal_name TEXT, group_name TEXT,
                address TEXT, home_country TEXT, company_reg TEXT,
                phone TEXT, email TEXT, portal TEXT,
                payment_terms TEXT, payment_notes TEXT, status TEXT DEFAULT 'active', notes TEXT,
                invoice_cadence TEXT DEFAULT 'monthly',
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, code))""",
            """INSERT INTO suppliers__rekey
                (code, legal_name, group_name, address, home_country, company_reg,
                 phone, email, portal, payment_terms, payment_notes, status, notes,
                 invoice_cadence, tenant_id)
                SELECT code, legal_name, group_name, address, home_country, company_reg,
                 phone, email, portal, payment_terms, payment_notes, status, notes,
                 invoice_cadence, tenant_id
                FROM suppliers""",
            "DROP TABLE suppliers",
            "ALTER TABLE suppliers__rekey RENAME TO suppliers",

            # supplier_vat_registrations: PK (supplier, country) -> (tenant_id, supplier,
            # country). rowkey stays NEW.supplier.
            # entity_name (per-country actual issuing legal entity) is APPENDED to this
            # list at the END (single ALTER); the rekey twin carries the column so a DB
            # rebuilt here lands it. We SELECT a literal NULL for it (not the source
            # column): entity_name is brand-new, so no pre-rekey row can hold a value, and
            # the committed demo suppliers.db has NEVER been rekeyed (no _ffs_migrations) —
            # i.e. its source table has NO entity_name column yet, so referencing it in the
            # SELECT would fail. The END-of-list ALTER is then a tolerated duplicate on an
            # already-rekeyed DB and the real add on one that skipped this rebuild.
            """CREATE TABLE IF NOT EXISTS supplier_vat_registrations__rekey (
                supplier TEXT, country TEXT, vat_number TEXT, source TEXT,
                entity_name TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, country))""",
            """INSERT INTO supplier_vat_registrations__rekey
                (supplier, country, vat_number, source, entity_name, tenant_id)
                SELECT supplier, country, vat_number, source, NULL, tenant_id
                FROM supplier_vat_registrations""",
            "DROP TABLE supplier_vat_registrations",
            "ALTER TABLE supplier_vat_registrations__rekey RENAME TO supplier_vat_registrations",

            # supplier_bank_accounts: PK (iban) -> (tenant_id, iban). `supplier`/
            # `beneficiary` stay non-PK columns; rowkey stays NEW.iban (pks[0]).
            """CREATE TABLE IF NOT EXISTS supplier_bank_accounts__rekey (
                supplier TEXT, beneficiary TEXT, iban TEXT, swift TEXT,
                bank TEXT, currency TEXT, notes TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, iban))""",
            """INSERT INTO supplier_bank_accounts__rekey
                (supplier, beneficiary, iban, swift, bank, currency, notes, tenant_id)
                SELECT supplier, beneficiary, iban, swift, bank, currency, notes, tenant_id
                FROM supplier_bank_accounts""",
            "DROP TABLE supplier_bank_accounts",
            "ALTER TABLE supplier_bank_accounts__rekey RENAME TO supplier_bank_accounts",

            # supplier_products: PK (supplier, product_code, product_name) -> (tenant_id,
            # supplier, product_code, product_name). rowkey stays NEW.supplier.
            """CREATE TABLE IF NOT EXISTS supplier_products__rekey (
                supplier TEXT, product_code TEXT, product_name TEXT, product_group TEXT,
                unit TEXT, vat_rate TEXT, discount_terms TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, product_code, product_name))""",
            """INSERT INTO supplier_products__rekey
                (supplier, product_code, product_name, product_group, unit, vat_rate,
                 discount_terms, tenant_id)
                SELECT supplier, product_code, product_name, product_group, unit, vat_rate,
                 discount_terms, tenant_id
                FROM supplier_products""",
            "DROP TABLE supplier_products",
            "ALTER TABLE supplier_products__rekey RENAME TO supplier_products",

            # supplier_invoices: PK (supplier, invoice_no) -> (tenant_id, supplier,
            # invoice_no). rowkey stays NEW.supplier.
            """CREATE TABLE IF NOT EXISTS supplier_invoices__rekey (
                supplier TEXT, country TEXT, invoice_no TEXT, invoice_date TEXT,
                period TEXT, currency TEXT, gross_total REAL, notes TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, invoice_no))""",
            """INSERT INTO supplier_invoices__rekey
                (supplier, country, invoice_no, invoice_date, period, currency,
                 gross_total, notes, tenant_id)
                SELECT supplier, country, invoice_no, invoice_date, period, currency,
                 gross_total, notes, tenant_id
                FROM supplier_invoices""",
            "DROP TABLE supplier_invoices",
            "ALTER TABLE supplier_invoices__rekey RENAME TO supplier_invoices",

            # supplier_statements (UNAUDITED): PK (supplier, statement_ref) ->
            # (tenant_id, supplier, statement_ref). register_statement adds a `customer`
            # column to this table via a SEPARATE module's (invoice_control) idempotent
            # ALTER. To preserve that column verbatim on an existing DB where it was
            # already added (e.g. the demo blob), we ADD it HERE (just before the rekey)
            # so it exists before the rebuild and is carried over by the INSERT…SELECT;
            # the later invoice_control ALTER then finds it present (tolerated duplicate).
            "ALTER TABLE supplier_statements ADD COLUMN customer TEXT",
            """CREATE TABLE IF NOT EXISTS supplier_statements__rekey (
                supplier TEXT, statement_ref TEXT, period TEXT, statement_date TEXT,
                notes TEXT, customer TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, statement_ref))""",
            """INSERT INTO supplier_statements__rekey
                (supplier, statement_ref, period, statement_date, notes, customer, tenant_id)
                SELECT supplier, statement_ref, period, statement_date, notes, customer, tenant_id
                FROM supplier_statements""",
            "DROP TABLE supplier_statements",
            "ALTER TABLE supplier_statements__rekey RENAME TO supplier_statements",

            # statement_invoices (UNAUDITED): PK (supplier, statement_ref, invoice_no) ->
            # (tenant_id, supplier, statement_ref, invoice_no).
            """CREATE TABLE IF NOT EXISTS statement_invoices__rekey (
                supplier TEXT, statement_ref TEXT, invoice_no TEXT, invoice_date TEXT,
                country TEXT, currency TEXT, net REAL, vat REAL, gross REAL,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, statement_ref, invoice_no))""",
            """INSERT INTO statement_invoices__rekey
                (supplier, statement_ref, invoice_no, invoice_date, country, currency,
                 net, vat, gross, tenant_id)
                SELECT supplier, statement_ref, invoice_no, invoice_date, country, currency,
                 net, vat, gross, tenant_id
                FROM statement_invoices""",
            "DROP TABLE statement_invoices",
            "ALTER TABLE statement_invoices__rekey RENAME TO statement_invoices",

            # PER-COUNTRY ENTITY NAME (append-only, END of list). The actual local
            # issuing/supplying legal entity for a (supplier, country) — e.g. EUROWAG ->
            # "W.A.G. Deutschland GmbH" in DE — distinct from suppliers.legal_name (one per
            # supplier). The rekey twin above already carries this column on a freshly
            # rebuilt DB; this ALTER adds it to a DB that SKIPPED the rebuild (already
            # rekeyed under old code), and is a tolerated duplicate where it is present.
            # Default NULL = "not set" -> get_issuer falls back to suppliers.legal_name.
            "ALTER TABLE supplier_vat_registrations ADD COLUMN entity_name TEXT",

            # ── BRAND ALIASES (append-only, END of list) ────────────────────────
            # The SUPPLIER LEGAL ENTITY (suppliers.code/legal_name) is the canonical
            # identity; BRAND names read off an invoice (e.g. "Shell", "Circle K",
            # "Neste") are EXPLICIT links/aliases to a legal-entity supplier. A legal
            # entity may carry MANY brands; brand_norm is the normalized lookup key
            # (the SAME normalization the app's _resolve_supplier_code uses) so a brand
            # typed in the UI matches what intake reads. UNIQUE on (tenant_id, supplier,
            # brand_norm) — the same brand can't be linked twice to one supplier, but
            # could (rarely) name two suppliers; the resolver/code_for_brand take the
            # first by code so the result is deterministic. Tenant-stamped (P1 plumbing)
            # so a brand link is tenant-scoped like the rest of suppliers.db. This is the
            # WRITABLE engine/admin path (supplier_master.connect()); the app reads the
            # table READ-ONLY via dataproduct for recognition.
            """CREATE TABLE IF NOT EXISTS supplier_brands (
                supplier TEXT, brand TEXT, brand_norm TEXT,
                created_at TEXT, created_by TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, brand_norm))""",

            # ── BRAND markers gain a per-(supply) COUNTRY scope (append-only, END) ──────
            # Brand markers are matched COUNTRY BY COUNTRY (country of supply): the SAME brand
            # may resolve to a supplier only for the countries an admin approved (E100 in BE
            # vs PL; Eurowag in ES/PL/LT). country='' = a GLOBAL marker (matches ANY supply
            # country) and is the back-compat default for rows created before this column.
            # Re-key so country is part of the PK and the same (supplier, brand) can exist
            # once PER country (mirrors the rekey twins above).
            "ALTER TABLE supplier_brands ADD COLUMN country TEXT NOT NULL DEFAULT ''",
            """CREATE TABLE IF NOT EXISTS supplier_brands__rekey (
                supplier TEXT, brand TEXT, brand_norm TEXT,
                country TEXT NOT NULL DEFAULT '',
                created_at TEXT, created_by TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                PRIMARY KEY (tenant_id, supplier, brand_norm, country))""",
            """INSERT INTO supplier_brands__rekey
                (supplier, brand, brand_norm, country, created_at, created_by, tenant_id)
                SELECT supplier, brand, brand_norm, country, created_at, created_by, tenant_id
                FROM supplier_brands""",
            "DROP TABLE supplier_brands",
            "ALTER TABLE supplier_brands__rekey RENAME TO supplier_brands",
        ])
        audit.install_audit(con, ['suppliers', 'supplier_vat_registrations', 'supplier_bank_accounts',
                                  'supplier_products', 'supplier_invoices', 'supplier_discounts',
                                  'supplier_brands'])
        _SCHEMA_READY.add(DB)
    return con

def set_discount_rule(supplier, country="%", station_like="%", product_group="Diesel",
                      expected_discount_eur_l=None, max_net_eur_l=None, note=""):
    """Add/replace a contract term used by the compliance auditor: the rebate that
    should be applied (EUR/L) and/or a NET price ceiling (EUR/L) for matching lines.
    `country`/`station_like` are SQL LIKE patterns ('%' = any)."""
    con = connect()
    # Multi-tenant P2: stamp the bound tenant on this engine/seed write. queue_tenant()
    # is the soft (never-raising) primitive — supplier writes run on the worker/engine/
    # seed where a missing tenant is normal and defaults to DEFAULT_TENANT_ID (== the
    # column DEFAULT), so OFF is byte-identical.
    con.execute("""INSERT INTO supplier_discounts
        (supplier, country, station_like, product_group, expected_discount_eur_l, max_net_eur_l, note, active, tenant_id)
        VALUES (?,?,?,?,?,?,?,1,?)""",
        (supplier.upper(), country, station_like, product_group,
         expected_discount_eur_l, max_net_eur_l, note, tenancy.queue_tenant()))
    con.commit(); rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]; con.close()
    return rid

def set_vat_registration(supplier, country, vat_number, source="document mining",
                         entity_name=None):
    """Upsert a supplier's VAT registration for a country (used to fill INPUT gaps).

    `entity_name` (optional) is the actual per-country issuing/supplying legal entity
    name that lands on the legal VAT claim (distinct from the supplier's default
    legal_name). PRECEDENCE: a 'capture'-sourced entity_name NEVER overwrites an
    existing non-'capture' (admin/manual/document-mining) one — a human edit always
    wins; it only SEEDS an empty/capture slot. Passing entity_name=None leaves any
    existing entity_name untouched (so VAT-only callers keep today's behavior)."""
    con = connect()
    # Multi-tenant P2: stamp the tenant on the registration row (soft queue_tenant —
    # OFF -> DEFAULT_TENANT_ID == the column DEFAULT, byte-identical).
    tid = tenancy.queue_tenant()
    # Decide the entity_name to persist, honoring manual > capture precedence. We read
    # the existing row first so a capture write can't clobber an admin/manual name and a
    # VAT-only write (entity_name=None) preserves whatever is already there.
    frag, params = tenancy.scope_clause()
    cur = con.execute("""SELECT entity_name, source, vat_number FROM supplier_vat_registrations
                         WHERE supplier=? AND country=?""" + frag,
                      [supplier, country, *params]).fetchone()
    existing_name = (cur["entity_name"] if cur and cur["entity_name"] else "") or ""
    existing_src = (cur["source"] if cur else "") or ""
    existing_vat = (cur["vat_number"] if cur and cur["vat_number"] else None)
    new_name = (entity_name or "").strip()
    capture_decline = (source == "capture" and existing_name and existing_src != "capture")
    if not new_name:
        # No entity_name supplied: keep whatever is on file.
        eff_name = existing_name or None
        eff_src = source
    elif capture_decline:
        # A manual/admin/document-mining name is on file: a capture write must NOT
        # overwrite the entity_name OR downgrade the row's source — the human edit wins.
        eff_name = existing_name
        eff_src = existing_src or source
    else:
        eff_name = new_name
        eff_src = source
    # VAT NUMBER precedence (this is the VAT id on the legal claim): a 'capture' write must
    # NOT overwrite an existing CURATED (non-'capture') VAT number — an AI-misread VAT id
    # can't clobber an admin/document-mining one. A missing new VAT keeps the existing.
    if source == "capture" and existing_vat and existing_src != "capture":
        eff_vat = existing_vat
    elif vat_number:
        eff_vat = vat_number
    else:
        eff_vat = existing_vat
    con.execute("""INSERT INTO supplier_vat_registrations
                     (supplier, country, vat_number, source, entity_name, tenant_id)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(tenant_id, supplier, country) DO UPDATE SET
                     vat_number=excluded.vat_number,
                     source=excluded.source,
                     entity_name=excluded.entity_name""",
                (supplier, country, eff_vat, eff_src, eff_name, tid))
    con.commit(); con.close()

def vat_registrations():
    con = connect()
    # Multi-tenant P2: no base WHERE -> lead with WHERE 1=1 so the scope fragment ANDs on.
    frag, params = tenancy.scope_clause()
    rows = [dict(r) for r in con.execute(
        "SELECT supplier, country, vat_number, source FROM supplier_vat_registrations "
        "WHERE 1=1" + frag, params)]
    con.close()
    return rows

def discount_rules(active_only=True):
    con = connect()
    # Multi-tenant P2: scope before ORDER BY; WHERE 1=1 carries both the optional
    # active filter and the tenant fragment.
    frag, params = tenancy.scope_clause()
    q = ("SELECT * FROM supplier_discounts WHERE 1=1"
         + (" AND active=1" if active_only else "") + frag + " ORDER BY supplier, id")
    rows = [dict(r) for r in con.execute(q, params)]
    con.close()
    return rows

def delete_discount_rule(rule_id):
    con = connect()
    # Multi-tenant P2: scope the DELETE so a tenant cannot delete another tenant's rule.
    frag, params = tenancy.scope_clause()
    con.execute("DELETE FROM supplier_discounts WHERE id=?" + frag, [rule_id, *params])
    con.commit(); con.close()

def seed(con):
    # Multi-tenant P2: stamp the tenant explicitly on every seeded row. queue_tenant()
    # is the soft primitive — seed runs as a system write with no tenant bound, so it
    # defaults to DEFAULT_TENANT_ID (== the column DEFAULT). Stamping it explicitly (vs.
    # relying on the DEFAULT) is byte-identical OFF and lets an ON tenant-bound re-seed
    # land its tenant.
    tid = tenancy.queue_tenant()
    con.executemany("""INSERT OR REPLACE INTO suppliers
        (code, legal_name, group_name, address, home_country, company_reg, phone, email,
         portal, payment_terms, payment_notes, status, notes, tenant_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [(*r, tid) for r in SUPPLIERS])
    # Explicit column lists (NOT positional VALUES) so the trailing P1 tenant_id
    # column takes a value rather than throwing a column-count mismatch.
    con.executemany("""INSERT OR REPLACE INTO supplier_vat_registrations
        (supplier, country, vat_number, source, tenant_id) VALUES (?,?,?,?,?)""",
        [(*r, tid) for r in VAT_REGS])
    con.executemany("""INSERT OR REPLACE INTO supplier_bank_accounts
        (supplier, beneficiary, iban, swift, bank, currency, notes, tenant_id)
        VALUES (?,?,?,?,?,?,?,?)""", [(*r, tid) for r in BANKS])
    con.executemany("""INSERT OR REPLACE INTO supplier_products
        (supplier, product_code, product_name, product_group, unit, vat_rate, discount_terms, tenant_id)
        VALUES (?,?,?,?,?,?,?,?)""", [(*r, tid) for r in PRODUCTS])
    con.executemany("""INSERT OR REPLACE INTO supplier_invoices
        (supplier, country, invoice_no, invoice_date, period, currency, gross_total, notes, tenant_id)
        VALUES (?,?,?,?,?,?,?,?,?)""", [(*r, tid) for r in INVOICE_REG])
    frag, params = tenancy.scope_clause()
    for code, cad in (("E100","semi-monthly"),("DKV","semi-monthly"),("Q8","monthly-per-country"),
                      ("MOEVE","monthly"),("BP","monthly"),("TFC","monthly"),("PORTONE","monthly")):
        con.execute("UPDATE suppliers SET invoice_cadence=? WHERE code=?" + frag,
                    [cad, code, *params])
    con.commit()

# ---- API used by the VAT refund module (replaces vat_config ISSUERS/INVOICES) ----
def get_issuer(code, country=None, con=None):
    own = con is None
    if own: con = connect()
    # Multi-tenant P2: scope both single-table reads. Called by the vat_refund claim
    # build and by the engine — the worker binds the job's tenant, so these scope ON;
    # OFF the fragment is ("",[]) and they are byte-identical.
    frag, params = tenancy.scope_clause()
    s = con.execute("SELECT legal_name FROM suppliers WHERE code=?" + frag,
                    [code, *params]).fetchone()
    v = con.execute("""SELECT vat_number, source, entity_name FROM supplier_vat_registrations
                       WHERE supplier=? AND country=?""" + frag,
                    [code, country, *params]).fetchone()
    if own: con.close()
    # Issuer NAME precedence (this lands on the legal VAT claim): the per-country actual
    # issuing entity_name when set, ELSE the supplier's default legal_name, ELSE the code.
    # NULL/empty entity_name => byte-identical to the pre-feature behavior.
    entity = (v["entity_name"] if v and v["entity_name"] else "").strip() if v else ""
    name = entity or (s["legal_name"] if s else code)
    if v and v["vat_number"]:
        return name, v["vat_number"], v["source"]
    return name, None, (v["source"] if v else "no VAT registration on file - INPUT")

def get_invoices(code, country, con=None):
    own = con is None
    if own: con = connect()
    # Multi-tenant P2: scope before ORDER BY (claim build + engine read; OFF inert).
    frag, params = tenancy.scope_clause()
    rows = con.execute("""SELECT invoice_no, invoice_date FROM supplier_invoices
                          WHERE supplier=? AND country=?""" + frag
                       + " ORDER BY invoice_date", [code, country, *params]).fetchall()
    if own: con.close()
    return [(r["invoice_no"], r["invoice_date"]) for r in rows] or \
           [(f"INPUT: {country} invoice", "")]

# ---- AUTO-ONBOARD an unknown supplier from an extracted invoice (engine path) ----
# EU VAT-number prefix -> ISO 3166-1 alpha-2 country. A VAT id always starts with the
# member-state code (Directive 2008/9/EC issuers are EU), so the prefix is the most
# reliable signal for a provisional supplier's home_country. EL is Greece's VAT prefix
# (ISO is GR); XI is Northern Ireland under the NI Protocol. Anything not here -> None
# (we never GUESS a country from a non-EU/garbage prefix).
VAT_PREFIX_COUNTRY = {
    "AT": "AT", "BE": "BE", "BG": "BG", "CY": "CY", "CZ": "CZ", "DE": "DE",
    "DK": "DK", "EE": "EE", "EL": "GR", "ES": "ES", "FI": "FI", "FR": "FR",
    "GR": "GR", "HR": "HR", "HU": "HU", "IE": "IE", "IT": "IT", "LT": "LT",
    "LU": "LU", "LV": "LV", "MT": "MT", "NL": "NL", "PL": "PL", "PT": "PT",
    "RO": "RO", "SE": "SE", "SI": "SI", "SK": "SK", "XI": "XI",
}


def country_from_vat(vat_number):
    """Derive an ISO country code from a EU VAT number's two-letter prefix, or None
    when the prefix is absent/unrecognised (so a garbage id never invents a country).
    The lookup is case-insensitive and tolerates spaces/punctuation before the code."""
    if not vat_number:
        return None
    v = "".join(str(vat_number).split()).upper()
    if len(v) < 2:
        return None
    return VAT_PREFIX_COUNTRY.get(v[:2])


# ──────────────────────── BRAND ALIASES (legal entity = canonical) ────────────
# A supplier (LEGAL ENTITY, e.g. "Shell Latvia SIA") may carry many BRANDS read off
# invoices (e.g. "Shell"). These helpers maintain the explicit brand→entity links so
# intake recognises a brand as its legal entity instead of treating it as unknown.

def _norm_brand(s):
    """Normalise a brand for matching: lowercase, drop punctuation, collapse spaces.
    Kept BYTE-IDENTICAL to app._resolve_supplier_code's _norm_name so a brand typed in
    the UI matches exactly what intake reads off a PDF (casefold + strip punctuation/
    whitespace). Pure; never raises."""
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _norm_country(c):
    """Canonical form of a brand marker's SUPPLY-COUNTRY scope: trimmed (empty = GLOBAL =
    any country). Matching is case-insensitive; admins pick from the supplier's registered
    country names so spelling stays consistent. Pure; never raises."""
    return (c or "").strip()


def add_brand(code, brand, country="", actor=None):
    """Link a BRAND name to a legal-entity supplier CODE, OPTIONALLY scoped to a SUPPLY
    COUNTRY (admin-curated marker). country='' = a GLOBAL marker (matches any supply
    country); a country-scoped marker is compared ONLY for that country of supply, so the
    same brand can map per country (E100 in BE vs PL). Idempotent (the (tenant, supplier,
    brand_norm, country) PK upserts), audited, tenant-stamped. Returns True when a link
    exists after the call, False on a blank code/brand or any error (never raises)."""
    code = (code or "").strip().upper()
    brand = (brand or "").strip()
    country = _norm_country(country)
    bn = _norm_brand(brand)
    if not code or not bn:
        return False
    con = None
    try:
        con = connect()
        if actor:
            audit.set_actor(con, actor)
        tid = tenancy.queue_tenant()
        con.execute("""INSERT INTO supplier_brands
                         (supplier, brand, brand_norm, country, created_at, created_by, tenant_id)
                       VALUES (?,?,?,?,datetime('now'),?,?)
                       ON CONFLICT(tenant_id, supplier, brand_norm, country) DO UPDATE SET
                         brand=excluded.brand""",
                    (code, brand, bn, country, actor, tid))
        con.commit()
        return True
    except Exception as e:
        applog.get("supplier_master").warning("add_brand failed: %s", e)
        return False
    finally:
        if con is not None:
            con.close()


def remove_brand(code, brand, country=""):
    """Unlink a BRAND from a supplier CODE for a given country scope (matched on the
    normalized brand + country, so the exact marker the UI shows is removed). country='' =
    the global marker. Audited (DELETE trigger), tenant-scoped. Returns True when a row was
    deleted, False otherwise; never raises -> False."""
    code = (code or "").strip().upper()
    bn = _norm_brand(brand)
    country = _norm_country(country)
    if not code or not bn:
        return False
    con = None
    try:
        con = connect()
        frag, params = tenancy.scope_clause()
        cur = con.execute(
            "DELETE FROM supplier_brands WHERE supplier=? AND brand_norm=? "
            "AND LOWER(country)=LOWER(?)" + frag, [code, bn, country, *params])
        con.commit()
        return (cur.rowcount or 0) > 0
    except Exception as e:
        applog.get("supplier_master").warning("remove_brand failed: %s", e)
        return False
    finally:
        if con is not None:
            con.close()


def brands_for(code, con=None):
    """Every BRAND marker linked to a supplier CODE, in display order. Tenant-scoped (OFF
    inert). Returns a list of {"brand", "country"} dicts (country='' = global/any country);
    never raises -> []."""
    code = (code or "").strip().upper()
    if not code:
        return []
    own = con is None
    try:
        if own:
            con = connect()
        frag, params = tenancy.scope_clause()
        rows = con.execute(
            "SELECT brand, country FROM supplier_brands WHERE supplier=?" + frag
            + " ORDER BY brand, country", [code, *params]).fetchall()
        return [{"brand": r["brand"], "country": r["country"] or ""} for r in rows]
    except Exception as e:
        applog.get("supplier_master").warning("brands_for failed: %s", e)
        return []
    finally:
        if own and con is not None:
            con.close()


def code_for_brand(brand, country=None, con=None):
    """Resolve a BRAND name (for a given SUPPLY COUNTRY) to the linked legal-entity supplier
    CODE, or None. Matches the normalized brand and PREFERS a marker scoped to this country
    of supply, falling back to a GLOBAL marker (country=''). Deterministic on collisions:
    country-specific beats global, then lowest code wins. Tenant-scoped (OFF inert). Never
    raises -> None."""
    bn = _norm_brand(brand)
    if not bn:
        return None
    cn = _norm_country(country)
    own = con is None
    try:
        if own:
            con = connect()
        frag, params = tenancy.scope_clause()
        r = con.execute(
            "SELECT supplier FROM supplier_brands WHERE brand_norm=? "
            "AND (LOWER(country)=LOWER(?) OR country='')" + frag
            + " ORDER BY (country='') ASC, supplier LIMIT 1",
            [bn, cn, *params]).fetchone()
        return r["supplier"] if r else None
    except Exception as e:
        applog.get("supplier_master").warning("code_for_brand failed: %s", e)
        return None
    finally:
        if own and con is not None:
            con.close()


def all_brand_map(country=None, con=None):
    """Bulk {brand_norm: supplier_code} for the resolver (one read, no per-brand query), for
    a given SUPPLY COUNTRY. Country-specific markers beat GLOBAL (country='') ones, then
    lowest code wins (matches code_for_brand). When country is None/'', only global markers
    apply. Tenant-scoped (OFF inert). Never raises -> {}."""
    cn = _norm_country(country)
    own = con is None
    try:
        if own:
            con = connect()
        frag, params = tenancy.scope_clause()
        rows = con.execute(
            "SELECT brand_norm, supplier FROM supplier_brands "
            "WHERE (LOWER(country)=LOWER(?) OR country='')" + frag
            + " ORDER BY (country='') ASC, supplier", [cn, *params]).fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["brand_norm"], r["supplier"])   # specific-first, then lowest code
        return out
    except Exception as e:
        applog.get("supplier_master").warning("all_brand_map failed: %s", e)
        return {}
    finally:
        if own and con is not None:
            con.close()


def supplier_exists(code, con=None):
    """True if a `suppliers` row exists for `code` (tenant-scoped, OFF inert). Used by
    the engine onboarding handler to avoid clobbering a known supplier; the WEB request
    checks existence READ-ONLY via dataproduct, never this writable handle."""
    code = (code or "").strip().upper()
    if not code:
        return False
    own = con is None
    if own:
        con = connect()
    frag, params = tenancy.scope_clause()
    r = con.execute("SELECT 1 FROM suppliers WHERE code=?" + frag,
                    [code, *params]).fetchone()
    if own:
        con.close()
    return r is not None


def create_provisional_supplier(code, legal_name=None, home_country=None,
                                vat_number=None, invoice_ref=None, con=None):
    """Engine-side handler for AUTO-ONBOARDING an unknown supplier read off an invoice.

    Inserts a `suppliers` row with status='provisional' (NOT 'active') and a note that
    records the source invoice, so a mis-read can never silently pollute the active
    master — an admin confirms it before it goes live. Idempotent: an existing supplier
    (any status) is LEFT UNTOUCHED (returns False) so re-running can't overwrite a
    confirmed supplier or downgrade it back to provisional. When a VAT number is given
    its registration is recorded for home_country too. Tenant-stamped + audited (the
    suppliers table carries audit triggers); the caller binds the audit actor/tenant.

    Returns True if a new provisional supplier was created, False if it already existed.
    Raises ValueError on a blank code or a missing home_country (we never onboard a
    supplier with no country — the caller derives one from the VAT prefix first)."""
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("supplier code required")
    home_country = (home_country or "").strip().upper() or None
    if not home_country:
        raise ValueError("home_country required to onboard a provisional supplier")
    own = con is None
    if own:
        con = connect()
    try:
        if supplier_exists(code, con=con):
            return False
        tid = tenancy.queue_tenant()
        note = (f"auto-onboarded from invoice {invoice_ref} — confirm details"
                if invoice_ref else "auto-onboarded from invoice — confirm details")
        con.execute("""INSERT INTO suppliers
            (code, legal_name, group_name, address, home_country, company_reg, phone,
             email, portal, payment_terms, payment_notes, status, notes, tenant_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, (legal_name or "").strip() or code, None, None, home_country, None,
             None, None, None, None, None, "provisional", note, tid))
        if vat_number:
            con.execute("""INSERT OR REPLACE INTO supplier_vat_registrations
                (supplier, country, vat_number, source, tenant_id) VALUES (?,?,?,?,?)""",
                (code, home_country, str(vat_number).strip(),
                 f"auto-onboarded from invoice {invoice_ref}" if invoice_ref
                 else "auto-onboarded from invoice", tid))
        con.commit()
        return True
    finally:
        if own:
            con.close()


def list_provisional(con=None):
    """Every supplier still awaiting admin confirmation (status='provisional'), oldest
    code first. Tenant-scoped (OFF inert). Returns a list of dicts."""
    own = con is None
    if own:
        con = connect()
    frag, params = tenancy.scope_clause()
    rows = con.execute(
        "SELECT code, legal_name, home_country, notes FROM suppliers "
        "WHERE status='provisional'" + frag + " ORDER BY code", params).fetchall()
    if own:
        con.close()
    return [dict(r) for r in rows]


def activate_supplier(code, con=None):
    """Confirm a provisional supplier -> status='active' (the engine write behind the
    admin 'Activate' action). Only flips a row that is currently 'provisional', so it
    can never resurrect a deactivated supplier. Audited + tenant-scoped. Returns True
    if a row was activated."""
    code = (code or "").strip().upper()
    if not code:
        return False
    own = con is None
    if own:
        con = connect()
    try:
        frag, params = tenancy.scope_clause()
        cur = con.execute(
            "UPDATE suppliers SET status='active' WHERE code=? AND status='provisional'"
            + frag, [code, *params])
        con.commit()
        return (cur.rowcount or 0) > 0
    finally:
        if own:
            con.close()


def card(con, code):
    # Multi-tenant P2: scope every single-table read in the card; OFF inert.
    frag, params = tenancy.scope_clause()
    s = con.execute("SELECT * FROM suppliers WHERE code=?" + frag,
                    [code, *params]).fetchone()
    print(f"\n=== {s['code']}: {s['legal_name']} ({s['group_name']}) [{s['status']}] ===")
    for k in ("address","home_country","company_reg","phone","email","portal",
              "payment_terms","payment_notes","notes"):
        if s[k]: print(f"  {k:14}: {s[k]}")
    for label, q in (("VAT regs","SELECT country, COALESCE(vat_number,'INPUT'), source FROM supplier_vat_registrations WHERE supplier=?"),
                     ("Banks","SELECT beneficiary, iban, COALESCE(swift,''), bank FROM supplier_bank_accounts WHERE supplier=?"),
                     ("Products","SELECT COALESCE(product_code,''), product_name, product_group, vat_rate, discount_terms FROM supplier_products WHERE supplier=?"),
                     ("Invoices","SELECT country, invoice_no, invoice_date, currency, gross_total FROM supplier_invoices WHERE supplier=?")):
        rows = con.execute(q + frag, [code, *params]).fetchall()
        if rows:
            print(f"  {label}:")
            for r in rows: print("    -", " | ".join(str(x) for x in r))

if __name__ == "__main__":
    con = connect(); seed(con)
    frag, params = tenancy.scope_clause()
    codes = [sys.argv[1]] if len(sys.argv) > 1 else \
            [r[0] for r in con.execute(
                "SELECT code FROM suppliers WHERE 1=1" + frag + " ORDER BY code", params)]
    for c in codes: card(con, c)
    con.close()
