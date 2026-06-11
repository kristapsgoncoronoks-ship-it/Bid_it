"""
SUPPLIER MASTER DATABASE - suppliers.db (SEPARATE from fuel_history.db)

Master data about product suppliers: legal identity, VAT registrations per
country, bank accounts, contacts, product catalogs with codes and discount
terms, payment terms, and the invoice registry. The transactional database
(fuel_history.db: transactions, VAT applications, documents) references
suppliers only by code - clean separation of master data vs. transactions.

Usage:
    python3 supplier_db.py                 -> (re)build schema + seed, print cards
    python3 supplier_db.py Q8              -> print one supplier card
Helpers for other modules:
    get_issuer(code, country) -> (legal_name, vat_id_or_None, note)
    get_invoices(code, country) -> [(invoice_no, date), ...]
"""
import sqlite3, sys
import audit

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/suppliers.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    code TEXT PRIMARY KEY, legal_name TEXT, group_name TEXT,
    address TEXT, home_country TEXT, company_reg TEXT,
    phone TEXT, email TEXT, portal TEXT,
    payment_terms TEXT, payment_notes TEXT, status TEXT DEFAULT 'active', notes TEXT);
CREATE TABLE IF NOT EXISTS supplier_vat_registrations (
    supplier TEXT, country TEXT, vat_number TEXT, source TEXT,
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

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try: con.execute("ALTER TABLE suppliers ADD COLUMN invoice_cadence TEXT DEFAULT 'monthly'")
    except sqlite3.OperationalError: pass  # column already exists (safe)
    audit.install_audit(con, ['suppliers', 'supplier_vat_registrations', 'supplier_bank_accounts', 'supplier_products', 'supplier_invoices'])
    return con

def seed(con):
    con.executemany("""INSERT OR REPLACE INTO suppliers
        (code, legal_name, group_name, address, home_country, company_reg, phone, email,
         portal, payment_terms, payment_notes, status, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", SUPPLIERS)
    con.executemany("INSERT OR REPLACE INTO supplier_vat_registrations VALUES (?,?,?,?)", VAT_REGS)
    con.executemany("INSERT OR REPLACE INTO supplier_bank_accounts VALUES (?,?,?,?,?,?,?)", BANKS)
    con.executemany("INSERT OR REPLACE INTO supplier_products VALUES (?,?,?,?,?,?,?)", PRODUCTS)
    con.executemany("INSERT OR REPLACE INTO supplier_invoices VALUES (?,?,?,?,?,?,?,?)", INVOICE_REG)
    for code, cad in (("E100","semi-monthly"),("DKV","semi-monthly"),("Q8","monthly-per-country"),
                      ("MOEVE","monthly"),("BP","monthly"),("TFC","monthly"),("PORTONE","monthly")):
        con.execute("UPDATE suppliers SET invoice_cadence=? WHERE code=?", (cad, code))
    con.commit()

# ---- API used by the VAT refund module (replaces vat_config ISSUERS/INVOICES) ----
def get_issuer(code, country=None):
    con = connect()
    s = con.execute("SELECT legal_name FROM suppliers WHERE code=?", (code,)).fetchone()
    v = con.execute("""SELECT vat_number, source FROM supplier_vat_registrations
                       WHERE supplier=? AND country=?""", (code, country)).fetchone()
    con.close()
    name = s["legal_name"] if s else code
    if v and v["vat_number"]:
        return name, v["vat_number"], v["source"]
    return name, None, (v["source"] if v else "no VAT registration on file - INPUT")

def get_invoices(code, country):
    con = connect()
    rows = con.execute("""SELECT invoice_no, invoice_date FROM supplier_invoices
                          WHERE supplier=? AND country=? ORDER BY invoice_date""",
                       (code, country)).fetchall()
    con.close()
    return [(r["invoice_no"], r["invoice_date"]) for r in rows] or \
           [(f"INPUT: {country} invoice", "")]

def card(con, code):
    s = con.execute("SELECT * FROM suppliers WHERE code=?", (code,)).fetchone()
    print(f"\n=== {s['code']}: {s['legal_name']} ({s['group_name']}) [{s['status']}] ===")
    for k in ("address","home_country","company_reg","phone","email","portal",
              "payment_terms","payment_notes","notes"):
        if s[k]: print(f"  {k:14}: {s[k]}")
    for label, q in (("VAT regs","SELECT country, COALESCE(vat_number,'INPUT'), source FROM supplier_vat_registrations WHERE supplier=?"),
                     ("Banks","SELECT beneficiary, iban, COALESCE(swift,''), bank FROM supplier_bank_accounts WHERE supplier=?"),
                     ("Products","SELECT COALESCE(product_code,''), product_name, product_group, vat_rate, discount_terms FROM supplier_products WHERE supplier=?"),
                     ("Invoices","SELECT country, invoice_no, invoice_date, currency, gross_total FROM supplier_invoices WHERE supplier=?")):
        rows = con.execute(q, (code,)).fetchall()
        if rows:
            print(f"  {label}:")
            for r in rows: print("    -", " | ".join(str(x) for x in r))

if __name__ == "__main__":
    con = connect(); seed(con)
    codes = [sys.argv[1]] if len(sys.argv) > 1 else \
            [r[0] for r in con.execute("SELECT code FROM suppliers ORDER BY code")]
    for c in codes: card(con, c)
    con.close()
