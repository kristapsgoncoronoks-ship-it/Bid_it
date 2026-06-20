"""
INVOICING — issue legally-compliant SALES invoices from a client (the platform's own
customer / the "issuer") to THAT client's OWN customers (the "bill-to" parties).

This is a NEW product area, structurally separate from the rest of the platform:

  - It is the OUTBOUND, accounts-RECEIVABLE counterpart of the inbound VAT-refund work.
    The VAT-refund module recovers tax on invoices a client RECEIVES from fuel suppliers;
    this module lets a client ISSUE its own invoices to its customers. The two never share
    a figure, a DB, or a status code.
  - The client's OWN customers live HERE (`bill_customers`), NOT in the platform's
    `customers.db` — that DB holds the PLATFORM's clients (the five Baltic transport
    entities). The two customer notions are deliberately kept apart.

APP-OWNED OVERLAY. Like every other app-owned module this owns its OWN SQLite file
(invoicing.db, gitignored) via connect() + db_migrate, audit-installed, db_tuning-tuned.
It writes NO engine product DB.

LEGAL INVOICE = IMMUTABLE. A draft invoice is freely editable. At ISSUE we assign a
GAP-FREE sequential number, SNAPSHOT the issuer and customer details onto the row (so a
later edit to the issuer profile or the customer book never rewrites a filed invoice),
set status `issued`, and refuse all further edits. Phase 1 lifecycle is `draft` -> `issued`;
the `status` column and an (empty) `invoice_payments` table leave room for the Phase 2/3
`sent`/`paid`/`overdue` states + e-invoice/email outputs + payment tracking with NO schema
reshuffle.

VAT MATH is done through money.py (Decimal, ROUND_HALF_UP) — per line
`line_net = qty * unit_price_net`, `line_vat = line_net * vat_rate`; totals group by VAT
rate. REVERSE CHARGE (cross-border EU B2B) is an EXPLICIT derivation/flag, not a silent
0%: when on, every line is 0% VAT and the mandatory "Reverse charge" wording is emitted.
All amounts are NET EUR (VAT excluded) by default; the currency is per-invoice.

TENANCY. Every table carries a tenant_id (tenancy seam, inert today): stamped on INSERT
via tenancy.write_tenant() and filtered on every read via tenancy.scope_clause() — so a
tenant can never see another tenant's customers / invoices once the switch is ON. The
gap-free COUNTER is per (series, year, tenant_id). MULTI-TENANT NOTE: the issuer profile
is stored today as GLOBAL admin settings (`invoice_issuer_*`); a per-tenant issuer profile
is a Phase 2 item (a small issuers table keyed by tenant_id) — flagged, not built.

GAP-FREE NUMBERING is the legal crux. `next_number(series, year)` runs inside a single
IMMEDIATE (write-locked) transaction that reads-then-bumps the counter row, so two
concurrent issues can never read the same `last_no` and collide or skip — the DB write
lock serialises them and SQLite's busy_timeout makes the loser wait rather than fail.
A number is assigned ONLY at issue, never to a draft.

BEST-EFFORT READS. The read/list helpers degrade to [] / None on a backend error (logged
via applog) rather than taking down a request; the WRITE helpers (create/issue) return
(obj, "") / (None, error) and raise only on a programming error, mirroring sibling modules.
"""
import os
import datetime
import io
import shutil
import struct
import subprocess
import tempfile
import sqlite3
import xml.etree.ElementTree as ET

from markupsafe import escape as esc

import applog
import audit
import db_tuning
import db_migrate
import money
import safexml
import tenancy

log = applog.get("invoicing")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned invoicing DB (gitignored). A module-level attr so tests can repoint it the
# same way they repoint workflow.DB / metadata.DB.
DB = f"{WORKDIR}/invoicing.db"

# Phase-1 invoice lifecycle. `draft` is editable; `issued` is the immutable, numbered
# legal invoice. Phase 3 adds the PAYMENT transitions (`partially_paid`/`paid`); `sent`
# is the Phase-2/email state, `cancelled` a later credit-note state.
#
# OVERDUE is DELIBERATELY NOT a stored status — it is DERIVED at read time from the due
# date + the paid total (see `is_overdue` / `display_status`), so an issued/sent, unpaid
# (or partly-paid) invoice past its due date is always correctly shown overdue without a
# background job racing the stored value.
STATUS_DRAFT = "draft"
STATUS_ISSUED = "issued"
STATUS_SENT = "sent"
STATUS_PARTIALLY_PAID = "partially_paid"
STATUS_PAID = "paid"
STATUS_OVERDUE = "overdue"            # DERIVED only (never stored) — see is_overdue()
STATUS_CANCELLED = "cancelled"        # DERIVED only — an invoice FULLY credited reads cancelled
STATUSES = (STATUS_DRAFT, STATUS_ISSUED, STATUS_SENT, STATUS_PARTIALLY_PAID,
            STATUS_PAID, STATUS_OVERDUE, STATUS_CANCELLED)

# PHASE 4: document type. An ordinary sales invoice vs a credit note (kreditrēķins) — a
# first-class document that reverses/corrects an issued invoice. Both ride the SAME
# tables/machinery; doc_type + corrects_invoice_id (and a separate number series) is the
# whole distinction.
DOC_INVOICE = "invoice"
DOC_CREDIT_NOTE = "credit_note"
# PHASE 7: PROFORMA invoice / QUOTE. NEITHER is a tax invoice — they do NOT consume a legal
# invoice number (they number from their OWN non-legal series), trigger NO output VAT, and are
# EXCLUDED everywhere a real invoice is counted (the VAT-output report, AR/aging, revenue, and
# bank-matching all filter doc_type='invoice'). A proforma/quote is freely editable like any
# draft; it can be CONVERTED to a real DRAFT invoice (copying customer + lines + discounts),
# which then issues normally and gets the legal invoice number.
DOC_PROFORMA = "proforma"
DOC_QUOTE = "quote"
# The set of "real tax invoice" doc types that count toward output VAT / AR / revenue / bank
# matching. A proforma/quote is deliberately NOT in here (see the filters that reference it).
REAL_INVOICE_TYPES = (DOC_INVOICE,)
# Non-legal document types (no legal number, no output VAT, excluded from real-invoice counting).
NON_LEGAL_TYPES = (DOC_PROFORMA, DOC_QUOTE)
# The statuses on which a payment may be recorded (a draft has no legal amount due).
_PAYABLE_STATUSES = (STATUS_ISSUED, STATUS_SENT, STATUS_PARTIALLY_PAID, STATUS_PAID)
# Payment sources (provenance of a payment row).
PAYMENT_SOURCE_MANUAL = "manual"
PAYMENT_SOURCE_BANK = "bank"

# Issuer profile is stored in security.db's app_settings under these keys (admin-only,
# audited via the normal settings audit). Kept as flat settings in Phase 1; a per-tenant
# issuers table is a Phase 2 item (see module docstring).
ISSUER_KEYS = ("name", "address", "vat_number", "reg_no", "iban", "bank",
               "series", "credit_series", "proforma_series", "quote_series",
               "number_format", "payment_terms_days",
               "logo_text", "brand_color")
SETTING_PREFIX = "invoice_issuer_"

# The number format placeholders: {series}, {year}, {seq} (seq zero-padded to {pad}).
DEFAULT_NUMBER_FORMAT = "{series}-{year}-{seq:06d}"
DEFAULT_SERIES = "INV"
# Credit notes number from their OWN series (default 'KR' for kreditrēķins) so their
# gap-free counter is independent of the invoice series — a credit note can never share a
# number with an invoice.
DEFAULT_CREDIT_SERIES = "KR"
# PHASE 7: a PROFORMA invoice / a QUOTE number from their OWN non-legal series so their counter
# can NEVER take a number from the legal invoice series (default 'PROF' / 'PIED' = piedāvājums).
DEFAULT_PROFORMA_SERIES = "PROF"
DEFAULT_QUOTE_SERIES = "PIED"
DEFAULT_PAYMENT_TERMS_DAYS = 14
DEFAULT_CURRENCY = "EUR"
# PHASE 7: a default brand/accent colour for the invoice header (overridable per issuer).
DEFAULT_BRAND_COLOR = "#0a3d62"
# Logo upload constraints: a small, header-sized image embedded as a data: URI in the PDF.
LOGO_MAX_BYTES = 512 * 1024            # 512 KiB cap on the stored logo image
LOGO_ALLOWED_MIME = ("image/png", "image/jpeg")
# Discount kinds for a line / document discount (a PERCENT of the base, or a fixed AMOUNT).
DISC_NONE = ""
DISC_PERCENT = "percent"
DISC_AMOUNT = "amount"

# Latvia 2026 VAT-rate presets offered as a dropdown in the line editor (a custom rate
# is still allowed). Stored/used as FRACTIONS. 21% standard, 12% reduced (e.g. heating,
# press), 5% reduced (e.g. fruit/veg, books), 0% (intra-Community / exports).
LV_VAT_RATE_PRESETS = (0.21, 0.12, 0.05, 0.0)

# Simplified-invoice gross ceiling (EU VAT Dir. Art. 238/226b: member states may permit
# a simplified invoice up to EUR 100; Latvia applies EUR 150). GROSS (VAT-inclusive) EUR.
SIMPLIFIED_GROSS_CEILING_EUR = 150.0

# Statutory retention period for an issued invoice (years). LV/EU record-keeping.
RETENTION_YEARS = 5

# PHASE 6: recurring-invoice frequencies + statuses. `interval_n` (every N periods) scales
# each. The schedule math (advance_next_run) is MONTH-END SAFE for month-based cadences.
FREQ_WEEKLY = "weekly"
FREQ_MONTHLY = "monthly"
FREQ_QUARTERLY = "quarterly"
FREQ_YEARLY = "yearly"
FREQUENCIES = (FREQ_WEEKLY, FREQ_MONTHLY, FREQ_QUARTERLY, FREQ_YEARLY)
# how many MONTHS one period of each month-based frequency spans (weekly handled apart)
_FREQ_MONTHS = {FREQ_MONTHLY: 1, FREQ_QUARTERLY: 3, FREQ_YEARLY: 12}
REC_ACTIVE = "active"
REC_PAUSED = "paused"

# PDF RENDERING. The PRIMARY path renders the HTML/CSS template to a print-ready A4 PDF
# via the wkhtmltopdf CLI (UTF-8 + system TrueType fonts -> Latvian/Unicode renders
# natively, and HTML/CSS gives a real invoice design). On a host WITHOUT wkhtmltopdf
# (e.g. a production server before `apt install wkhtmltopdf`) we DEGRADE to a
# dependency-free PDF that STILL renders Latvian — it embeds a Unicode TrueType font
# (DejaVuSans) and writes the text as Unicode (Type0/Identity-H), so `ā š ž ē ī ū ķ ļ ņ
# ģ č` are real glyphs, never the latin-1 `?` the old shared text_to_pdf produced.
# SERVER NOTE: install wkhtmltopdf (Debian/Ubuntu: `apt install wkhtmltopdf`) to get the
# DESIGNED HTML invoice; without it the Latvian-capable fallback below is used.
WKHTMLTOPDF_BIN = "wkhtmltopdf"
WKHTMLTOPDF_TIMEOUT = 30          # seconds; the subprocess is killed past this
# Candidate Unicode TrueType fonts for the dependency-free fallback (first that exists
# wins). DejaVuSans covers the full Latvian diacritic set.
_FALLBACK_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bill_customers (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    reg_no          TEXT,
    vat_number      TEXT,
    address         TEXT,
    country         TEXT,                       -- ISO-2 (e.g. 'DE'); blank = unknown
    email           TEXT,
    payment_terms_days INTEGER,
    notes           TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    created_by      TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_bill_customers_tenant ON bill_customers(tenant_id, name);

CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY,
    number          TEXT,                       -- NULL until issued (gap-free at issue)
    series          TEXT,
    issue_date      TEXT,                       -- YYYY-MM-DD (set at issue)
    supply_date     TEXT,                       -- YYYY-MM-DD (Art.226 'date of supply')
    due_date        TEXT,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    status          TEXT NOT NULL DEFAULT 'draft',
    customer_id     INTEGER,
    reverse_charge  INTEGER NOT NULL DEFAULT 0,
    -- SNAPSHOTS captured at issue (so later issuer/customer edits don't rewrite a filed
    -- invoice). JSON text; NULL while draft.
    issuer_snapshot   TEXT,
    customer_snapshot TEXT,
    -- totals (NET basis, EUR/local per `currency`)
    net_total       REAL NOT NULL DEFAULT 0,
    vat_total       REAL NOT NULL DEFAULT 0,
    gross_total     REAL NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    created_by      TEXT,
    issued_at       TEXT,
    issued_by       TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_invoices_tenant ON invoices(tenant_id, status, issue_date);
CREATE INDEX IF NOT EXISTS ix_invoices_number ON invoices(number);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id              INTEGER PRIMARY KEY,
    invoice_id      INTEGER NOT NULL,
    line_no         INTEGER NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    quantity        REAL NOT NULL DEFAULT 0,
    unit            TEXT,                        -- e.g. 'h', 'pcs', 'L'
    unit_price_net  REAL NOT NULL DEFAULT 0,
    vat_rate        REAL NOT NULL DEFAULT 0,     -- a FRACTION (0.21 = 21%)
    line_net        REAL NOT NULL DEFAULT 0,
    line_vat        REAL NOT NULL DEFAULT 0,
    goods_code      TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_invoice_lines_invoice ON invoice_lines(invoice_id, line_no);

CREATE TABLE IF NOT EXISTS invoice_counters (
    series     TEXT NOT NULL,
    year       INTEGER NOT NULL,
    last_no    INTEGER NOT NULL DEFAULT 0,
    tenant_id  TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY (series, year, tenant_id)
);

-- Phase 3 payment tracking (schema only; UNUSED in Phase 1 so no later migration reshuffle).
CREATE TABLE IF NOT EXISTS invoice_payments (
    id          INTEGER PRIMARY KEY,
    invoice_id  INTEGER NOT NULL,
    paid_date   TEXT,
    amount      REAL NOT NULL DEFAULT 0,
    currency    TEXT,
    method      TEXT,
    reference   TEXT,
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    created_by  TEXT,
    tenant_id   TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_invoice_payments_invoice ON invoice_payments(invoice_id);

-- PHASE 7: the issuer LOGO image (bytes) lives in the app-owned invoicing DB, NOT in
-- app_settings (which holds text only). One row per tenant (the issuer profile is global
-- today; tenant_id keeps the multi-tenant seam). Embedded as a data: URI on the PDF.
CREATE TABLE IF NOT EXISTS issuer_logo (
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    mime        TEXT,
    data        BLOB,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_by  TEXT,
    PRIMARY KEY (tenant_id)
);
"""

# Versioned migrations: APPEND new statements at the END (positions are stable).
_MIGRATIONS = [
    # PHASE 2 ------------------------------------------------------------------
    # SIMPLIFIED INVOICE (EU VAT Dir. Art. 238 / Art. 226b): when gross <= EUR 150 a
    # member state may relax the full customer-detail requirement. Off by default.
    "ALTER TABLE invoices ADD COLUMN simplified INTEGER NOT NULL DEFAULT 0",
    # FX rate (foreign-per-1-EUR) snapshotted at issue so the VAT-in-EUR figure on a
    # foreign-currency invoice is reproducible (LV/EU rule: VAT must also be stated in
    # EUR). NULL while EUR or until the user supplies a rate.
    "ALTER TABLE invoices ADD COLUMN fx_rate REAL",
    # 5-YEAR RETENTION marker stamped at issue (record-keeping obligation). A date the
    # invoice must be retained until; no enforcement yet, purely a metadata marker.
    "ALTER TABLE invoices ADD COLUMN retain_until TEXT",
    # PHASE 3 — PAYMENT / STATUS TRACKING -------------------------------------
    # Provenance of a payment row: 'manual' (a human keyed it) or 'bank' (confirmed from a
    # bank-statement import). Defaults to 'manual' so any Phase-1-era empty row reads cleanly.
    "ALTER TABLE invoice_payments ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
    # For a bank-sourced payment: the matched bank transaction reference (remittance/Ustrd
    # or the statement's own ref), kept for the audit trail of WHY this payment was booked.
    "ALTER TABLE invoice_payments ADD COLUMN matched_txn_ref TEXT",
    # IDEMPOTENCY key for a bank import: a stable per-transaction id/hash so re-importing
    # the same statement (or the same txn) never double-records. NULL for a manual payment.
    "ALTER TABLE invoice_payments ADD COLUMN txn_id TEXT",
    # Dedupe a bank txn PER TENANT: a partial UNIQUE index over (tenant_id, txn_id) for the
    # non-NULL txn_id rows only, so a re-import collides on the INSERT (caught + skipped) and
    # manual payments (txn_id NULL) are never constrained.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_invoice_payments_txn "
    "ON invoice_payments(tenant_id, txn_id) WHERE txn_id IS NOT NULL",
    # The bill-to customer's stored IBAN — the PAYER account used by the advisory bank-
    # statement matcher (priority (iii): payer IBAN == the customer's IBAN). Optional.
    "ALTER TABLE bill_customers ADD COLUMN iban TEXT",
    # PHASE 4 — EMAIL + CREDIT NOTES -----------------------------------------
    # EMAIL: when an issued invoice has been emailed to the customer the status moves to
    # `sent` (= issued + emailed). These columns record the audited send (when + to whom).
    "ALTER TABLE invoices ADD COLUMN sent_at TEXT",
    "ALTER TABLE invoices ADD COLUMN sent_to TEXT",
    # CREDIT NOTE / cancellation (kreditrēķins): a credit note is a first-class document
    # reusing the invoice machinery. `doc_type` distinguishes an ordinary invoice from a
    # credit note; `corrects_invoice_id` references the ORIGINAL issued invoice the credit
    # note reverses/corrects. A credit note numbers from its OWN series (issuer.credit_series,
    # default 'KR') via the existing invoice_counters keyed by that series, so credit notes
    # never collide with invoice numbers.
    "ALTER TABLE invoices ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'invoice'",
    "ALTER TABLE invoices ADD COLUMN corrects_invoice_id INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_invoices_corrects "
    "ON invoices(corrects_invoice_id) WHERE corrects_invoice_id IS NOT NULL",
    # PHASE 6 — RECURRING INVOICES -------------------------------------------
    # A template that auto-generates invoices on a schedule (e.g. monthly fuel-card
    # billing). The template OWNS no figures — it carries a customer + a JSON line spec +
    # the schedule math, and at each due date `generate_due` composes a DRAFT (or, when
    # auto_issue, an ISSUED) invoice from it via the SAME create_draft/add_line/issue path.
    # Nothing here mutates a filed invoice; the schedule cursor `next_run` advances
    # MONTH-END SAFE (a Jan-31 monthly recurring lands Feb-28/29, never skipping a month).
    """CREATE TABLE IF NOT EXISTS recurring_templates (
        id               INTEGER PRIMARY KEY,
        name             TEXT,
        customer_id      INTEGER,
        currency         TEXT NOT NULL DEFAULT 'EUR',
        lines            TEXT NOT NULL DEFAULT '[]',   -- JSON list of line dicts
        frequency        TEXT NOT NULL DEFAULT 'monthly',
        interval_n       INTEGER NOT NULL DEFAULT 1,   -- every N periods
        start_date       TEXT,                         -- YYYY-MM-DD (first due date)
        next_run         TEXT,                         -- YYYY-MM-DD (next generation due)
        end_date         TEXT,                         -- optional stop date
        max_occurrences  INTEGER,                      -- optional cap on # generated
        occurrences_done INTEGER NOT NULL DEFAULT 0,
        auto_issue       INTEGER NOT NULL DEFAULT 0,   -- 0 = generate a DRAFT for review
        status           TEXT NOT NULL DEFAULT 'active',  -- active | paused
        notes            TEXT,
        created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        created_by       TEXT,
        tenant_id        TEXT NOT NULL DEFAULT 'default'
    )""",
    "CREATE INDEX IF NOT EXISTS ix_recurring_tenant "
    "ON recurring_templates(tenant_id, status, next_run)",
    # The GENERATION LOG / linkage: one row per (template, due-date) the engine generated.
    # It records WHICH invoice came from WHICH template+period (traceability) AND is the
    # IDEMPOTENCY guard — a UNIQUE (tenant_id, template_id, period) means a second run on
    # the same due date collides on the INSERT and is skipped (never double-generates).
    """CREATE TABLE IF NOT EXISTS recurring_runs (
        id           INTEGER PRIMARY KEY,
        template_id  INTEGER NOT NULL,
        period       TEXT NOT NULL,                    -- the due date this run satisfied
        invoice_id   INTEGER,                          -- the generated invoice (NULL = error)
        status       TEXT,                             -- draft | issued | error
        message      TEXT,
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        tenant_id    TEXT NOT NULL DEFAULT 'default'
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_recurring_runs_period "
    "ON recurring_runs(tenant_id, template_id, period)",
    # Linkage on the generated invoice back to its source template (traceability + the AR
    # views can show 'recurring'). NULL for an ordinary, hand-composed invoice.
    "ALTER TABLE invoices ADD COLUMN from_template_id INTEGER",
    # PHASE 7 — DISCOUNTS + PROFORMA/QUOTE ------------------------------------
    # LINE-level discount: a kind ('percent' | 'amount' | '' = none) + a value. The
    # discounted line net = q2(qty*unit_price_net) − discount (clamped ≥ 0); VAT is computed
    # on the DISCOUNTED net. `line_net`/`line_vat` are STORED POST-DISCOUNT (so the VAT-output
    # report and every read sees the post-discount figures). `gross_amount` keeps the
    # PRE-discount line net for the PDF/e-invoice (the EN-16931 line allowance base BG-27/28).
    "ALTER TABLE invoice_lines ADD COLUMN discount_kind TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE invoice_lines ADD COLUMN discount_value REAL NOT NULL DEFAULT 0",
    "ALTER TABLE invoice_lines ADD COLUMN discount_amount REAL NOT NULL DEFAULT 0",
    "ALTER TABLE invoice_lines ADD COLUMN gross_amount REAL NOT NULL DEFAULT 0",
    # INVOICE-level (document) discount: a kind + a value, allocated PER VAT RATE so each
    # rate's taxable net (and its VAT) is reduced proportionally (VAT on the post-discount
    # net). `disc_amount` snapshots the actually-applied document allowance amount (the
    # EN-16931 BT-107 allowance total / BG-20 document allowance).
    "ALTER TABLE invoices ADD COLUMN disc_kind TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE invoices ADD COLUMN disc_value REAL NOT NULL DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN disc_amount REAL NOT NULL DEFAULT 0",
    # PROFORMA/QUOTE: when a proforma/quote is CONVERTED to a real draft invoice, the new
    # invoice links back to the source proforma (traceability). NULL otherwise.
    "ALTER TABLE invoices ADD COLUMN converted_from_id INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_invoices_converted_from "
    "ON invoices(converted_from_id) WHERE converted_from_id IS NOT NULL",
]

_AUDITED_TABLES = ["bill_customers", "invoices", "invoice_lines",
                   "invoice_counters", "invoice_payments",
                   "recurring_templates", "recurring_runs", "issuer_logo"]

_SCHEMA_READY = set()   # DB files whose schema is set up this process


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)      # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "invoicing", _MIGRATIONS)
        audit.install_audit(con, _AUDITED_TABLES)
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


# ============================================================ issuer profile (settings)
def get_issuer():
    """The client's OWN legal entity (the invoice SUPPLIER), as a dict of the ISSUER_KEYS.
    Read from app_settings (security.db). Sensible defaults for the numbering fields so a
    fresh install can still issue. Never raises (read path)."""
    out = {}
    try:
        import auth
        for k in ISSUER_KEYS:
            out[k] = auth.get_setting(SETTING_PREFIX + k, "") or ""
    except Exception as e:
        log.warning("get_issuer read failed, returning blanks: %s", e)
        out = {k: "" for k in ISSUER_KEYS}
    out["series"] = out.get("series") or DEFAULT_SERIES
    out["credit_series"] = out.get("credit_series") or DEFAULT_CREDIT_SERIES
    out["proforma_series"] = out.get("proforma_series") or DEFAULT_PROFORMA_SERIES
    out["quote_series"] = out.get("quote_series") or DEFAULT_QUOTE_SERIES
    out["number_format"] = out.get("number_format") or DEFAULT_NUMBER_FORMAT
    out["payment_terms_days"] = out.get("payment_terms_days") or str(DEFAULT_PAYMENT_TERMS_DAYS)
    out["brand_color"] = out.get("brand_color") or DEFAULT_BRAND_COLOR
    return out


def set_issuer(values):
    """Persist the issuer profile (admin-only at the route layer). `values` is a dict
    keyed by a subset of ISSUER_KEYS. Settings writes are audited by auth. Returns the
    stored issuer dict. Raises only on a missing auth backend (programming error)."""
    import auth
    for k in ISSUER_KEYS:
        if k in values:
            auth.set_setting(SETTING_PREFIX + k, str(values.get(k) or ""))
    return get_issuer()


def issuer_complete(issuer=None):
    """True iff the issuer profile carries the Art.226 MANDATORY supplier fields (legal
    name, address, VAT number). Numbering/bank fields are not strictly mandatory to be
    *present* on the entity, but name/address/VAT are."""
    iss = issuer or get_issuer()
    return all((iss.get(k) or "").strip() for k in ("name", "address", "vat_number"))


# ============================================================ PHASE 7: issuer LOGO / branding
# The issuer LOGO is an optional small header image (PNG/JPG, size-capped). It is stored as
# BYTES in the app-owned invoicing DB (issuer_logo), NOT in app_settings (text only). It is
# embedded as a data: URI in the invoice HTML so wkhtmltopdf renders it; the dependency-free
# Latvian fallback simply OMITS it (it has no image support). A missing/corrupt logo NEVER
# breaks PDF generation — every read path degrades gracefully.

def _sniff_image_mime(data):
    """Detect PNG/JPEG from the magic bytes. Returns a mime string or None. Pure."""
    if not data or len(data) < 4:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def set_issuer_logo(data, *, mime=None, updated_by=None):
    """Store (or, with data=None/empty, CLEAR) the issuer logo image. Validates the size cap
    and that the bytes are a real PNG/JPEG (sniffed, not trusted from the upload). Returns
    (mime, "") on success / ("", "") on clear / (None, error). Never raises."""
    tenant = tenancy.write_tenant()
    try:
        if not data:
            con = connect()
            try:
                con.execute("DELETE FROM issuer_logo WHERE tenant_id=?", (tenant,))
                con.commit()
            finally:
                con.close()
            return "", ""
        if len(data) > LOGO_MAX_BYTES:
            return None, (f"the logo is too large (max {LOGO_MAX_BYTES // 1024} KiB) — "
                          "use a smaller PNG or JPG")
        sniffed = _sniff_image_mime(data)
        if sniffed is None:
            return None, "the logo must be a PNG or JPG image"
        use_mime = sniffed   # trust the magic bytes, never the client-supplied mime
        con = connect()
        try:
            con.execute(
                "INSERT INTO issuer_logo (tenant_id, mime, data, updated_at, updated_by) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET mime=excluded.mime, "
                "data=excluded.data, updated_at=excluded.updated_at, "
                "updated_by=excluded.updated_by",
                (tenant, use_mime, sqlite3.Binary(data),
                 datetime.datetime.utcnow().isoformat(timespec="seconds"), updated_by))
            con.commit()
        finally:
            con.close()
        return use_mime, ""
    except Exception as e:
        log.exception("set_issuer_logo failed")
        return None, f"could not store the logo ({str(e)[:80]})"


def get_issuer_logo():
    """The stored issuer logo as (mime, bytes), or (None, None) when none is set. Tenant-
    scoped. Read path — never raises -> (None, None)."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            row = con.execute("SELECT mime, data FROM issuer_logo WHERE 1=1" + frag,
                              tp).fetchone()
        finally:
            con.close()
        if not row or not row["data"]:
            return None, None
        return row["mime"], bytes(row["data"])
    except Exception as e:
        log.warning("get_issuer_logo failed: %s", e)
        return None, None


def logo_data_uri():
    """The issuer logo as an embeddable `data:` URI for the HTML→PDF header, or "" when no
    logo is set / it can't be read. NEVER raises (a bad logo degrades to no image)."""
    try:
        mime, data = get_issuer_logo()
        if not data or not mime:
            return ""
        import base64
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        log.warning("logo_data_uri failed, omitting logo: %s", e)
        return ""


# ============================================================ bill-to customers (CRUD)
def _cust_dict(row):
    d = dict(row)
    d["active"] = bool(d.get("active"))
    return d


def add_customer(name, *, reg_no="", vat_number="", address="", country="",
                 email="", payment_terms_days=None, notes="", iban="", created_by=None):
    """Create a bill-to customer. Returns (customer_dict, "") or (None, error)."""
    name = (name or "").strip()
    if not name:
        return None, "a customer name is required"
    try:
        ptd = int(payment_terms_days) if str(payment_terms_days or "").strip() else None
    except (TypeError, ValueError):
        ptd = None
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO bill_customers
                   (name, reg_no, vat_number, address, country, email,
                    payment_terms_days, notes, iban, created_by, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (name, (reg_no or "").strip(), (vat_number or "").strip(),
                 (address or "").strip(), (country or "").strip().upper(),
                 (email or "").strip(), ptd, (notes or "").strip(),
                 (iban or "").strip().replace(" ", "").upper(),
                 created_by, tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM bill_customers WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (_cust_dict(row) if row else None), ""
    except Exception as e:
        log.exception("add_customer failed for name=%r", name)
        return None, f"could not add customer ({str(e)[:80]})"


def update_customer(customer_id, **fields):
    """Update editable fields of a bill-to customer (tenant-scoped). Returns
    (customer_dict, "") or (None, error)."""
    allowed = ("name", "reg_no", "vat_number", "address", "country", "email",
               "payment_terms_days", "notes", "active", "iban")
    sets, params = [], []
    for k in allowed:
        if k in fields:
            v = fields[k]
            if k == "country" and v:
                v = str(v).strip().upper()
            elif k == "iban" and v:
                v = str(v).strip().replace(" ", "").upper()
            elif k == "payment_terms_days":
                try:
                    v = int(v) if str(v or "").strip() else None
                except (TypeError, ValueError):
                    v = None
            elif k == "active":
                v = 1 if v else 0
            elif isinstance(v, str):
                v = v.strip()
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return get_customer(customer_id), ""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            con.execute(f"UPDATE bill_customers SET {', '.join(sets)} WHERE id=?" + frag,
                        [*params, customer_id, *tp])
            con.commit()
        finally:
            con.close()
        return get_customer(customer_id), ""
    except Exception as e:
        log.exception("update_customer(%s) failed", customer_id)
        return None, f"could not update customer ({str(e)[:80]})"


def get_customer(customer_id):
    """One bill-to customer (tenant-scoped), or None. Never raises."""
    if not customer_id:
        return None
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM bill_customers WHERE id=?" + frag,
                              [customer_id, *tp]).fetchone()
        finally:
            con.close()
        return _cust_dict(row) if row else None
    except Exception as e:
        log.warning("get_customer(%s) failed: %s", customer_id, e)
        return None


def list_customers(include_inactive=True):
    """All bill-to customers for the current tenant (active first, then by name).
    Never raises -> []."""
    frag, tp = tenancy.scope_clause()
    where = "WHERE 1=1" + frag + ("" if include_inactive else " AND active=1")
    try:
        con = connect()
        try:
            rows = con.execute(
                f"SELECT * FROM bill_customers {where} ORDER BY active DESC, name",
                tp).fetchall()
        finally:
            con.close()
        return [_cust_dict(r) for r in rows]
    except Exception as e:
        log.warning("list_customers failed, returning empty: %s", e)
        return []


# ============================================================ VAT math (the heart)
def line_discount_amount(gross_net, discount_kind, discount_value):
    """The MONEY discount on ONE line given its PRE-discount net `gross_net`. A PERCENT kind
    takes `value`% of the gross net; an AMOUNT kind takes the fixed value. The result is
    quantized half-up and CLAMPED to [0, gross_net] (a discount can never make a line
    negative, nor be negative). Returns a Decimal. Pure, never raises."""
    base = money.D(gross_net or 0)
    kind = (discount_kind or "").strip().lower()
    try:
        val = money.D(discount_value or 0)
    except Exception:
        val = money.D(0)
    if val <= 0 or base <= 0:
        return money.D(0)
    if kind == DISC_PERCENT:
        disc = money.q2(base * val / money.D(100))
    elif kind == DISC_AMOUNT:
        disc = money.q2(val)
    else:
        return money.D(0)
    if disc < 0:
        disc = money.D(0)
    if disc > money.q2(base):
        disc = money.q2(base)
    return disc


def compute_line(quantity, unit_price_net, vat_rate, reverse_charge=False,
                 discount_kind="", discount_value=0):
    """Per-line money. The PRE-discount net = q2(qty*unit_price_net); the LINE DISCOUNT
    (percent of that net, or a fixed amount, clamped to [0, net]) is subtracted to give the
    DISCOUNTED line net, and VAT is computed on the DISCOUNTED net. Under reverse charge the
    effective rate is 0. Returns (line_net, line_vat, effective_rate, gross_net, discount)
    — line_net/line_vat are POST-discount; gross_net is the PRE-discount net; discount is the
    money discount. All floats (rate float). Pure, never raises."""
    qty = money.D(quantity)
    price = money.D(unit_price_net)
    rate = money.D(0) if reverse_charge else money.D(vat_rate)
    gross_net = money.q2(qty * price)
    disc = line_discount_amount(gross_net, discount_kind, discount_value)
    net = money.q2(gross_net - disc)
    if net < 0:
        net = money.D(0)
    vat = money.q2(net * rate)
    return float(net), float(vat), float(rate), float(gross_net), float(disc)


def document_discount_amount(net_total, disc_kind, disc_value):
    """The TOTAL document-level discount given the post-line-discount net total. PERCENT =
    value% of the net total; AMOUNT = the fixed value; clamped to [0, net_total]. Returns a
    Decimal (quantized half-up). Pure, never raises."""
    base = money.D(net_total or 0)
    kind = (disc_kind or "").strip().lower()
    try:
        val = money.D(disc_value or 0)
    except Exception:
        val = money.D(0)
    if val <= 0 or base <= 0:
        return money.D(0)
    if kind == DISC_PERCENT:
        disc = money.q2(base * val / money.D(100))
    elif kind == DISC_AMOUNT:
        disc = money.q2(val)
    else:
        return money.D(0)
    if disc < 0:
        disc = money.D(0)
    if disc > money.q2(base):
        disc = money.q2(base)
    return disc


def _allocate_document_discount(by_rate, total_discount):
    """Allocate a document-level discount across the per-rate buckets PROPORTIONALLY to each
    bucket's (post-line-discount) net, so VAT is recomputed on the POST-document-discount net
    per rate and the totals tie out EXACTLY (cents). `by_rate` is a list of
    {"rate","net","vat"} dicts; `total_discount` is a Decimal. Returns a NEW list of
    {"rate","net","vat","alloc"} (alloc = the discount removed from that bucket). The
    allocation is cents-exact: it floors each share and hands the rounding remainder to the
    LARGEST-net bucket so Σalloc == total_discount precisely. Pure."""
    disc = money.q2(total_discount or 0)
    rows = [dict(r) for r in by_rate]
    base_total = money.D(money.fsum([r["net"] for r in rows]))
    if disc <= 0 or base_total <= 0:
        for r in rows:
            r["alloc"] = 0.0
        return rows
    # cents-exact proportional split: floor each share, distribute the remainder.
    allocs = []
    for r in rows:
        share = (money.D(r["net"]) * disc) / base_total
        allocs.append(money.q2(share))
    # fix the rounding drift so Σalloc == disc exactly.
    drift = disc - money.D(money.fsum([float(a) for a in allocs]))
    if drift != 0 and rows:
        # hand the (small) drift to the largest-net bucket.
        idx = max(range(len(rows)), key=lambda i: money.D(rows[i]["net"]))
        allocs[idx] = money.q2(allocs[idx] + drift)
    out = []
    for r, a in zip(rows, allocs):
        a = money.q2(a)
        if a > money.q2(r["net"]):
            a = money.q2(r["net"])
        new_net = money.q2(money.D(r["net"]) - a)
        new_vat = money.q2(new_net * money.D(r["rate"]))
        out.append({"rate": r["rate"], "net": float(new_net), "vat": float(new_vat),
                    "alloc": float(a)})
    return out


def compute_totals(lines, reverse_charge=False, disc_kind="", disc_value=0):
    """Compute the per-VAT-rate breakdown and the grand totals from a list of line dicts
    (each with quantity, unit_price_net, vat_rate and optional discount_kind/discount_value).
    A DOCUMENT-level discount (disc_kind/disc_value) is allocated PER RATE proportionally and
    VAT is recomputed on the post-discount net. Returns a dict:

        {"lines": [{...line with line_net/line_vat/rate/gross_net/discount...}],
         "by_rate": [{"rate": 0.21, "net": .., "vat": .., "alloc": ..}, ...] (rate asc),
         "net_total": .., "vat_total": .., "gross_total": ..,
         "line_net_total": .. (post-line-discount, pre-document-discount),
         "doc_discount": .. (the applied document-discount amount)}

    All amounts are floats already quantized to cents (ROUND_HALF_UP), summed exactly via
    money.fsum so a per-rate subtotal equals the sum of its lines (post both discounts).
    Pure, never raises."""
    out_lines = []
    buckets = {}   # rate(float) -> {"net":[..], "vat":[..]}
    for ln in lines:
        net, vat, rate, gross_net, disc = compute_line(
            ln.get("quantity"), ln.get("unit_price_net"), ln.get("vat_rate"),
            reverse_charge=reverse_charge,
            discount_kind=ln.get("discount_kind"), discount_value=ln.get("discount_value"))
        row = dict(ln)
        row["line_net"], row["line_vat"], row["vat_rate"] = net, vat, rate
        row["gross_amount"], row["discount_amount"] = gross_net, disc
        out_lines.append(row)
        b = buckets.setdefault(rate, {"net": [], "vat": []})
        b["net"].append(net)
        b["vat"].append(vat)
    by_rate = []
    for rate in sorted(buckets):
        b = buckets[rate]
        by_rate.append({"rate": rate, "net": money.fsum(b["net"]),
                        "vat": money.fsum(b["vat"]), "alloc": 0.0})
    # post-line-discount net total (the base for a document-level discount).
    line_net_total = money.fsum([r["net"] for r in by_rate])
    doc_disc = document_discount_amount(line_net_total, disc_kind, disc_value)
    if doc_disc > 0:
        by_rate = _allocate_document_discount(by_rate, doc_disc)
    net_total = money.fsum([r["net"] for r in by_rate])
    vat_total = money.fsum([r["vat"] for r in by_rate])
    gross_total = money.fsum([net_total, vat_total])
    return {"lines": out_lines, "by_rate": by_rate,
            "net_total": net_total, "vat_total": vat_total, "gross_total": gross_total,
            "line_net_total": line_net_total, "doc_discount": float(money.q2(doc_disc))}


def eur_fx_rate(invoice):
    """The FX rate (FOREIGN units per 1 EUR) to convert this invoice's amounts to EUR.
    EUR -> 1.0. A foreign currency uses the invoice's stored `fx_rate` if the user
    supplied one; otherwise we look up an ECB rate for the issue/supply date (best-effort).
    Returns (rate, source) or (None, "") when no rate is available — we NEVER fabricate one.
    `source` is a short human label ('manual'/'ECB <date>')."""
    ccy = (invoice.get("currency") or DEFAULT_CURRENCY).strip().upper()
    if ccy == "EUR":
        return 1.0, "EUR"
    fx = invoice.get("fx_rate")
    if fx not in (None, "", 0):
        try:
            return float(fx), "manual"
        except (TypeError, ValueError):
            pass
    on_date = invoice.get("issue_date") or invoice.get("supply_date")
    try:
        import ecb_rates
        rate, asof = ecb_rates.rate_for(ccy, on_date)
        if rate:
            return float(rate), f"ECB {asof}" if asof else "ECB"
    except Exception as e:
        log.warning("eur_fx_rate ECB lookup failed for %s: %s", ccy, e)
    return None, ""


def vat_total_eur(invoice, vat_total=None):
    """The invoice VAT total expressed in EUR (LV/EU rule: a foreign-currency invoice must
    ALSO state the VAT amount in EUR). For an EUR invoice this is just the VAT total. For a
    foreign currency it is vat_total / fx_rate (fx = foreign-per-EUR), quantized HALF_UP.
    Returns (eur_vat, fx_rate, source) or (None, None, "") when no rate is available."""
    vt = invoice.get("vat_total") if vat_total is None else vat_total
    rate, source = eur_fx_rate(invoice)
    if rate is None:
        return None, None, ""
    if rate == 1.0 and source == "EUR":
        return money.f2(vt), 1.0, "EUR"
    return float(money.q2(money.D(vt) / money.D(rate))), rate, source


def derive_reverse_charge(issuer, customer):
    """Decide whether an invoice from `issuer` to `customer` is an intra-EU B2B
    REVERSE-CHARGE supply (the customer accounts for VAT; the supplier charges 0%).

    Heuristic (Phase 1, EXPLICIT not silent): both parties carry a VAT number, both
    countries are known, they DIFFER, and both are EU member states. The route layer
    surfaces this as a checkbox the user confirms — this only computes the SUGGESTED
    default. Returns a bool. Pure, never raises."""
    ic = _country_of(issuer)
    cc = _country_of(customer)
    if not ic or not cc or ic == cc:
        return False
    if not (issuer.get("vat_number") or "").strip():
        return False
    if not (customer.get("vat_number") or "").strip():
        return False
    return ic in EU_COUNTRIES and cc in EU_COUNTRIES


# EU member states (ISO-2). For the reverse-charge derivation default only.
EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "EL",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI",
    "ES", "SE",
}


def _country_of(party):
    """The ISO-2 country of an issuer/customer dict, best-effort. Falls back to the first
    two letters of a VAT number (EU VAT numbers are country-prefixed, e.g. DE123...)."""
    c = (party.get("country") or "").strip().upper()
    if c:
        return c[:2]
    vat = (party.get("vat_number") or "").strip().upper()
    if len(vat) >= 2 and vat[:2].isalpha():
        return vat[:2]
    return ""


REVERSE_CHARGE_NOTE = ("Reverse charge — VAT to be accounted for by the recipient "
                       "(Art. 196 Directive 2006/112/EC).")


# ============================================================ invoice composer (draft)
def _inv_dict(row):
    d = dict(row)
    d["reverse_charge"] = bool(d.get("reverse_charge"))
    d["simplified"] = bool(d.get("simplified"))
    return d


def create_draft(customer_id=None, *, currency=DEFAULT_CURRENCY, supply_date=None,
                 notes="", reverse_charge=None, created_by=None, doc_type=DOC_INVOICE):
    """Create a DRAFT invoice (no number, no snapshots yet). `reverse_charge` may be left
    None to auto-derive from issuer+customer at this moment (the user can still override
    later). `doc_type` chooses an ordinary 'invoice' or a non-legal 'proforma'/'quote'
    (a proforma/quote numbers from its own non-legal series and never consumes a legal
    number). Returns (invoice_dict, "") or (None, error)."""
    dt = (doc_type or DOC_INVOICE).strip().lower()
    if dt not in (DOC_INVOICE, DOC_PROFORMA, DOC_QUOTE):
        dt = DOC_INVOICE
    try:
        rc = reverse_charge
        if rc is None and customer_id:
            cust = get_customer(customer_id)
            if cust:
                rc = derive_reverse_charge(get_issuer(), cust)
        rc = 1 if rc else 0
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO invoices
                   (status, customer_id, currency, supply_date, notes,
                    reverse_charge, doc_type, created_by, tenant_id)
                   VALUES ('draft', ?,?,?,?,?,?,?,?)""",
                (customer_id, (currency or DEFAULT_CURRENCY).strip().upper(),
                 supply_date, (notes or "").strip(), rc, dt, created_by,
                 tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM invoices WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (_inv_dict(row) if row else None), ""
    except Exception as e:
        log.exception("create_draft failed")
        return None, f"could not create draft ({str(e)[:80]})"


def get_invoice(invoice_id):
    """An invoice header dict (tenant-scoped), or None. Never raises."""
    if not invoice_id:
        return None
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM invoices WHERE id=?" + frag,
                              [invoice_id, *tp]).fetchone()
        finally:
            con.close()
        return _inv_dict(row) if row else None
    except Exception as e:
        log.warning("get_invoice(%s) failed: %s", invoice_id, e)
        return None


def get_lines(invoice_id):
    """All lines of an invoice (by line_no), tenant-scoped. Never raises -> []."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT * FROM invoice_lines WHERE invoice_id=?" + frag
                + " ORDER BY line_no, id", [invoice_id, *tp]).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("get_lines(%s) failed: %s", invoice_id, e)
        return []


def list_invoices(status=None, year=None):
    """Invoice headers for the current tenant, newest first. Optional status / issue-year
    filters. Joins the customer name for display. Never raises -> []."""
    frag, tp = tenancy.scope_clause(column="i.tenant_id")
    where = ["1=1"]
    params = []
    if status:
        where.append("i.status=?")
        params.append(status)
    if year:
        # filter on the year of the issue date (only issued invoices are dated)
        where.append("substr(i.issue_date,1,4)=?")
        params.append(str(year))
    sql = ("SELECT i.*, c.name AS customer_name FROM invoices i "
           "LEFT JOIN bill_customers c ON c.id=i.customer_id "
           "WHERE " + " AND ".join(where) + frag
           + " ORDER BY (i.issued_at IS NULL) DESC, i.issued_at DESC, i.id DESC")
    try:
        con = connect()
        try:
            rows = con.execute(sql, [*params, *tp]).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            d = _inv_dict(r)
            out.append(d)
        return out
    except Exception as e:
        log.warning("list_invoices failed: %s", e)
        return []


def _assert_draft(con, invoice_id):
    """Return the invoice row if it exists, is tenant-visible and is a DRAFT; else
    (None, error). A legal invoice is immutable, so any edit to a non-draft is refused."""
    frag, tp = tenancy.scope_clause()
    row = con.execute("SELECT * FROM invoices WHERE id=?" + frag,
                      [invoice_id, *tp]).fetchone()
    if not row:
        return None, "invoice not found"
    if row["status"] != STATUS_DRAFT:
        return None, ("this invoice is issued and immutable — a legal invoice cannot be "
                      "edited (use a credit note / cancellation, a later phase)")
    return row, ""


def _norm_discount(kind, value):
    """Normalise a (kind, value) discount pair to a clean (kind, value-float). An empty/
    unknown kind or a non-positive value => (DISC_NONE, 0.0). Pure."""
    k = (kind or "").strip().lower()
    if k not in (DISC_PERCENT, DISC_AMOUNT):
        return DISC_NONE, 0.0
    try:
        v = float(money.D(value or 0))
    except Exception:
        v = 0.0
    if v <= 0:
        return DISC_NONE, 0.0
    return k, v


def add_line(invoice_id, *, description="", quantity=0, unit="", unit_price_net=0,
             vat_rate=0, goods_code="", discount_kind="", discount_value=0):
    """Append a line to a DRAFT invoice and re-total. The optional LINE DISCOUNT
    (discount_kind='percent'|'amount', discount_value) reduces the line net BEFORE VAT —
    `line_net`/`line_vat` are STORED POST-discount (so every read/report sees post-discount
    figures), `gross_amount` keeps the pre-discount net and `discount_amount` the money
    discount. Returns (invoice_dict, "") or (None, error). Refuses if the invoice is issued
    (immutable)."""
    dk, dv = _norm_discount(discount_kind, discount_value)
    try:
        con = connect()
        try:
            row, err = _assert_draft(con, invoice_id)
            if err:
                return None, err
            n = con.execute("SELECT COALESCE(MAX(line_no),0) FROM invoice_lines "
                            "WHERE invoice_id=?", (invoice_id,)).fetchone()[0]
            net, vat, rate, gross_net, disc = compute_line(
                quantity, unit_price_net, vat_rate,
                reverse_charge=bool(row["reverse_charge"]),
                discount_kind=dk, discount_value=dv)
            con.execute(
                """INSERT INTO invoice_lines
                   (invoice_id, line_no, description, quantity, unit, unit_price_net,
                    vat_rate, line_net, line_vat, goods_code, discount_kind,
                    discount_value, discount_amount, gross_amount, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (invoice_id, n + 1, (description or "").strip(), float(money.D(quantity)),
                 (unit or "").strip(), float(money.D(unit_price_net)), rate,
                 net, vat, (goods_code or "").strip(), dk, dv, disc, gross_net,
                 tenancy.write_tenant()))
            con.commit()
        finally:
            con.close()
        _retotal(invoice_id)
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("add_line(%s) failed", invoice_id)
        return None, f"could not add line ({str(e)[:80]})"


def remove_line(invoice_id, line_id):
    """Delete a line from a DRAFT invoice and re-total. Returns (invoice_dict, "") or
    (None, error)."""
    try:
        con = connect()
        try:
            row, err = _assert_draft(con, invoice_id)
            if err:
                return None, err
            con.execute("DELETE FROM invoice_lines WHERE id=? AND invoice_id=?",
                        (line_id, invoice_id))
            con.commit()
        finally:
            con.close()
        _retotal(invoice_id)
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("remove_line(%s,%s) failed", invoice_id, line_id)
        return None, f"could not remove line ({str(e)[:80]})"


def replace_lines(invoice_id, lines):
    """ATOMICALLY replace ALL lines of a DRAFT invoice with `lines` (a list of dicts), then
    re-total — the single-round-trip counterpart of the per-line add_line/remove_line forms.

    The SERVER stays the source of truth: every figure (line_net / line_vat / the per-rate
    breakdown / the header totals) is recomputed here via the SAME compute_line / _retotal
    path add_line uses; the client's preview math is never trusted for a stored figure. Each
    dict may carry description, quantity, unit, unit_price_net, vat_rate (a FRACTION),
    goods_code, discount_kind ('percent'|'amount'|''), discount_value. Blank/whitespace-only
    rows (no description AND zero qty AND zero price) are dropped. Refuses on a non-draft
    (immutable). Returns (invoice_dict, "") or (None, error)."""
    try:
        con = connect()
        try:
            row, err = _assert_draft(con, invoice_id)
            if err:
                return None, err
            rc = bool(row["reverse_charge"])
            # Drop the old lines wholesale, then re-insert the supplied set in order. The
            # delete + inserts share one transaction so a half-written replace can't persist.
            con.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (invoice_id,))
            line_no = 0
            tenant = tenancy.write_tenant()
            for ln in (lines or []):
                desc = (str(ln.get("description") or "")).strip()
                try:
                    qty = float(money.D(ln.get("quantity") or 0))
                except Exception:
                    qty = 0.0
                try:
                    price = float(money.D(ln.get("unit_price_net") or 0))
                except Exception:
                    price = 0.0
                # skip a wholly-empty row (the editor leaves a blank trailing row)
                if not desc and qty == 0 and price == 0:
                    continue
                dk, dv = _norm_discount(ln.get("discount_kind"), ln.get("discount_value"))
                try:
                    rate_in = float(ln.get("vat_rate") or 0)
                except (TypeError, ValueError):
                    rate_in = 0.0
                net, vat, rate, gross_net, disc = compute_line(
                    qty, price, rate_in, reverse_charge=rc,
                    discount_kind=dk, discount_value=dv)
                line_no += 1
                con.execute(
                    """INSERT INTO invoice_lines
                       (invoice_id, line_no, description, quantity, unit, unit_price_net,
                        vat_rate, line_net, line_vat, goods_code, discount_kind,
                        discount_value, discount_amount, gross_amount, tenant_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (invoice_id, line_no, desc, qty,
                     (str(ln.get("unit") or "")).strip(), price, rate,
                     net, vat, (str(ln.get("goods_code") or "")).strip(), dk, dv,
                     disc, gross_net, tenant))
            con.commit()
        finally:
            con.close()
        _retotal(invoice_id)
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("replace_lines(%s) failed", invoice_id)
        return None, f"could not save the lines ({str(e)[:80]})"


def set_line_discount(invoice_id, line_id, discount_kind="", discount_value=0):
    """Set/clear the LINE discount on a DRAFT invoice line and re-total. The line VAT is
    recomputed on the post-discount net. Returns (invoice_dict, "") or (None, error).
    Refuses on an issued invoice (immutable)."""
    dk, dv = _norm_discount(discount_kind, discount_value)
    try:
        con = connect()
        try:
            row, err = _assert_draft(con, invoice_id)
            if err:
                return None, err
            ln = con.execute("SELECT quantity, unit_price_net, vat_rate FROM invoice_lines "
                             "WHERE id=? AND invoice_id=?", (line_id, invoice_id)).fetchone()
            if not ln:
                return None, "line not found"
            net, vat, rate, gross_net, disc = compute_line(
                ln["quantity"], ln["unit_price_net"], ln["vat_rate"],
                reverse_charge=bool(row["reverse_charge"]),
                discount_kind=dk, discount_value=dv)
            con.execute(
                "UPDATE invoice_lines SET discount_kind=?, discount_value=?, "
                "discount_amount=?, gross_amount=?, line_net=?, line_vat=? "
                "WHERE id=? AND invoice_id=?",
                (dk, dv, disc, gross_net, net, vat, line_id, invoice_id))
            con.commit()
        finally:
            con.close()
        _retotal(invoice_id)
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("set_line_discount(%s,%s) failed", invoice_id, line_id)
        return None, f"could not set the line discount ({str(e)[:80]})"


def set_document_discount(invoice_id, discount_kind="", discount_value=0):
    """Set/clear the INVOICE-level (document) discount on a DRAFT invoice. Convenience
    wrapper over set_invoice_fields. Returns (invoice_dict, "") or (None, error)."""
    dk, dv = _norm_discount(discount_kind, discount_value)
    return set_invoice_fields(invoice_id, disc_kind=dk, disc_value=dv)


def set_invoice_fields(invoice_id, **fields):
    """Edit header fields of a DRAFT invoice (customer_id, currency, supply_date, notes,
    reverse_charge). Re-totals if reverse_charge changes (it flips every line's VAT).
    Returns (invoice_dict, "") or (None, error)."""
    allowed = ("customer_id", "currency", "supply_date", "notes", "reverse_charge",
               "simplified", "fx_rate", "disc_kind", "disc_value")
    sets, params = [], []
    rc_changed = False
    for k in allowed:
        if k in fields:
            v = fields[k]
            if k == "currency" and v:
                v = str(v).strip().upper()
            elif k == "reverse_charge":
                v = 1 if v else 0
                rc_changed = True
            elif k == "disc_kind":
                v = (str(v or "").strip().lower()
                     if str(v or "").strip().lower() in (DISC_PERCENT, DISC_AMOUNT)
                     else DISC_NONE)
            elif k == "disc_value":
                try:
                    v = float(money.D(v or 0))
                except Exception:
                    v = 0.0
                if v < 0:
                    v = 0.0
            elif k == "simplified":
                v = 1 if v else 0
            elif k == "fx_rate":
                try:
                    v = float(v) if str(v if v is not None else "").strip() else None
                except (TypeError, ValueError):
                    v = None
            elif isinstance(v, str):
                v = v.strip()
            sets.append(f"{k}=?")
            params.append(v)
    try:
        con = connect()
        try:
            row, err = _assert_draft(con, invoice_id)
            if err:
                return None, err
            if sets:
                con.execute(f"UPDATE invoices SET {', '.join(sets)} WHERE id=?",
                            [*params, invoice_id])
                con.commit()
                if rc_changed:
                    # reverse charge flips every line between its rate and 0% — recompute,
                    # PRESERVING each line's discount (the discounted net is what's taxed).
                    rc = bool(fields.get("reverse_charge"))
                    for ln in con.execute(
                            "SELECT id, quantity, unit_price_net, vat_rate, discount_kind, "
                            "discount_value FROM invoice_lines WHERE invoice_id=?",
                            (invoice_id,)).fetchall():
                        # When turning RC OFF we cannot recover the original rate if it was
                        # stored as 0; callers re-enter rates. Here we only zero-out on ON.
                        net, vat, rate, gross_net, disc = compute_line(
                            ln["quantity"], ln["unit_price_net"], ln["vat_rate"],
                            reverse_charge=rc, discount_kind=ln["discount_kind"],
                            discount_value=ln["discount_value"])
                        con.execute("UPDATE invoice_lines SET vat_rate=?, line_net=?, "
                                    "line_vat=?, discount_amount=?, gross_amount=? "
                                    "WHERE id=?",
                                    (rate, net, vat, disc, gross_net, ln["id"]))
                    con.commit()
        finally:
            con.close()
        _retotal(invoice_id)
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("set_invoice_fields(%s) failed", invoice_id)
        return None, f"could not update invoice ({str(e)[:80]})"


def _retotal(invoice_id):
    """Recompute and persist the header totals from the stored lines (cents-exact). The
    per-line `line_net`/`line_vat` are already POST-line-discount; the DOCUMENT discount
    (disc_kind/disc_value on the header) is allocated per VAT rate here, so the stored header
    net/VAT/gross are POST both discounts. `disc_amount` snapshots the applied document
    discount (the EN-16931 BG-20 / BT-107 allowance amount)."""
    con = connect()
    try:
        hdr = con.execute(
            "SELECT disc_kind, disc_value FROM invoices WHERE id=?",
            (invoice_id,)).fetchone()
        disc_kind = (hdr["disc_kind"] if hdr else "") or ""
        disc_value = (hdr["disc_value"] if hdr else 0) or 0
        rows = con.execute(
            "SELECT line_net, line_vat, vat_rate FROM invoice_lines WHERE invoice_id=?",
            (invoice_id,)).fetchall()
        # group the post-line-discount nets/vats by rate, then allocate the document discount.
        buckets = {}
        for r in rows:
            rate = float(r["vat_rate"] or 0)
            b = buckets.setdefault(rate, {"net": [], "vat": []})
            b["net"].append(r["line_net"])
            b["vat"].append(r["line_vat"])
        by_rate = [{"rate": rate, "net": money.fsum(buckets[rate]["net"]),
                    "vat": money.fsum(buckets[rate]["vat"]), "alloc": 0.0}
                   for rate in sorted(buckets)]
        line_net_total = money.fsum([r["net"] for r in by_rate])
        doc_disc = document_discount_amount(line_net_total, disc_kind, disc_value)
        if doc_disc > 0:
            by_rate = _allocate_document_discount(by_rate, doc_disc)
        net = money.fsum([r["net"] for r in by_rate])
        vat = money.fsum([r["vat"] for r in by_rate])
        gross = money.fsum([net, vat])
        con.execute("UPDATE invoices SET net_total=?, vat_total=?, gross_total=?, "
                    "disc_amount=? WHERE id=?",
                    (net, vat, gross, float(money.q2(doc_disc)), invoice_id))
        con.commit()
    finally:
        con.close()


# ============================================================ gap-free numbering
def _format_number(fmt, series, year, seq):
    """Render a number from a format string. Tolerant of a bad/blank format (falls back
    to the default). The padded sequence is exposed as both {seq} and {seq:06d}."""
    fmt = (fmt or DEFAULT_NUMBER_FORMAT).strip() or DEFAULT_NUMBER_FORMAT
    try:
        return fmt.format(series=series, year=year, seq=seq)
    except (KeyError, ValueError, IndexError):
        log.warning("bad invoice number_format %r, using default", fmt)
        return DEFAULT_NUMBER_FORMAT.format(series=series, year=year, seq=seq)


def next_number(series, year, *, number_format=None, con=None):
    """Atomically assign the next GAP-FREE sequence for (series, year, current-tenant) and
    return (formatted_number, seq).

    CONCURRENCY: runs inside a single IMMEDIATE (write-locked) transaction — the read of
    `last_no` and its +1 bump are serialised by SQLite's write lock, so two concurrent
    issues can never read the same `last_no` (the loser waits up to busy_timeout, then
    proceeds on the bumped value). A number is therefore unique AND gap-free per series/year.

    If `con` is given the bump runs on it (used by issue() so the number assignment and the
    invoice status flip commit ATOMICALLY together); otherwise we open and own a connection."""
    tenant = tenancy.queue_tenant()
    own = con is None
    if own:
        con = connect()
    try:
        # BEGIN IMMEDIATE takes the write lock up front, serialising concurrent callers.
        if own:
            con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT OR IGNORE INTO invoice_counters (series, year, last_no, tenant_id) "
            "VALUES (?,?,0,?)", (series, year, tenant))
        con.execute(
            "UPDATE invoice_counters SET last_no = last_no + 1 "
            "WHERE series=? AND year=? AND tenant_id=?", (series, year, tenant))
        seq = con.execute(
            "SELECT last_no FROM invoice_counters WHERE series=? AND year=? AND tenant_id=?",
            (series, year, tenant)).fetchone()[0]
        if own:
            con.commit()
        return _format_number(number_format, series, year, seq), seq
    finally:
        if own:
            con.close()


# ============================================================ issue (lock + immutable)
def validate_for_issue(invoice_id):
    """Return "" if the invoice is complete enough to issue, else a human error.
    Requires: an issuer profile (name/address/VAT), a chosen customer with the mandatory
    fields, at least one line, and (unless reverse charge) the math present. Pure read."""
    inv = get_invoice(invoice_id)
    if not inv:
        return "invoice not found"
    if inv["status"] != STATUS_DRAFT:
        return "invoice is already issued"
    if not issuer_complete():
        return ("the issuer profile is incomplete — set the legal name, address and VAT "
                "number in the issuer settings before issuing")
    cust = get_customer(inv["customer_id"]) if inv["customer_id"] else None
    if not cust:
        return "choose a customer to bill before issuing"
    # SIMPLIFIED INVOICE (Art. 238/226b): when the flag is on AND gross <= the ceiling the
    # full customer-detail requirement is relaxed (a name still helps but address/VAT may be
    # omitted). Reverse charge is incompatible with a simplified invoice (it needs the
    # customer VAT number), so its check below still applies.
    flag = bool(inv.get("simplified"))
    gross = float(inv.get("gross_total") or 0)
    if flag and gross > SIMPLIFIED_GROSS_CEILING_EUR:
        return (f"simplified invoices are only allowed up to EUR {SIMPLIFIED_GROSS_CEILING_EUR:.0f} "
                "gross — turn the simplified flag off or reduce the amount")
    simplified = flag and gross <= SIMPLIFIED_GROSS_CEILING_EUR
    if not simplified:
        if not (cust.get("name") or "").strip() or not (cust.get("address") or "").strip():
            return "the customer is missing a name or address (mandatory on a VAT invoice)"
    if inv["reverse_charge"] and not (cust.get("vat_number") or "").strip():
        return ("reverse charge requires the customer's VAT number (intra-EU B2B); add it "
                "or turn reverse charge off")
    lines = get_lines(invoice_id)
    if not lines:
        return "add at least one line before issuing"
    # VAT-IN-EUR (LV/EU): a foreign-currency invoice must also state the VAT total in EUR.
    # We need a reproducible FX rate — the user's supplied rate or a cached ECB rate. We
    # never fabricate one, so refuse to issue until a rate is available.
    if (inv.get("currency") or DEFAULT_CURRENCY).strip().upper() != "EUR":
        rate, _src = eur_fx_rate(inv)
        if not rate:
            return ("this invoice is in a foreign currency — supply an FX rate (foreign per "
                    "1 EUR) so the VAT total can also be stated in EUR (LV/EU rule), or set "
                    "the currency to EUR")
    return ""


def _snapshot_issuer(issuer):
    import json
    return json.dumps({k: issuer.get(k, "") for k in ISSUER_KEYS}, ensure_ascii=False)


def _snapshot_customer(cust):
    import json
    keep = ("id", "name", "reg_no", "vat_number", "address", "country", "email",
            "payment_terms_days", "iban")
    return json.dumps({k: cust.get(k) for k in keep}, ensure_ascii=False)


def issue(invoice_id, *, issued_by=None, issue_date=None):
    """Issue a DRAFT invoice: validate completeness, assign the GAP-FREE number, SNAPSHOT
    the issuer + customer, set issue/supply/due dates, flip status to `issued` and make it
    IMMUTABLE. The number assignment and the status flip commit ATOMICALLY in one
    write-locked transaction. Returns (invoice_dict, "") or (None, error)."""
    err = validate_for_issue(invoice_id)
    if err:
        return None, err
    issuer = get_issuer()
    # Each document type numbers from its OWN gap-free series so the counters never collide:
    #   - CREDIT NOTE -> credit_series  (default 'KR')
    #   - PROFORMA    -> proforma_series (default 'PROF') — a NON-LEGAL series; a proforma
    #     never takes a number from the legal invoice series
    #   - QUOTE       -> quote_series    (default 'PIED')
    #   - INVOICE     -> the legal invoice series
    # The SAME {series}-{year}-{seq} format renders them all.
    _doc = (get_invoice(invoice_id) or {}).get("doc_type") or DOC_INVOICE
    if _doc == DOC_CREDIT_NOTE:
        series = (issuer.get("credit_series") or DEFAULT_CREDIT_SERIES).strip() or DEFAULT_CREDIT_SERIES
    elif _doc == DOC_PROFORMA:
        series = (issuer.get("proforma_series") or DEFAULT_PROFORMA_SERIES).strip() or DEFAULT_PROFORMA_SERIES
    elif _doc == DOC_QUOTE:
        series = (issuer.get("quote_series") or DEFAULT_QUOTE_SERIES).strip() or DEFAULT_QUOTE_SERIES
    else:
        series = (issuer.get("series") or DEFAULT_SERIES).strip() or DEFAULT_SERIES
    number_format = issuer.get("number_format") or DEFAULT_NUMBER_FORMAT
    try:
        terms = int(issuer.get("payment_terms_days") or DEFAULT_PAYMENT_TERMS_DAYS)
    except (TypeError, ValueError):
        terms = DEFAULT_PAYMENT_TERMS_DAYS
    today = issue_date or datetime.date.today().isoformat()
    try:
        year = int(today[:4])
    except (TypeError, ValueError):
        year = datetime.date.today().year
    try:
        idt = datetime.date.fromisoformat(today)
    except (TypeError, ValueError):
        idt = datetime.date.today()
        today = idt.isoformat()
    cust = get_customer(get_invoice(invoice_id)["customer_id"])
    # the customer's own terms override the issuer default if set
    cterms = cust.get("payment_terms_days")
    if cterms is not None and str(cterms).strip() != "":
        try:
            terms = int(cterms)
        except (TypeError, ValueError):
            pass
    due = (idt + datetime.timedelta(days=terms)).isoformat()
    # 5-YEAR RETENTION marker (record-keeping). Best-effort exact-year arithmetic; a
    # 29-Feb issue date falls back to 28-Feb (a non-leap target year has no 29 Feb).
    try:
        retain_until = idt.replace(year=idt.year + RETENTION_YEARS).isoformat()
    except ValueError:
        retain_until = idt.replace(year=idt.year + RETENTION_YEARS,
                                   day=28).isoformat()
    try:
        con = connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            # re-check the status UNDER the write lock (no double-issue race)
            frag, tp = tenancy.scope_clause()
            row = con.execute("SELECT status, supply_date FROM invoices WHERE id=?" + frag,
                              [invoice_id, *tp]).fetchone()
            if not row:
                con.rollback()
                return None, "invoice not found"
            if row["status"] != STATUS_DRAFT:
                con.rollback()
                return None, "invoice is already issued"
            number, seq = next_number(series, year, number_format=number_format, con=con)
            con.execute(
                """UPDATE invoices
                   SET number=?, series=?, issue_date=?, due_date=?,
                       supply_date=COALESCE(supply_date, ?),
                       issuer_snapshot=?, customer_snapshot=?, retain_until=?,
                       status='issued', issued_at=?, issued_by=?
                   WHERE id=?""",
                (number, series, today, due, today,
                 _snapshot_issuer(issuer), _snapshot_customer(cust), retain_until,
                 datetime.datetime.utcnow().isoformat(timespec="seconds"),
                 issued_by, invoice_id))
            con.commit()
        finally:
            con.close()
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("issue(%s) failed", invoice_id)
        return None, f"could not issue invoice ({str(e)[:80]})"


# ============================================================ PHASE 4: credit notes
# A CREDIT NOTE (kreditrēķins) is the LEGAL way to reverse or correct an ISSUED invoice —
# an issued invoice stays IMMUTABLE, so we never edit it; instead we issue a credit note
# that references it. It is a FIRST-CLASS document reusing the invoice machinery: same
# tables, same compose/issue/PDF/e-invoice path, distinguished only by doc_type =
# 'credit_note', a corrects_invoice_id pointing at the original, and its own number SERIES.
#
# EFFECT ON THE ORIGINAL is DERIVED, never a mutation of the original's stored figures:
# credited_total() sums the ISSUED credit notes against an invoice (gross), and
# outstanding()/display_status()/the AR view subtract that — so a FULLY-credited invoice
# reads `cancelled` with a zero receivable, and a partial credit reduces the receivable by
# exactly the credited amount, all without touching the filed original.
#
# LV NOTE flagged for the product owner: in Latvian practice a FULL reversal of an issued
# invoice is conventionally a credit note (kreditrēķins) as modelled here; some
# jurisdictions distinguish a "cancellation" document. We model BOTH full and partial as a
# credit note (the EN-16931 type 381) — confirm this matches your accountant's convention.

def credited_total(original_invoice_id):
    """The total (gross) credited against an ORIGINAL invoice by ISSUED credit notes,
    cents-exact (money.fsum). Drafts do not count (they carry no legal amount). Read-only;
    never raises -> 0.0."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT gross_total FROM invoices WHERE corrects_invoice_id=? "
                "AND doc_type=? AND status<>'draft'" + frag,
                [original_invoice_id, DOC_CREDIT_NOTE, *tp]).fetchall()
        finally:
            con.close()
        return money.fsum([r["gross_total"] for r in rows])
    except Exception as e:
        log.warning("credited_total(%s) failed: %s", original_invoice_id, e)
        return 0.0


def credit_notes_for(original_invoice_id):
    """All credit notes (draft + issued) raised against an ORIGINAL invoice, newest first,
    tenant-scoped. Read-only; never raises -> []."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT * FROM invoices WHERE corrects_invoice_id=? AND doc_type=?" + frag
                + " ORDER BY (issued_at IS NULL) DESC, issued_at DESC, id DESC",
                [original_invoice_id, DOC_CREDIT_NOTE, *tp]).fetchall()
        finally:
            con.close()
        return [_inv_dict(r) for r in rows]
    except Exception as e:
        log.warning("credit_notes_for(%s) failed: %s", original_invoice_id, e)
        return []


def is_fully_credited(invoice):
    """True iff an invoice has been credited (by issued credit notes) for its full gross
    (within a cent, money.q2). Pure-ish read; never raises -> False."""
    try:
        inv = invoice if isinstance(invoice, dict) else get_invoice(invoice)
        if not inv or inv.get("doc_type") == DOC_CREDIT_NOTE:
            return False
        gross = money.q2(inv.get("gross_total") or 0)
        if gross <= 0:
            return False
        return money.q2(credited_total(inv["id"])) >= gross
    except Exception as e:
        log.warning("is_fully_credited() failed: %s", e)
        return False


def create_credit_note(original_invoice_id, *, mode="full", lines=None, reason="",
                       created_by=None):
    """Create a DRAFT credit note that reverses or corrects an ISSUED invoice.

    `mode='full'` mirrors EVERY line of the original (a full reversal). `mode='partial'`
    takes `lines` = a list of dicts to credit, each
        {"line_no": <original line_no>, "quantity": q, "unit_price_net": p}
    (or a free line with description/unit/vat_rate) — each credited amount must be ≤ the
    original's corresponding line amount, and the total credited (existing issued credit
    notes + this one) must not exceed the original gross. Same VAT rates / reverse-charge
    as the original. Returns (credit_note_dict, "") or (None, error).

    The credit note is a DRAFT until issue() — which assigns its number from the CREDIT
    series (not the invoice series) and makes it immutable. It snapshots issuer + customer
    at issue exactly like an invoice."""
    orig = get_invoice(original_invoice_id)
    if not orig:
        return None, "original invoice not found"
    if orig.get("doc_type") == DOC_CREDIT_NOTE:
        return None, "cannot credit a credit note"
    if orig.get("status") == STATUS_DRAFT or not orig.get("number"):
        return None, "only an issued invoice can be credited"
    orig_lines = get_lines(original_invoice_id)
    rc = bool(orig.get("reverse_charge"))

    # Resolve the lines to credit (positive amounts, the PDF/e-invoice present them as a
    # credit per convention). FULL = mirror every original line; PARTIAL = the supplied set.
    if mode == "full":
        to_credit = [dict(quantity=ln.get("quantity"), unit=ln.get("unit"),
                          unit_price_net=ln.get("unit_price_net"),
                          vat_rate=ln.get("vat_rate"),
                          description=ln.get("description"),
                          goods_code=ln.get("goods_code"))
                     for ln in orig_lines]
        if not to_credit:
            return None, "the original invoice has no lines to credit"
    else:
        if not lines:
            return None, "choose at least one line to credit"
        by_no = {int(ln["line_no"]): ln for ln in orig_lines if ln.get("line_no") is not None}
        to_credit = []
        for req in lines:
            try:
                qty = money.D(req.get("quantity") or 0)
                price = money.D(req.get("unit_price_net") or 0)
            except Exception:
                return None, "invalid credit-line amount"
            if qty <= 0 or price < 0:
                continue
            src = None
            ln_no = req.get("line_no")
            if ln_no is not None and str(ln_no).strip() != "":
                src = by_no.get(int(ln_no))
                if src is None:
                    return None, "credit line does not match an original line"
                # each credited amount must be ≤ the original line's net amount
                credit_net = money.q2(qty * price)
                orig_net = money.q2(src.get("line_net") or 0)
                if credit_net > orig_net:
                    return None, ("a credit line cannot exceed the original line amount "
                                  f"(line {ln_no})")
            desc = req.get("description") or (src.get("description") if src else "")
            unit = req.get("unit") or (src.get("unit") if src else "")
            rate = req.get("vat_rate")
            if rate is None:
                rate = (src.get("vat_rate") if src else 0)
            to_credit.append(dict(quantity=float(qty), unit=unit,
                                  unit_price_net=float(price), vat_rate=rate,
                                  description=desc,
                                  goods_code=(src.get("goods_code") if src else "")))
        if not to_credit:
            return None, "choose at least one line to credit"

    # over-credit guard: existing issued credits + this new credit ≤ the original gross.
    this_gross = compute_totals(to_credit, reverse_charge=rc)["gross_total"]
    already = credited_total(original_invoice_id)
    if money.q2(money.D(already) + money.D(this_gross)) > money.q2(orig.get("gross_total") or 0):
        return None, "the amount to credit exceeds the original invoice"

    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO invoices
                   (status, customer_id, currency, supply_date, notes,
                    reverse_charge, simplified, fx_rate, doc_type, corrects_invoice_id,
                    created_by, tenant_id)
                   VALUES ('draft', ?,?,?,?,?,?,?,?,?,?,?)""",
                (orig.get("customer_id"), orig.get("currency") or DEFAULT_CURRENCY,
                 orig.get("supply_date"),
                 (reason or "").strip(), (1 if rc else 0),
                 (1 if orig.get("simplified") else 0), orig.get("fx_rate"),
                 DOC_CREDIT_NOTE, original_invoice_id, created_by, tenancy.write_tenant()))
            cn_id = cur.lastrowid
            for i, ln in enumerate(to_credit, 1):
                # a credit line mirrors the already-(line-)discounted original net (no further
                # line discount on the credit note itself).
                net, vat, rate, gross_net, disc = compute_line(
                    ln.get("quantity"), ln.get("unit_price_net"),
                    ln.get("vat_rate"), reverse_charge=rc)
                con.execute(
                    """INSERT INTO invoice_lines
                       (invoice_id, line_no, description, quantity, unit, unit_price_net,
                        vat_rate, line_net, line_vat, goods_code, gross_amount, tenant_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cn_id, i, (ln.get("description") or "").strip(),
                     float(money.D(ln.get("quantity"))), (ln.get("unit") or "").strip(),
                     float(money.D(ln.get("unit_price_net"))), rate, net, vat,
                     (ln.get("goods_code") or "").strip(), gross_net, tenancy.write_tenant()))
            con.commit()
        finally:
            con.close()
        _retotal(cn_id)
        return get_invoice(cn_id), ""
    except Exception as e:
        log.exception("create_credit_note(%s) failed", original_invoice_id)
        return None, f"could not create credit note ({str(e)[:80]})"


# ============================================================ PHASE 7: proforma / quote
# A PROFORMA invoice / a QUOTE is NOT a tax invoice. It numbers from its OWN non-legal series
# (it NEVER takes a number from the legal invoice series), triggers NO output VAT, and is
# EXCLUDED everywhere a real invoice is counted (the VAT-output report, AR/aging, revenue,
# bank-matching — all filter doc_type='invoice'). It IS clearly labelled on the PDF and CAN
# be CONVERTED to a real DRAFT invoice (copying customer + lines + discounts), which then
# issues normally and gets the legal invoice number. The conversion links the new invoice
# back to the source proforma/quote (converted_from_id) for traceability.

def convert_to_invoice(proforma_id, *, created_by=None):
    """Convert a PROFORMA/QUOTE into a NEW DRAFT ordinary invoice, copying the customer,
    currency, reverse-charge/simplified/fx flags, the document discount, and EVERY line with
    its line discount. The new invoice is a DRAFT (doc_type='invoice') linked back to the
    source via converted_from_id; the caller then issues it normally to assign the legal
    number. Returns (invoice_dict, "") or (None, error).

    A proforma/quote can be converted whether it is still a DRAFT or already issued (an issued
    proforma is a non-legal numbered offer; converting it produces the real invoice). It is
    NOT consumed — it stays as the historical offer. An ordinary invoice / credit note cannot
    be converted (it is already, or cannot become, a real invoice)."""
    src = get_invoice(proforma_id)
    if not src:
        return None, "document not found"
    if src.get("doc_type") not in (DOC_PROFORMA, DOC_QUOTE):
        return None, "only a proforma invoice or a quote can be converted to an invoice"
    src_lines = get_lines(proforma_id)
    if not src_lines:
        return None, "the proforma/quote has no lines to convert"
    rc = bool(src.get("reverse_charge"))
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO invoices
                   (status, customer_id, currency, supply_date, notes,
                    reverse_charge, simplified, fx_rate, doc_type, disc_kind, disc_value,
                    converted_from_id, created_by, tenant_id)
                   VALUES ('draft', ?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (src.get("customer_id"), src.get("currency") or DEFAULT_CURRENCY,
                 src.get("supply_date"), (src.get("notes") or "").strip(),
                 (1 if rc else 0), (1 if src.get("simplified") else 0),
                 src.get("fx_rate"), DOC_INVOICE,
                 (src.get("disc_kind") or ""), (src.get("disc_value") or 0),
                 proforma_id, created_by, tenancy.write_tenant()))
            new_id = cur.lastrowid
            for i, ln in enumerate(src_lines, 1):
                dk, dv = _norm_discount(ln.get("discount_kind"), ln.get("discount_value"))
                net, vat, rate, gross_net, disc = compute_line(
                    ln.get("quantity"), ln.get("unit_price_net"), ln.get("vat_rate"),
                    reverse_charge=rc, discount_kind=dk, discount_value=dv)
                con.execute(
                    """INSERT INTO invoice_lines
                       (invoice_id, line_no, description, quantity, unit, unit_price_net,
                        vat_rate, line_net, line_vat, goods_code, discount_kind,
                        discount_value, discount_amount, gross_amount, tenant_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_id, i, (ln.get("description") or "").strip(),
                     float(money.D(ln.get("quantity"))), (ln.get("unit") or "").strip(),
                     float(money.D(ln.get("unit_price_net"))), rate, net, vat,
                     (ln.get("goods_code") or "").strip(), dk, dv, disc, gross_net,
                     tenancy.write_tenant()))
            con.commit()
        finally:
            con.close()
        _retotal(new_id)
        return get_invoice(new_id), ""
    except Exception as e:
        log.exception("convert_to_invoice(%s) failed", proforma_id)
        return None, f"could not convert to invoice ({str(e)[:80]})"


# ============================================================ PHASE 3: payments + status
# PAYMENT LEDGER + STATUS LIFECYCLE. A payment is APPENDED to invoice_payments (manual or
# bank-sourced); the paid total is recomputed (money.fsum, cents-exact) and the stored
# status moves draft -> issued -> sent -> partially_paid -> paid. OVERDUE is NEVER stored:
# it is DERIVED at read time from the due date + the paid total (see is_overdue /
# display_status), so it is always correct without a job racing the column.
#
# CURRENCY NOTE: a payment is assumed to be in the invoice's own currency (the amount is
# compared directly to the gross total). A foreign-currency settlement-in-EUR is out of
# scope for this phase — the operator records the amount in the invoice currency. This is
# flagged, not silently mishandled.

def _paid_total(con, invoice_id):
    """The sum of all recorded payments for an invoice (cents-exact via money.fsum),
    tenant-scoped. Runs on an OPEN connection (so it can share the write transaction)."""
    frag, tp = tenancy.scope_clause()
    rows = con.execute(
        "SELECT amount FROM invoice_payments WHERE invoice_id=?" + frag,
        [invoice_id, *tp]).fetchall()
    return money.fsum([r["amount"] for r in rows])


def paid_total(invoice_id):
    """Public: the total paid against an invoice (EUR/local, cents-exact). Never raises."""
    try:
        con = connect()
        try:
            return _paid_total(con, invoice_id)
        finally:
            con.close()
    except Exception as e:
        log.warning("paid_total(%s) failed: %s", invoice_id, e)
        return 0.0


def outstanding(invoice):
    """gross_total - paid_total - credited_total for an invoice DICT (or id). The amount
    still owed, never below zero, cents-exact. A credit note (issued against this invoice)
    REDUCES the receivable (a full credit zeroes it) — the credited amount is DERIVED, not
    a mutation of the original. Pure-ish read; never raises -> the gross (worst case)."""
    try:
        inv = invoice if isinstance(invoice, dict) else get_invoice(invoice)
        if not inv:
            return 0.0
        gross = money.q2(inv.get("gross_total") or 0)
        paid = money.q2(paid_total(inv["id"]))
        credited = (money.q2(credited_total(inv["id"]))
                    if inv.get("doc_type") != DOC_CREDIT_NOTE else money.q2(0))
        rem = gross - paid - credited
        return float(rem) if rem > 0 else 0.0
    except Exception as e:
        log.warning("outstanding() failed: %s", e)
        return 0.0


def _status_for_paid(gross, paid):
    """Map (gross, paid) to a STORED status: `paid` when paid >= gross (within a cent via
    money.q2 — a tiny overpay/rounding still settles), `partially_paid` when 0 < paid <
    gross, else `issued` (nothing paid). Pure."""
    g = money.q2(gross or 0)
    p = money.q2(paid or 0)
    if p >= g and g > 0:
        return STATUS_PAID
    if p > 0:
        return STATUS_PARTIALLY_PAID
    return STATUS_ISSUED


def record_payment(invoice_id, amount, date=None, *, method="", reference="",
                   source=PAYMENT_SOURCE_MANUAL, matched_txn_ref=None, txn_id=None,
                   created_by=None):
    """Append a payment against an ISSUED invoice and recompute its status.

    Refuses a payment on a DRAFT (a draft has no legal amount due) and on an unknown
    invoice. The paid total is recomputed (money.fsum) and the status moves to `paid`
    (paid_total >= gross within a cent, money.q2) or `partially_paid` (0 < paid < gross).
    A `paid` invoice that later changes is preserved by the same rule. Bank-sourced rows
    carry `matched_txn_ref` + a `txn_id` for IDEMPOTENCY: a duplicate (tenant, txn_id) is
    REFUSED (the UNIQUE index), so re-importing a statement never double-records.

    Returns (invoice_dict, "") or (None, error). Audited via the invoice_payments triggers;
    never raises (programming errors aside) — returns (None, error)."""
    try:
        amt = float(money.q2(amount))
    except Exception:
        return None, "invalid payment amount"
    if amt <= 0:
        return None, "the payment amount must be positive"
    paid_date = (date or datetime.date.today().isoformat())
    try:
        con = connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            frag, tp = tenancy.scope_clause()
            inv = con.execute("SELECT id, status, currency, gross_total, doc_type "
                              "FROM invoices WHERE id=?" + frag,
                              [invoice_id, *tp]).fetchone()
            if not inv:
                con.rollback()
                return None, "invoice not found"
            if inv["doc_type"] in NON_LEGAL_TYPES:
                con.rollback()
                return None, ("a proforma invoice / quote is not a tax invoice — it carries "
                              "no legal amount due; convert it to an invoice first")
            if inv["status"] == STATUS_DRAFT:
                con.rollback()
                return None, ("cannot record a payment on a draft — issue the invoice first "
                              "(a draft has no legal amount due)")
            # IDEMPOTENCY: a bank txn already recorded for this tenant is a no-op (not an
            # error) — re-importing the same statement must not double-book.
            if txn_id:
                dup = con.execute(
                    "SELECT 1 FROM invoice_payments WHERE txn_id=?" + frag,
                    [txn_id, *tp]).fetchone()
                if dup:
                    con.rollback()
                    return get_invoice(invoice_id), ""
            con.execute(
                """INSERT INTO invoice_payments
                   (invoice_id, paid_date, amount, currency, method, reference,
                    source, matched_txn_ref, txn_id, created_by, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (invoice_id, paid_date, amt, (inv["currency"] or DEFAULT_CURRENCY),
                 (method or "").strip(), (reference or "").strip(),
                 (source or PAYMENT_SOURCE_MANUAL), matched_txn_ref, txn_id,
                 created_by, tenancy.write_tenant()))
            paid = _paid_total(con, invoice_id)
            new_status = _status_for_paid(inv["gross_total"], paid)
            con.execute("UPDATE invoices SET status=? WHERE id=?",
                        (new_status, invoice_id))
            con.commit()
        finally:
            con.close()
        return get_invoice(invoice_id), ""
    except sqlite3.IntegrityError:
        # the UNIQUE (tenant, txn_id) index fired on a concurrent duplicate import — treat
        # as an idempotent no-op, not an error.
        log.info("record_payment: duplicate txn_id %r ignored (idempotent)", txn_id)
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("record_payment(%s) failed", invoice_id)
        return None, f"could not record the payment ({str(e)[:80]})"


def list_payments(invoice_id):
    """All payments recorded against an invoice (oldest first), tenant-scoped. Never
    raises -> []."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT * FROM invoice_payments WHERE invoice_id=?" + frag
                + " ORDER BY paid_date, id", [invoice_id, *tp]).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_payments(%s) failed: %s", invoice_id, e)
        return []


def is_overdue(invoice, today=None):
    """True iff an invoice is OVERDUE: an issued/sent/partially-paid invoice that is NOT
    fully paid and whose due date is strictly in the past. DERIVED (never stored) so it is
    always correct. A draft / a fully-paid / an undated invoice is never overdue. Pure;
    never raises -> False."""
    try:
        inv = invoice if isinstance(invoice, dict) else get_invoice(invoice)
        if not inv:
            return False
        st = inv.get("status")
        if st not in (STATUS_ISSUED, STATUS_SENT, STATUS_PARTIALLY_PAID):
            return False
        due = inv.get("due_date")
        if not due:
            return False
        ref = today or datetime.date.today().isoformat()
        # outstanding > 0 is implied by the status set above, but guard against a stale
        # status (e.g. fully paid but not yet flipped) by checking the balance too.
        if outstanding(inv) <= 0:
            return False
        return str(due) < str(ref)
    except Exception as e:
        log.warning("is_overdue() failed: %s", e)
        return False


def display_status(invoice, today=None):
    """The status to SHOW for an invoice: the stored status, except (a) a FULLY-CREDITED
    invoice reads `cancelled` (DERIVED from issued credit notes), and (b) an unpaid/
    partly-paid issued/sent invoice past its due date reads `overdue` (DERIVED). Pure;
    never raises."""
    try:
        inv = invoice if isinstance(invoice, dict) else get_invoice(invoice)
        if not inv:
            return ""
        # a fully-credited invoice is effectively cancelled — show that ahead of overdue
        # (there is nothing left to chase).
        if inv.get("doc_type") != DOC_CREDIT_NOTE and is_fully_credited(inv):
            return STATUS_CANCELLED
        if is_overdue(inv, today=today):
            return STATUS_OVERDUE
        return inv.get("status") or STATUS_DRAFT
    except Exception:
        return (invoice or {}).get("status") if isinstance(invoice, dict) else ""


def _age_bucket(due_date, today=None):
    """Aging bucket of a past-due invoice from its due date: 'current' (not yet due),
    '1-30', '31-60', '60+'. Returns (label, days_past_due). Pure."""
    ref = today or datetime.date.today().isoformat()
    try:
        d = datetime.date.fromisoformat(str(due_date))
        r = datetime.date.fromisoformat(str(ref))
    except (TypeError, ValueError):
        return "current", 0
    days = (r - d).days
    if days <= 0:
        return "current", days
    if days <= 30:
        return "1-30", days
    if days <= 60:
        return "31-60", days
    return "60+", days


# Aging-bucket order (for a stable display). 'current' = issued but not yet due.
AGING_BUCKETS = ("current", "1-30", "31-60", "60+")


def accounts_receivable(today=None):
    """The AR view: every UNPAID / partly-paid issued invoice with its outstanding balance,
    derived status and aging bucket, plus the totals. Read-only; never raises -> empty.

    Returns {"rows": [{invoice fields..., outstanding, paid, display_status, bucket,
                        days_past_due, customer_name}],
             "total_outstanding": float,
             "buckets": {bucket: {"count": n, "outstanding": float}}}.
    Money at full precision (money.fsum); the route formats for display."""
    ref = today or datetime.date.today().isoformat()
    out = {"rows": [], "total_outstanding": 0.0,
           "buckets": {b: {"count": 0, "outstanding": 0.0} for b in AGING_BUCKETS}}
    try:
        # only issued-and-onward invoices carry a legal amount due; a fully-paid one drops
        # off the AR list.
        frag, tp = tenancy.scope_clause(column="i.tenant_id")
        rows = connect_query(
            "SELECT i.*, c.name AS customer_name FROM invoices i "
            "LEFT JOIN bill_customers c ON c.id=i.customer_id "
            "WHERE i.status IN (?,?,?) AND i.doc_type='invoice'" + frag
            + " ORDER BY i.due_date, i.id",
            [STATUS_ISSUED, STATUS_SENT, STATUS_PARTIALLY_PAID, *tp])
        bucket_amts = {b: [] for b in AGING_BUCKETS}
        all_out = []
        for r in rows:
            inv = _inv_dict(r)
            inv["customer_name"] = r["customer_name"] if "customer_name" in r.keys() else None
            owed = outstanding(inv)
            if owed <= 0:
                continue
            inv["paid"] = paid_total(inv["id"])
            inv["outstanding"] = owed
            inv["display_status"] = display_status(inv, today=ref)
            bucket, days = _age_bucket(inv.get("due_date"), today=ref)
            inv["bucket"] = bucket
            inv["days_past_due"] = days
            out["rows"].append(inv)
            out["buckets"][bucket]["count"] += 1
            bucket_amts[bucket].append(owed)
            all_out.append(owed)
        for b in AGING_BUCKETS:
            out["buckets"][b]["outstanding"] = money.fsum(bucket_amts[b])
        out["total_outstanding"] = money.fsum(all_out)
        return out
    except Exception as e:
        log.warning("accounts_receivable() failed: %s", e)
        return {"rows": [], "total_outstanding": 0.0,
                "buckets": {b: {"count": 0, "outstanding": 0.0} for b in AGING_BUCKETS}}


def connect_query(sql, params):
    """Small helper: run a read-only SELECT and return the rows, closing the connection.
    Used by accounts_receivable so the AR query is a single readable call."""
    con = connect()
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


# ============================================================ PHASE 3: bank statements
# Two import paths feed the ADVISORY auto-matcher: ISO 20022 camt.053 (the SEPA bank-to-
# customer statement) and a generic bank-export CSV. Each yields the SAME normalized
# CREDIT shape so the matcher consumes either identically:
#
#   {txn_id, date, amount (positive EUR/local), reference, counterparty, iban}
#
# camt.053 is parsed with safexml (defused — entity-bomb-safe) and bounded by a size cap.
# MATCHING IS ADVISORY (mirroring bank_recon.py): we SUGGEST the best open-invoice match,
# a human confirms, and only on confirm does record_payment(source='bank') book it. The
# txn_id makes the whole flow IDEMPOTENT (re-import = no double payment).

# Hard size cap on an uploaded statement (defensive — an admin upload, but still bounded).
MAX_STATEMENT_BYTES = 8 * 1024 * 1024      # 8 MiB

# ISO 20022 camt.053 lives in a versioned namespace (camt.053.001.02 .. .08). We match by
# LOCAL element name (namespace-agnostic) so every version parses.


def _camt_local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _camt_find(el, name):
    """First descendant of `el` whose local tag == name, else None."""
    for d in el.iter():
        if _camt_local(d.tag) == name:
            return d
    return None


def _camt_findall_direct(el, name):
    """Direct-or-descendant search for ALL elements with local tag == name."""
    return [d for d in el.iter() if _camt_local(d.tag) == name]


def parse_camt053(data):
    """Parse an ISO 20022 camt.053 bank statement (bytes/str) into normalized CREDIT lines.
    Never raises (-> []). Each CREDIT entry (CdtDbtInd == 'CRDT') yields:

        {txn_id, date (value date, ISO), amount (positive float), reference (remittance /
         Ustrd / EndToEndId), counterparty (debtor name), iban (debtor IBAN)}

    Parsed with safexml (defused: entity-bomb-safe). A statement that is not camt is simply
    not recognised (returns []). The txn_id is the statement's own AcctSvcrRef/EndToEndId
    when present, else a stable hash of (date, amount, reference) so re-imports dedupe."""
    out = []
    try:
        if isinstance(data, (bytes, bytearray)) and len(data) > MAX_STATEMENT_BYTES:
            log.warning("parse_camt053: statement exceeds %d bytes, refusing",
                        MAX_STATEMENT_BYTES)
            return out
        root = safexml.fromstring(data)
    except Exception as e:
        log.warning("parse_camt053: not parseable XML: %s", e)
        return out
    try:
        entries = _camt_findall_direct(root, "Ntry")
        for ntry in entries:
            cdi = _camt_find(ntry, "CdtDbtInd")
            if cdi is None or (cdi.text or "").strip().upper() != "CRDT":
                continue          # only incoming credits settle a receivable
            amt_el = _camt_find(ntry, "Amt")
            amount = _camt_amount(amt_el)
            if amount is None or amount <= 0:
                continue
            # value date (preferred) then booking date
            date = (_camt_date(_camt_find(ntry, "ValDt"))
                    or _camt_date(_camt_find(ntry, "BookgDt")))
            # remittance / references — search the transaction detail block.
            ref = _camt_remittance(ntry)
            party, iban = _camt_debtor(ntry)
            txn_id = _camt_txn_id(ntry, date, amount, ref)
            out.append({"txn_id": txn_id, "date": date, "amount": float(amount),
                        "reference": ref or "", "counterparty": party or "",
                        "iban": iban or ""})
    except Exception as e:
        log.warning("parse_camt053: extraction failed: %s", e)
    return out


def _camt_amount(amt_el):
    """A camt <Amt> element's value as a positive float, or None."""
    if amt_el is None:
        return None
    try:
        return abs(float((amt_el.text or "").strip()))
    except (TypeError, ValueError):
        return None


def _camt_date(dt_el):
    """A camt <ValDt>/<BookgDt> wrapper -> ISO 'YYYY-MM-DD' (it wraps <Dt> or <DtTm>)."""
    if dt_el is None:
        return None
    for child in dt_el:
        txt = (child.text or "").strip()
        if txt:
            return txt[:10]
    return None


def _camt_remittance(ntry):
    """The best remittance/reference text on a camt entry: unstructured <Ustrd>, else a
    structured creditor reference, else EndToEndId, else the entry's AddtlNtryInf."""
    for name in ("Ustrd", "CdtrRefInf", "EndToEndId", "AddtlTxInf", "AddtlNtryInf"):
        el = _camt_find(ntry, name)
        if el is not None and (el.text or "").strip():
            # CdtrRefInf wraps a <Ref>; prefer that child when present.
            if name == "CdtrRefInf":
                ref = _camt_find(el, "Ref")
                if ref is not None and (ref.text or "").strip():
                    return ref.text.strip()
            return el.text.strip()
    return ""


def _camt_debtor(ntry):
    """(debtor name, debtor IBAN) on a camt CREDIT entry — the PAYER. Best-effort -> ('','')."""
    name, iban = "", ""
    dbtr = _camt_find(ntry, "Dbtr")
    if dbtr is not None:
        nm = _camt_find(dbtr, "Nm")
        if nm is not None and (nm.text or "").strip():
            name = nm.text.strip()
    acct = _camt_find(ntry, "DbtrAcct")
    if acct is not None:
        ib = _camt_find(acct, "IBAN")
        if ib is not None and (ib.text or "").strip():
            iban = ib.text.strip().replace(" ", "").upper()
    return name, iban


def _camt_txn_id(ntry, date, amount, ref):
    """A STABLE idempotency id for a camt entry: the bank's own reference when present
    (AcctSvcrRef / TxId / EndToEndId), else a sha256 of (date, amount, reference)."""
    for name in ("AcctSvcrRef", "TxId", "EndToEndId"):
        el = _camt_find(ntry, name)
        if el is not None and (el.text or "").strip():
            return f"camt:{el.text.strip()}"
    import hashlib
    h = hashlib.sha256(f"{date}|{amount}|{ref}".encode("utf-8")).hexdigest()[:24]
    return f"camt:h:{h}"


# ----------------------------------------------------------------- generic CSV statement
# Reuses the same header-alias / amount-parsing approach as bank_recon (a bank export's
# common shape) but yields the Phase-3 normalized CREDIT shape with a stable txn_id.
_CSV_DATE_ALIASES = ("date", "booking date", "bookingdate", "value date", "valuedate",
                     "transaction date", "posted")
_CSV_AMOUNT_ALIASES = ("amount", "value", "transaction amount")
_CSV_CREDIT_ALIASES = ("credit", "credit amount", "paid in", "paid-in", "money in")
_CSV_DEBIT_ALIASES = ("debit", "debit amount", "paid out", "paid-out", "money out")
_CSV_REF_ALIASES = ("reference", "description", "details", "narrative", "remittance",
                    "memo", "payment reference")
_CSV_PARTY_ALIASES = ("counterparty", "payer", "name", "debtor", "creditor", "payee",
                      "beneficiary")
_CSV_IBAN_ALIASES = ("iban", "debtor iban", "payer iban", "counterparty iban", "account")
_CSV_TXNID_ALIASES = ("txn id", "txnid", "transaction id", "id", "reference number",
                      "bank reference")


def _csv_norm_header(h):
    return (h or "").strip().lower().lstrip("﻿")


def _csv_find_col(fieldnames, aliases):
    norm = {_csv_norm_header(f): f for f in (fieldnames or [])}
    for a in aliases:
        if a in norm:
            return norm[a]
    return None


def _csv_parse_amount(raw):
    """A possibly-formatted amount cell -> float (None if unparseable). Tolerates thousands
    separators, a currency symbol, accountancy parentheses and a decimal comma."""
    s = (raw or "").strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = "".join(ch for ch in s if ch.isdigit() or ch in ".,-+")
    if not s or s in ("+", "-", ".", ","):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def _csv_parse_date(raw):
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_bank_csv(data):
    """Parse a generic bank-statement CSV (bytes/str) into normalized CREDIT lines. Never
    raises (-> []). Auto-detects columns by case-insensitive header alias (BOM tolerated):

      date         : date | booking date | value date | transaction date | posted
      amount       : amount | value     (signed; + = credit)  OR  a credit/debit pair
      reference    : reference | description | details | narrative | remittance | memo
      counterparty : counterparty | payer | name | debtor | creditor | beneficiary
      iban         : iban | payer iban | counterparty iban | account
      txn_id       : txn id | transaction id | id | bank reference   (optional)

    Only CREDITS (positive amount) are returned (a debit is not a customer payment-in).
    The txn_id is the file's own column when present, else a sha256 of (date, amount,
    reference) so re-importing the same file dedupes."""
    out = []
    try:
        if isinstance(data, (bytes, bytearray)) and len(data) > MAX_STATEMENT_BYTES:
            log.warning("parse_bank_csv: statement exceeds %d bytes, refusing",
                        MAX_STATEMENT_BYTES)
            return out
        text = (data.decode("utf-8-sig", "replace")
                if isinstance(data, (bytes, bytearray)) else str(data))
    except Exception as e:
        log.warning("parse_bank_csv: could not decode bytes: %s", e)
        return out
    import csv
    try:
        reader = csv.DictReader(io.StringIO(text))
        fields = reader.fieldnames
        if not fields:
            return out
        date_col = _csv_find_col(fields, _CSV_DATE_ALIASES)
        amt_col = _csv_find_col(fields, _CSV_AMOUNT_ALIASES)
        credit_col = _csv_find_col(fields, _CSV_CREDIT_ALIASES)
        debit_col = _csv_find_col(fields, _CSV_DEBIT_ALIASES)
        ref_col = _csv_find_col(fields, _CSV_REF_ALIASES)
        party_col = _csv_find_col(fields, _CSV_PARTY_ALIASES)
        iban_col = _csv_find_col(fields, _CSV_IBAN_ALIASES)
        txnid_col = _csv_find_col(fields, _CSV_TXNID_ALIASES)
        for row in reader:
            try:
                date = _csv_parse_date(row.get(date_col) if date_col else None)
                amount = _csv_parse_amount(row.get(amt_col)) if amt_col else None
                if amount is None and (credit_col or debit_col):
                    cr = _csv_parse_amount(row.get(credit_col)) if credit_col else None
                    dr = _csv_parse_amount(row.get(debit_col)) if debit_col else None
                    if cr:
                        amount = abs(cr)
                    elif dr:
                        amount = -abs(dr)
                if date is None or amount is None or amount <= 0:
                    continue          # skip non-credits / unparseable rows
                ref = (row.get(ref_col) or "").strip() if ref_col else ""
                party = (row.get(party_col) or "").strip() if party_col else ""
                iban = ((row.get(iban_col) or "").strip().replace(" ", "").upper()
                        if iban_col else "")
                txn_id = (row.get(txnid_col) or "").strip() if txnid_col else ""
                if txn_id:
                    txn_id = f"csv:{txn_id}"
                else:
                    import hashlib
                    h = hashlib.sha256(
                        f"{date}|{amount}|{ref}|{party}".encode("utf-8")).hexdigest()[:24]
                    txn_id = f"csv:h:{h}"
                out.append({"txn_id": txn_id, "date": date, "amount": float(amount),
                            "reference": ref, "counterparty": party, "iban": iban})
            except Exception as e:
                log.debug("parse_bank_csv: skipping bad row %r: %s", row, e)
                continue
    except Exception as e:
        log.warning("parse_bank_csv failed: %s", e)
    return out


def parse_statement(data, filename=""):
    """Parse an uploaded statement, AUTO-DETECTING the format: camt.053 (XML) vs generic
    CSV. Returns (lines, format_label). Never raises -> ([], 'unknown'). The detection is
    by content (an XML prolog / a 'camt.053'/'Document' root) then by extension, so a
    mislabelled upload still parses."""
    try:
        head = data[:512] if isinstance(data, (bytes, bytearray)) else str(data)[:512]
        head_s = (head.decode("utf-8-sig", "replace")
                  if isinstance(head, (bytes, bytearray)) else head).lstrip()
    except Exception:
        head_s = ""
    looks_xml = head_s.startswith("<?xml") or head_s.startswith("<")
    fn = (filename or "").lower()
    if looks_xml or fn.endswith(".xml") or "camt" in head_s.lower():
        lines = parse_camt053(data)
        if lines:
            return lines, "camt.053"
        # fall through to CSV only if the XML yielded nothing AND it's not clearly XML
        if looks_xml:
            return [], "camt.053"
    lines = parse_bank_csv(data)
    return lines, ("csv" if lines else "unknown")


# ----------------------------------------------------------------- advisory matching
def _norm_ref_text(s):
    """Uppercase + strip non-alphanumerics, for a forgiving substring match of an invoice
    NUMBER against free-form remittance text (spaces/dashes/slashes vary by bank)."""
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def open_invoices_for_matching(today=None):
    """The set of invoices a bank credit could settle: issued/sent/partially-paid with a
    positive outstanding balance, each enriched with its outstanding + the customer's
    stored IBAN (for the IBAN match). Read-only; never raises -> []."""
    rows = []
    try:
        frag, tp = tenancy.scope_clause(column="i.tenant_id")
        recs = connect_query(
            "SELECT i.*, c.name AS customer_name, c.iban AS customer_iban "
            "FROM invoices i LEFT JOIN bill_customers c ON c.id=i.customer_id "
            "WHERE i.status IN (?,?,?) AND i.doc_type='invoice'" + frag + " ORDER BY i.id",
            [STATUS_ISSUED, STATUS_SENT, STATUS_PARTIALLY_PAID, *tp])
    except Exception as e:
        log.warning("open_invoices_for_matching() failed: %s", e)
        return rows
    for r in recs:
        inv = _inv_dict(r)
        inv["customer_name"] = r["customer_name"] if "customer_name" in r.keys() else None
        # customer IBAN may live on the bill_customers row (Phase-3 adds the column below).
        inv["customer_iban"] = ((r["customer_iban"] or "").replace(" ", "").upper()
                                if "customer_iban" in r.keys() and r["customer_iban"]
                                else "")
        owed = outstanding(inv)
        if owed <= 0:
            continue
        inv["outstanding"] = owed
        rows.append(inv)
    return rows


# Match-reason codes, in PRIORITY order (best first). The matcher returns the first that
# hits for a credit.
MATCH_BY_NUMBER = "number"      # the invoice number appears in the remittance/reference
MATCH_BY_AMOUNT = "amount"      # exact outstanding-amount match
MATCH_BY_IBAN = "iban"         # payer IBAN == the customer's stored IBAN
MATCH_NONE = "none"


def suggest_match(credit, open_invoices):
    """ADVISORY: suggest the single best open-invoice match for ONE incoming bank CREDIT,
    by PRIORITY: (i) the invoice NUMBER appearing in the remittance/reference text, (ii) an
    exact outstanding-amount match (within a cent, money.q2), (iii) the payer IBAN equal to
    the customer's stored IBAN. Returns (invoice_or_None, reason_code). Pure; never raises.

    NEVER auto-applies — the caller shows this as a suggestion a human confirms."""
    try:
        ref_norm = _norm_ref_text(credit.get("reference"))
        # (i) invoice number in the remittance text (most specific).
        if ref_norm:
            for inv in open_invoices:
                num = _norm_ref_text(inv.get("number"))
                if num and num in ref_norm:
                    return inv, MATCH_BY_NUMBER
        # (ii) exact outstanding-amount match. If several invoices share the amount we
        # cannot disambiguate -> no confident suggestion (avoid a wrong auto-pick).
        amt = money.q2(credit.get("amount"))
        amount_hits = [inv for inv in open_invoices
                       if money.q2(inv.get("outstanding")) == amt]
        if len(amount_hits) == 1:
            return amount_hits[0], MATCH_BY_AMOUNT
        # (iii) payer IBAN == the customer's stored IBAN (again, only when unambiguous).
        payer = (credit.get("iban") or "").replace(" ", "").upper()
        if payer:
            iban_hits = [inv for inv in open_invoices
                         if (inv.get("customer_iban") or "") == payer]
            if len(iban_hits) == 1:
                return iban_hits[0], MATCH_BY_IBAN
        return None, MATCH_NONE
    except Exception as e:
        log.warning("suggest_match() failed: %s", e)
        return None, MATCH_NONE


def match_statement(lines, today=None):
    """Run the advisory matcher over parsed statement CREDITS, returning a review list:
    [{credit, suggested (invoice dict or None), reason}]. One-to-one greedy: once an
    invoice is suggested for a credit it is not re-suggested for a later credit (so two
    transfers don't both point at the same invoice). Read-only; never raises -> []."""
    try:
        open_invs = open_invoices_for_matching(today=today)
        used = set()
        review = []
        for credit in (lines or []):
            avail = [i for i in open_invs if i["id"] not in used]
            inv, reason = suggest_match(credit, avail)
            if inv is not None:
                used.add(inv["id"])
            review.append({"credit": credit, "suggested": inv, "reason": reason})
        return review
    except Exception as e:
        log.warning("match_statement() failed: %s", e)
        return []


# ============================================================ PDF (Art. 226 compliant)
def _invoice_view(invoice_id):
    """Assemble everything the PDF/template needs: header, lines, the per-rate breakdown,
    and the issuer/customer details — from the SNAPSHOT once issued, live while draft.
    Returns a dict or None."""
    import json
    inv = get_invoice(invoice_id)
    if not inv:
        return None
    lines = get_lines(invoice_id)
    issued = inv["status"] != STATUS_DRAFT
    if issued and inv.get("issuer_snapshot"):
        try:
            issuer = json.loads(inv["issuer_snapshot"])
        except Exception:
            issuer = get_issuer()
    else:
        issuer = get_issuer()
    if issued and inv.get("customer_snapshot"):
        try:
            customer = json.loads(inv["customer_snapshot"])
        except Exception:
            customer = get_customer(inv["customer_id"]) or {}
    else:
        customer = get_customer(inv["customer_id"]) or {}
    totals = compute_totals(lines, reverse_charge=bool(inv["reverse_charge"]),
                            disc_kind=inv.get("disc_kind"), disc_value=inv.get("disc_value"))
    # For a CREDIT NOTE expose the ORIGINAL invoice (number + issue date) so the document
    # can carry the mandatory reference back to the corrected invoice.
    doc_type = inv.get("doc_type") or DOC_INVOICE
    original = None
    if doc_type == DOC_CREDIT_NOTE and inv.get("corrects_invoice_id"):
        original = get_invoice(inv.get("corrects_invoice_id"))
    return {"invoice": inv, "lines": totals["lines"], "issuer": issuer, "customer": customer,
            "by_rate": totals["by_rate"], "issued": issued, "original": original,
            "totals": totals, "doc_type": doc_type,
            "is_credit_note": doc_type == DOC_CREDIT_NOTE,
            "is_proforma": doc_type == DOC_PROFORMA, "is_quote": doc_type == DOC_QUOTE,
            "is_non_legal": doc_type in NON_LEGAL_TYPES}


def _fmt_money(x):
    return f"{money.f2(x):,.2f}"


def _pct(rate):
    """A VAT rate fraction (0.21) as a human percent string ('21%' / '5.5%')."""
    v = money.D(rate) * 100
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{s}%"


def invoice_text(invoice_id):
    """The plain-text body of the invoice carrying EVERY Art. 226 mandatory field, in the
    order a reader expects. Used both for the PDF (via customer_master.text_to_pdf) and as
    an assertable representation in tests. A DRAFT is clearly labelled "DRAFT — not a valid
    invoice". Returns the text string, or "" if the invoice is unknown."""
    v = _invoice_view(invoice_id)
    if not v:
        return ""
    inv, lines, issuer, cust = v["invoice"], v["lines"], v["issuer"], v["customer"]
    ccy = inv.get("currency") or DEFAULT_CURRENCY
    is_cn = v.get("is_credit_note")
    is_pf = v.get("is_proforma")
    is_qt = v.get("is_quote")
    original = v.get("original")
    if is_cn:
        doc_word, doc_kw = "CREDIT NOTE", "Credit note"
    elif is_pf:
        doc_word, doc_kw = "PROFORMA INVOICE", "Proforma"
    elif is_qt:
        doc_word, doc_kw = "QUOTE", "Quote"
    else:
        doc_word, doc_kw = "INVOICE", "Invoice"
    L = []
    if not v["issued"]:
        L.append(f"*** DRAFT — not a valid {doc_word.lower()} ***")
        L.append("")
    # PROFORMA/QUOTE: a loud "not a tax invoice / not a demand for payment" banner.
    if is_pf:
        L.append("*** PROFORMA INVOICE — not a VAT invoice, not a demand for payment ***")
        L.append("")
    elif is_qt:
        L.append("*** QUOTE — not a VAT invoice ***")
        L.append("")
    # Art. 226(2) sequential number / (1) issue date / 'date of supply'
    L.append(doc_word if v["issued"] else f"{doc_word} (DRAFT)")
    L.append(f"{doc_kw} number: "
             f"{inv.get('number') or '(assigned at issue)'}")
    L.append(f"Issue date: {inv.get('issue_date') or '(at issue)'}")
    sd = inv.get("supply_date")
    if sd and sd != inv.get("issue_date"):
        L.append(f"Date of supply: {sd}")
    # CREDIT NOTE: reference the ORIGINAL invoice (number + issue date) + reason.
    if is_cn and original:
        L.append(f"Original invoice: {original.get('number') or '—'}"
                 + (f" ({original.get('issue_date')})" if original.get('issue_date') else ""))
        if inv.get("notes"):
            L.append(f"Reason: {inv.get('notes')}")
    L.append("")
    # Art. 226(3)/(4): supplier (issuer) + customer full name, address, VAT id
    L.append("Supplier (issuer):")
    L.append(f"  {issuer.get('name') or ''}")
    if issuer.get("address"):
        L.append(f"  {issuer.get('address')}")
    L.append(f"  VAT no: {issuer.get('vat_number') or ''}")
    if issuer.get("reg_no"):
        L.append(f"  Reg no: {issuer.get('reg_no')}")
    L.append("")
    L.append("Customer (bill to):")
    L.append(f"  {cust.get('name') or ''}")
    if cust.get("address"):
        L.append(f"  {cust.get('address')}")
    L.append(f"  VAT no: {cust.get('vat_number') or '(not VAT-registered)'}")
    if cust.get("reg_no"):
        L.append(f"  Reg no: {cust.get('reg_no')}")
    L.append("")
    # Art. 226(6)/(7)/(8): per-line description, qty, unit price, rate. The Discount column
    # shows the per-line allowance (the line Net is POST-discount).
    L.append(f"Lines (amounts NET, {ccy}, VAT excluded; Net is after any line discount):")
    L.append(f"  {'#':<3}{'Description':<30}{'Qty':>7} {'Unit price':>11} "
             f"{'Disc.':>9} {'Rate':>6} {'Net':>12}")
    for i, ln in enumerate(lines, 1):
        disc = float(ln.get("discount_amount") or 0)
        disc_s = f"-{_fmt_money(disc)}" if disc > 0 else "—"
        L.append(f"  {i:<3}{(ln.get('description') or '')[:30]:<30}"
                 f"{money.D(ln.get('quantity')):>7} {_fmt_money(ln.get('unit_price_net')):>11} "
                 f"{disc_s:>9} {_pct(ln.get('vat_rate')):>6} {_fmt_money(ln.get('line_net')):>12}")
    L.append("")
    # INVOICE-level (document) discount: the pre/post amounts. The per-rate breakdown + the
    # totals below are already POST document discount.
    doc_disc = float((v.get("totals") or {}).get("doc_discount") or inv.get("disc_amount") or 0)
    if doc_disc > 0:
        sub = float((v.get("totals") or {}).get("line_net_total") or 0)
        L.append(f"Subtotal (net):       {_fmt_money(sub):>15} {ccy}")
        L.append(f"Document discount:   -{_fmt_money(doc_disc):>15} {ccy}")
        L.append("")
    # Art. 226(9)/(10): per-VAT-rate breakdown (taxable amount per rate, rate, VAT amount)
    L.append("VAT breakdown:")
    L.append(f"  {'Rate':>7} {'Taxable net':>15} {'VAT amount':>15}")
    for b in v["by_rate"]:
        L.append(f"  {_pct(b['rate']):>7} {_fmt_money(b['net']):>15} "
                 f"{_fmt_money(b['vat']):>15}")
    L.append("")
    # totals
    L.append(f"Total net:   {_fmt_money(inv.get('net_total')):>15} {ccy}")
    L.append(f"Total VAT:   {_fmt_money(inv.get('vat_total')):>15} {ccy}")
    L.append(f"Grand total: {_fmt_money(inv.get('gross_total')):>15} {ccy}")
    # VAT-IN-EUR (LV/EU): a foreign-currency invoice must ALSO state the VAT in EUR.
    if ccy != "EUR":
        eur_vat, rate, source = vat_total_eur(inv)
        if eur_vat is not None:
            L.append(f"Total VAT (EUR): {_fmt_money(eur_vat):>11} EUR  "
                     f"(FX {money.D(rate):g} {ccy}/EUR, {source})")
    L.append("")
    # SIMPLIFIED INVOICE label (Art. 238/226b) when the flag is set.
    if inv.get("simplified"):
        L.append(f"Simplified invoice (gross <= EUR {SIMPLIFIED_GROSS_CEILING_EUR:.0f}, "
                 "EU VAT Dir. Art. 238).")
        L.append("")
    # Art. 226(11)/(11a): exemption / reverse-charge wording
    if inv.get("reverse_charge"):
        L.append(REVERSE_CHARGE_NOTE)
        L.append("")
    # payment terms + due date + IBAN. A proforma/quote is NOT a demand for payment, so it
    # carries no "Payment due" wording (a proforma may still show bank details for prepayment).
    if is_qt:
        L.append("This is a quote — not a demand for payment.")
    elif is_pf:
        L.append("Proforma — for advance payment / order confirmation; not a VAT invoice.")
    else:
        L.append(f"Payment due: {inv.get('due_date') or '(set at issue)'}")
    if issuer.get("iban") and not is_qt:
        L.append(f"IBAN: {issuer.get('iban')}"
                 + (f" ({issuer.get('bank')})" if issuer.get("bank") else ""))
    if inv.get("notes"):
        L.append("")
        L.append(f"Notes: {inv.get('notes')}")
    if issuer.get("logo_text"):
        L.insert(0, issuer.get("logo_text"))
        L.insert(1, "")
    return "\n".join(L)


# ----------------------------------------------------------------- HTML invoice template
_INVOICE_CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: 'DejaVu Sans', 'Helvetica Neue', Arial, sans-serif;
       font-size: 10pt; color: #1a1a1a; margin: 0; }
.draft-banner { background: #fff4f4; border: 1px solid #d33; color: #b00020;
                padding: 8px 12px; margin: 0 0 14px 0; font-weight: bold;
                text-align: center; letter-spacing: .04em; }
.watermark { position: fixed; top: 42%; left: 0; right: 0; text-align: center;
             font-size: 92pt; color: rgba(200,0,0,.07); font-weight: bold;
             transform: rotate(-24deg); z-index: -1; letter-spacing: .08em; }
.head { display: table; width: 100%; margin-bottom: 18px; }
.head .issuer { display: table-cell; vertical-align: top; width: 58%; }
.head .meta { display: table-cell; vertical-align: top; text-align: right; }
.issuer .logo { font-size: 15pt; font-weight: bold; color: #0a3d62; }
.issuer .logoimg { margin-bottom: 6px; }
.issuer .logoimg img { max-height: 64px; max-width: 240px; height: auto; width: auto; }
.issuer .name { font-size: 12pt; font-weight: bold; }
.issuer .det { color: #444; line-height: 1.4; }
.title { font-size: 22pt; font-weight: bold; color: #0a3d62; margin: 0 0 4px 0; }
.title .lv { color: #888; font-size: 13pt; font-weight: normal; }
.meta table { border-collapse: collapse; margin-left: auto; }
.meta td { padding: 1px 0 1px 10px; }
.meta td.k { color: #666; text-align: right; }
.meta td.v { font-weight: bold; text-align: right; }
.billto { border: 1px solid #ddd; background: #fafafa; padding: 8px 12px;
          margin-bottom: 16px; }
.billto .lbl { color: #888; font-size: 8pt; text-transform: uppercase;
               letter-spacing: .06em; margin-bottom: 2px; }
.billto .name { font-weight: bold; }
.billto .det { color: #444; line-height: 1.4; }
table.lines { width: 100%; border-collapse: collapse; margin-bottom: 14px; }
table.lines th { background: #0a3d62; color: #fff; font-weight: bold;
                 padding: 6px 8px; text-align: left; font-size: 9pt; }
table.lines td { padding: 5px 8px; border-bottom: 1px solid #eee; vertical-align: top; }
table.lines td.num, table.lines th.num { text-align: right; white-space: nowrap; }
.totbox { width: 100%; display: table; }
.totbox .vat { display: table-cell; vertical-align: top; width: 56%; }
.totbox .grand { display: table-cell; vertical-align: bottom; text-align: right; }
table.vat { border-collapse: collapse; font-size: 9pt; }
table.vat th, table.vat td { padding: 3px 10px; border-bottom: 1px solid #eee;
                             text-align: right; }
table.vat th { color: #666; text-align: right; font-weight: normal; }
table.grand { border-collapse: collapse; margin-left: auto; }
table.grand td { padding: 3px 12px; text-align: right; }
table.grand td.k { color: #555; }
table.grand tr.total td { font-size: 12pt; font-weight: bold; color: #0a3d62;
                          border-top: 2px solid #0a3d62; padding-top: 6px; }
.note { background: #f4f8fb; border-left: 3px solid #0a3d62; padding: 8px 12px;
        margin: 12px 0; color: #234; }
.pay { margin-top: 18px; border-top: 1px solid #ddd; padding-top: 10px; color: #333; }
.pay .iban { font-weight: bold; }
.foot { margin-top: 22px; color: #999; font-size: 8pt; text-align: center; }
"""


def _h(v):
    """Escape a value for HTML (markupsafe). None -> ''."""
    return esc("" if v is None else str(v))


import re as _re_color
_HEX_COLOR = _re_color.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _safe_color(value):
    """Return a SAFE #rrggbb/#rgb hex colour string, or "" for anything else. This defends the
    invoice CSS against injection — only a strict hex literal is ever interpolated into the
    <style>. Pure."""
    s = (value or "").strip()
    if not s:
        return ""
    if not s.startswith("#"):
        s = "#" + s
    return s if _HEX_COLOR.match(s) else ""


def _brand_css(color):
    """A small CSS override that re-skins the invoice's accent colour to the issuer brand
    colour (`color` is a pre-validated hex string). Applies to the title, the line-table
    header band, the grand-total rule, and the issuer logo text. Pure."""
    return (f".issuer .logo {{ color:{color}; }}"
            f".title {{ color:{color}; }}"
            f"table.lines th {{ background:{color}; }}"
            f"table.grand tr.total td {{ color:{color}; border-top-color:{color}; }}"
            f".note {{ border-left-color:{color}; }}")


def invoice_html(invoice_id, lang=None):
    """The full invoice as a standalone, print-ready A4 HTML document (UTF-8). EVERY DB
    value is escaped via markupsafe (`esc`) — no raw f-string interpolation of DB text.

    Design: issuer header (name/address/VAT/reg/IBAN), the "INVOICE / Rēķins" title +
    number + dates, a customer bill-to block, the line-item table (description / qty /
    unit / unit price / rate / net), the per-VAT-rate subtotal table, the totals
    (net/VAT/grand, currency; + VAT-in-EUR when currency != EUR), payment terms + due
    date + IBAN, the reverse-charge/exemption wording, and a DRAFT watermark + banner
    until the invoice is issued. Returns the HTML string, or "" if the invoice is unknown.

    i18n: the FIXED LABELS (not DB values) go through i18n.t in the invoice's language.
    `lang=None` resolves the current request language (so the on-screen PDF matches the UI);
    an explicit `lang` (e.g. the issuer's default) overrides. DEFAULT 'en' renders the
    document byte-identically to before. The historic dual-language title "INVOICE / Rēķins"
    is preserved under English and collapses to the single Latvian title under lv.

    Latvian + full Unicode render NATIVELY here (UTF-8 + system fonts via wkhtmltopdf)."""
    import i18n
    L = i18n.normalize(lang) if lang is not None else i18n.current_lang()
    def _t(s):
        return i18n.t(s, L)
    v = _invoice_view(invoice_id)
    if not v:
        return ""
    inv, lines, issuer, cust = v["invoice"], v["lines"], v["issuer"], v["customer"]
    ccy = inv.get("currency") or DEFAULT_CURRENCY
    issued = v["issued"]
    is_cn = v.get("is_credit_note")
    is_pf = v.get("is_proforma")
    is_qt = v.get("is_quote")
    original = v.get("original")
    P = []                                              # HTML parts (already escaped)
    P.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    P.append(f"<style>{_INVOICE_CSS}</style>")
    # PHASE 7: the issuer accent/brand colour overrides the default header colour. Only a
    # safe #rrggbb / #rgb hex string is honoured (defended against CSS injection).
    brand = _safe_color(issuer.get("brand_color"))
    if brand:
        P.append(f"<style>{_brand_css(brand)}</style>")
    P.append("</head><body>")
    if not issued:
        P.append(f"<div class='watermark'>{_h(_t('DRAFT'))}</div>")
        P.append(f"<div class='draft-banner'>{_h(_t('DRAFT'))} — {_h('not a valid invoice')}</div>")
    # PHASE 7: PROFORMA / QUOTE — a loud "not a tax invoice / not a demand for payment" banner.
    if is_pf:
        P.append(f"<div class='draft-banner' style='background:#f4f8fb;border-color:#0a3d62;"
                 f"color:#0a3d62'>{_h(_t('PROFORMA INVOICE'))} — "
                 f"{_h(_t('not a VAT invoice, not a demand for payment'))}</div>")
    elif is_qt:
        P.append(f"<div class='draft-banner' style='background:#f4f8fb;border-color:#0a3d62;"
                 f"color:#0a3d62'>{_h(_t('QUOTE'))} — {_h(_t('not a VAT invoice'))}</div>")
    # ---- header: issuer block (left) + invoice meta (right) ----
    P.append("<div class='head'><div class='issuer'>")
    # PHASE 7: the issuer LOGO IMAGE (data: URI), shown above the text header. A missing/
    # corrupt logo degrades to no image (the legacy text header still renders).
    _logo = logo_data_uri()
    if _logo:
        P.append(f"<div class='logoimg'><img src='{_h(_logo)}' alt=''></div>")
    if issuer.get("logo_text"):
        P.append(f"<div class='logo'>{_h(issuer.get('logo_text'))}</div>")
    P.append(f"<div class='name'>{_h(issuer.get('name'))}</div>")
    P.append("<div class='det'>")
    if issuer.get("address"):
        P.append(f"{_h(issuer.get('address'))}<br>")
    P.append(f"{_h(_t('VAT'))}: {_h(issuer.get('vat_number'))}")
    if issuer.get("reg_no"):
        P.append(f"<br>{_h(_t('Reg. no'))}: {_h(issuer.get('reg_no'))}")
    if issuer.get("iban"):
        bank = f" ({_h(issuer.get('bank'))})" if issuer.get("bank") else ""
        P.append(f"<br>{_h(_t('IBAN'))}: {_h(issuer.get('iban'))}{bank}")
    P.append("</div></div>")                            # /issuer /det
    P.append("<div class='meta'>")
    # Title: English keeps the historic dual-language form; Latvian collapses to the single
    # localized title. A CREDIT NOTE swaps "INVOICE / Rēķins" for "CREDIT NOTE / Kreditrēķins".
    if is_cn:
        if L == "lv":
            P.append(f"<div class='title'>{_h(_t('CREDIT NOTE'))}</div>")
        else:
            P.append("<div class='title'>CREDIT NOTE <span class='lv'>/ Kreditrēķins</span></div>")
    elif is_pf:
        if L == "lv":
            P.append(f"<div class='title'>{_h(_t('PROFORMA INVOICE'))}</div>")
        else:
            P.append("<div class='title'>PROFORMA INVOICE <span class='lv'>/ Priekšapmaksas rēķins</span></div>")
    elif is_qt:
        if L == "lv":
            P.append(f"<div class='title'>{_h(_t('QUOTE'))}</div>")
        else:
            P.append("<div class='title'>QUOTE <span class='lv'>/ Piedāvājums</span></div>")
    elif L == "lv":
        P.append(f"<div class='title'>{_h(_t('INVOICE'))}</div>")
    else:
        P.append("<div class='title'>INVOICE <span class='lv'>/ Rēķins</span></div>")
    P.append("<table>")
    P.append(f"<tr><td class='k'>{_h(_t('Number'))}</td><td class='v'>"
             f"{_h(inv.get('number') or '(assigned at issue)')}</td></tr>")
    P.append(f"<tr><td class='k'>{_h(_t('Issue date'))}</td><td class='v'>"
             f"{_h(inv.get('issue_date') or '(at issue)')}</td></tr>")
    sd = inv.get("supply_date")
    if sd and sd != inv.get("issue_date"):
        P.append(f"<tr><td class='k'>{_h(_t('Date of supply'))}</td><td class='v'>{_h(sd)}</td></tr>")
    P.append(f"<tr><td class='k'>{_h(_t('Due date'))}</td><td class='v'>"
             f"{_h(inv.get('due_date') or '(set at issue)')}</td></tr>")
    P.append(f"<tr><td class='k'>{_h(_t('Currency'))}</td><td class='v'>{_h(ccy)}</td></tr>")
    # CREDIT NOTE: a clear reference back to the ORIGINAL invoice (number + issue date).
    if is_cn and original:
        P.append(f"<tr><td class='k'>{_h(_t('Original invoice'))}</td><td class='v'>"
                 f"{_h(original.get('number') or '—')}"
                 + (f" ({_h(original.get('issue_date'))})" if original.get('issue_date') else "")
                 + "</td></tr>")
    P.append("</table></div></div>")                    # /meta /head
    # ---- bill-to ----
    P.append(f"<div class='billto'><div class='lbl'>{_h(_t('Bill to'))}</div>")
    P.append(f"<div class='name'>{_h(cust.get('name'))}</div><div class='det'>")
    if cust.get("address"):
        P.append(f"{_h(cust.get('address'))}<br>")
    P.append(f"{_h(_t('VAT'))}: {_h(cust.get('vat_number') or '(not VAT-registered)')}")
    if cust.get("reg_no"):
        P.append(f"<br>{_h(_t('Reg. no'))}: {_h(cust.get('reg_no'))}")
    P.append("</div></div>")
    # ---- line-item table (amounts NET, VAT excluded). A Discount column shows the per-line
    # allowance; the Net column is POST-discount. ----
    any_line_disc = any(float(ln.get("discount_amount") or 0) > 0 for ln in lines)
    P.append("<table class='lines'><thead><tr>"
             f"<th class='num'>#</th><th>{_h(_t('Description'))}</th>"
             f"<th class='num'>{_h(_t('Qty'))}</th><th>{_h(_t('Unit'))}</th>"
             f"<th class='num'>{_h(_t('Unit price'))}</th>"
             + (f"<th class='num'>{_h(_t('Discount'))}</th>" if any_line_disc else "")
             + f"<th class='num'>{_h(_t('VAT'))}</th>"
             f"<th class='num'>{_h(_t('Net'))} ({_h(ccy)})</th></tr></thead><tbody>")
    for i, ln in enumerate(lines, 1):
        disc = float(ln.get("discount_amount") or 0)
        disc_cell = ""
        if any_line_disc:
            disc_cell = (f"<td class='num'>-{_h(_fmt_money(disc))}</td>"
                         if disc > 0 else "<td class='num'>—</td>")
        P.append(
            "<tr>"
            f"<td class='num'>{i}</td>"
            f"<td>{_h(ln.get('description'))}</td>"
            f"<td class='num'>{_h(format(money.D(ln.get('quantity', 0)), 'g'))}</td>"
            f"<td>{_h(ln.get('unit'))}</td>"
            f"<td class='num'>{_h(_fmt_money(ln.get('unit_price_net')))}</td>"
            + disc_cell
            + f"<td class='num'>{_h(_pct(ln.get('vat_rate')))}</td>"
            f"<td class='num'>{_h(_fmt_money(ln.get('line_net')))}</td>"
            "</tr>")
    P.append("</tbody></table>")
    # ---- totals: per-rate VAT (left) + grand totals (right) ----
    P.append("<div class='totbox'><div class='vat'>")
    P.append(f"<table class='vat'><thead><tr><th>{_h(_t('VAT rate'))}</th>"
             f"<th>{_h(_t('Taxable net'))}</th><th>{_h(_t('VAT amount'))}</th></tr></thead><tbody>")
    for b in v["by_rate"]:
        P.append(f"<tr><td>{_h(_pct(b['rate']))}</td>"
                 f"<td>{_h(_fmt_money(b['net']))}</td>"
                 f"<td>{_h(_fmt_money(b['vat']))}</td></tr>")
    P.append("</tbody></table></div><div class='grand'><table class='grand'>")
    # PHASE 7: a DOCUMENT-level discount shows the pre-discount subtotal + the discount line
    # above the (post-discount) total-net row.
    doc_disc = float((v.get("totals") or {}).get("doc_discount") or inv.get("disc_amount") or 0)
    if doc_disc > 0:
        sub = float((v.get("totals") or {}).get("line_net_total") or 0)
        P.append(f"<tr><td class='k'>{_h(_t('Subtotal (net)'))}</td><td>"
                 f"{_h(_fmt_money(sub))} {_h(ccy)}</td></tr>")
        P.append(f"<tr><td class='k'>{_h(_t('Document discount'))}</td><td>"
                 f"-{_h(_fmt_money(doc_disc))} {_h(ccy)}</td></tr>")
    P.append(f"<tr><td class='k'>{_h(_t('Total net'))}</td><td>"
             f"{_h(_fmt_money(inv.get('net_total')))} {_h(ccy)}</td></tr>")
    P.append(f"<tr><td class='k'>{_h(_t('Total VAT'))}</td><td>"
             f"{_h(_fmt_money(inv.get('vat_total')))} {_h(ccy)}</td></tr>")
    P.append(f"<tr class='total'><td class='k'>{_h(_t('Grand total'))}</td><td>"
             f"{_h(_fmt_money(inv.get('gross_total')))} {_h(ccy)}</td></tr>")
    if ccy != "EUR":
        eur_vat, rate, source = vat_total_eur(inv)
        if eur_vat is not None:
            P.append(f"<tr><td class='k'>{_h(_t('VAT in EUR'))}</td><td>{_h(_fmt_money(eur_vat))} EUR"
                     f"<br><span style='color:#888;font-size:8pt'>FX "
                     f"{_h(format(money.D(rate), 'g'))} {_h(ccy)}/EUR · {_h(source)}"
                     f"</span></td></tr>")
    P.append("</table></div></div>")                    # /grand /totbox
    # ---- notes / legal wording ----
    # CREDIT NOTE: the amounts above are a CREDIT against the referenced original invoice.
    if is_cn:
        ref = ""
        if original and original.get("number"):
            ref = (f" — {_h(_t('References original invoice'))}: "
                   f"{_h(original.get('number'))}"
                   + (f" ({_h(original.get('issue_date'))})" if original.get('issue_date') else ""))
        P.append(f"<div class='note'>{_h(_t('Credit note'))}: "
                 f"{_h(_t('the amounts are a credit to the customer'))}.{ref}</div>")
    if inv.get("simplified"):
        P.append(f"<div class='note'>{_h(_t('Simplified invoice'))} "
                 f"(gross ≤ EUR {SIMPLIFIED_GROSS_CEILING_EUR:.0f}, "
                 "EU VAT Dir. Art. 238).</div>")
    if inv.get("reverse_charge"):
        P.append(f"<div class='note'>{_h(_t(REVERSE_CHARGE_NOTE))}</div>")
    if inv.get("notes"):
        lbl = (_h(_t('Reason')) + ": ") if is_cn else ""
        P.append(f"<div class='note'>{lbl}{_h(inv.get('notes'))}</div>")
    # ---- payment block. A QUOTE is NOT a demand for payment; a PROFORMA is for advance
    # payment but is not a VAT invoice. ----
    P.append("<div class='pay'>")
    if is_qt:
        P.append(_h(_t("This is a quote — not a demand for payment.")))
    elif is_pf:
        P.append(_h(_t("Proforma — for advance payment / order confirmation; "
                       "not a VAT invoice.")))
        if issuer.get("iban"):
            bank = f" ({_h(issuer.get('bank'))})" if issuer.get("bank") else ""
            P.append(f" Please transfer to IBAN <span class='iban'>"
                     f"{_h(issuer.get('iban'))}</span>{bank}.")
    else:
        P.append(f"{_h(_t('Payment due'))}: <b>{_h(inv.get('due_date') or '(set at issue)')}</b>.")
        if issuer.get("iban"):
            bank = f" ({_h(issuer.get('bank'))})" if issuer.get("bank") else ""
            P.append(f" Please transfer to IBAN <span class='iban'>"
                     f"{_h(issuer.get('iban'))}</span>{bank}.")
    P.append("</div>")
    P.append("<div class='foot'>Amounts are NET (VAT excluded) unless stated, "
             f"in {_h(ccy)}. Retain per statutory record-keeping rules.</div>")
    P.append("</body></html>")
    return "".join(str(p) for p in P)


def _wkhtmltopdf_available():
    """True iff the wkhtmltopdf CLI is resolvable on this host. Cheap, never raises."""
    try:
        return shutil.which(WKHTMLTOPDF_BIN) is not None
    except Exception:
        return False


def _render_pdf_wkhtmltopdf(html):
    """Render an HTML document to PDF bytes via the wkhtmltopdf CLI. SAFETY: a FIXED argv
    list (no shell, so DB text in the HTML can never be interpreted as a command), input
    and output go through dedicated TEMP FILES that are always cleaned up, and the call is
    bounded by WKHTMLTOPDF_TIMEOUT. Returns the PDF bytes, or None on any failure (the
    caller then degrades to the Unicode fallback) — NEVER raises."""
    path = shutil.which(WKHTMLTOPDF_BIN)
    if not path:
        return None
    in_fd, in_path = tempfile.mkstemp(suffix=".html")
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        with os.fdopen(in_fd, "wb") as fh:
            fh.write(html.encode("utf-8"))
        # Fixed argv; --enable-local-file-access is off (no remote/file fetch needed —
        # the doc is self-contained), quiet, A4 with the @page margins from the CSS.
        argv = [path, "--quiet", "--encoding", "utf-8", "--print-media-type",
                "--page-size", "A4", in_path, out_path]
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=WKHTMLTOPDF_TIMEOUT, check=False)
        if proc.returncode != 0 and not os.path.getsize(out_path):
            log.warning("wkhtmltopdf rc=%s: %s", proc.returncode,
                        proc.stderr.decode("utf-8", "replace")[:200])
            return None
        with open(out_path, "rb") as fh:
            data = fh.read()
        return data if data[:5] == b"%PDF-" else None
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("wkhtmltopdf render failed: %s", e)
        return None
    except Exception as e:                              # defensive: never raise
        log.warning("wkhtmltopdf render unexpected error: %s", e)
        return None
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ------------------------------------------- Unicode (DejaVu-embedded) fallback PDF
# A dependency-free PDF writer that EMBEDS a Unicode TrueType font and writes text as a
# composite Type0 font with Identity-H encoding (glyph IDs straight from the font's
# cmap). This is what renders Latvian when wkhtmltopdf is absent — every char maps to its
# real glyph, so `ā š ž ē ī ū ķ ļ ņ ģ č` are drawn, NEVER the latin-1 `?`. Plainer than
# the HTML version, but legible and legally complete.

def _find_fallback_font():
    """Path to the first available Unicode TrueType font, or None."""
    for p in _FALLBACK_FONT_CANDIDATES:
        try:
            if os.path.isfile(p):
                return p
        except OSError:
            continue
    return None


def _ttf_tables(data):
    """Parse the TrueType table directory -> {tag: (offset, length)}. Supports the plain
    'sfnt' (0x00010000 / 'true') layout used by DejaVuSans.ttf."""
    if len(data) < 12:
        raise ValueError("not a TrueType font")
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables = {}
    off = 12
    for _ in range(num_tables):
        tag = data[off:off + 4].decode("latin-1")
        _checksum, t_off, t_len = struct.unpack(">III", data[off + 4:off + 16])
        tables[tag] = (t_off, t_len)
        off += 16
    return tables


def _ttf_cmap_unicode(data, tables):
    """Build a {codepoint -> glyph_id} map from the font's cmap. Prefers a Unicode BMP
    (platform 3, encoding 1, format 4) subtable; falls back to a format-12 (full Unicode)
    subtable. Covers every codepoint we draw (Latin + Latvian diacritics)."""
    base, _ = tables["cmap"]
    ntab = struct.unpack(">H", data[base + 2:base + 4])[0]
    sub_off = None
    f12_off = None
    for i in range(ntab):
        rec = base + 4 + i * 8
        plat, enc = struct.unpack(">HH", data[rec:rec + 4])
        offset = struct.unpack(">I", data[rec + 4:rec + 8])[0]
        fmt = struct.unpack(">H", data[base + offset:base + offset + 2])[0]
        if plat == 3 and enc in (1, 10) and fmt == 4 and sub_off is None:
            sub_off = base + offset
        if (plat == 3 and enc == 10 or plat == 0) and fmt == 12:
            f12_off = base + offset
    cmap = {}
    if sub_off is not None:
        seg_x2 = struct.unpack(">H", data[sub_off + 6:sub_off + 8])[0]
        segc = seg_x2 // 2
        p = sub_off + 14
        end = struct.unpack(">%dH" % segc, data[p:p + seg_x2]); p += seg_x2 + 2
        start = struct.unpack(">%dH" % segc, data[p:p + seg_x2]); p += seg_x2
        delta = struct.unpack(">%dh" % segc, data[p:p + seg_x2]); p += seg_x2
        ro_base = p
        rangeoff = struct.unpack(">%dH" % segc, data[p:p + seg_x2])
        for s in range(segc):
            for c in range(start[s], end[s] + 1):
                if c == 0xFFFF:
                    continue
                if rangeoff[s] == 0:
                    g = (c + delta[s]) & 0xFFFF
                else:
                    gi = ro_base + s * 2 + rangeoff[s] + (c - start[s]) * 2
                    g = struct.unpack(">H", data[gi:gi + 2])[0]
                    if g != 0:
                        g = (g + delta[s]) & 0xFFFF
                if g:
                    cmap[c] = g
    if f12_off is not None:
        ngroups = struct.unpack(">I", data[f12_off + 12:f12_off + 16])[0]
        gp = f12_off + 16
        for _ in range(ngroups):
            sc, ec, sg = struct.unpack(">III", data[gp:gp + 12]); gp += 12
            for c in range(sc, ec + 1):
                cmap.setdefault(c, sg + (c - sc))
    return cmap


def _ttf_metrics(data, tables):
    """Return (units_per_em, num_glyphs, advance_widths[list]) from head/maxp/hhea/hmtx."""
    head, _ = tables["head"]
    upm = struct.unpack(">H", data[head + 18:head + 20])[0]
    maxp, _ = tables["maxp"]
    nglyphs = struct.unpack(">H", data[maxp + 4:maxp + 6])[0]
    hhea, _ = tables["hhea"]
    num_hm = struct.unpack(">H", data[hhea + 34:hhea + 36])[0]
    hmtx, _ = tables["hmtx"]
    widths = []
    p = hmtx
    last = 0
    for i in range(nglyphs):
        if i < num_hm:
            last = struct.unpack(">H", data[p:p + 2])[0]
            p += 4
        widths.append(last)
    return upm, nglyphs, widths


def _pdf_unicode_doc(font_bytes, pages, lines_widths=None):
    """Assemble a multi-page PDF that embeds `font_bytes` (a Unicode TTF) as a Type0
    composite font (Identity-H) and draws `pages` — a list of pages, each a list of
    (x, y, size, glyph_ids) text runs. Returns the PDF bytes.

    The text is emitted as 2-byte GLYPH IDS (Identity-H), so any Unicode codepoint the
    font covers is drawn correctly. A ToUnicode CMap is omitted (display-only doc) but the
    glyphs themselves are the real Latvian letters — never `?`."""
    upm, nglyphs, widths = lines_widths
    # /W default width array maps every glyph to its real advance (1000-unit text space).
    scale = 1000.0 / upm
    w_entries = " ".join(str(int(round(w * scale))) for w in widths)

    objs = []   # (body_str_or_bytes,)
    def add(body):
        objs.append(body)
        return len(objs)

    # Reserve ids in a fixed order so the cross-refs are simple.
    catalog_id = 1
    pages_id = 2
    font0_id = 3        # Type0
    cidfont_id = 4      # CIDFontType2
    desc_id = 5         # FontDescriptor
    fontfile_id = 6     # FontFile2 (the embedded TTF)
    w_id = 7            # the /W width array (own object to keep the dict small)
    cidsysinfo_id = 8
    first_page_id = 9   # pages then content objects follow

    page_ids = [first_page_id + 2 * i for i in range(len(pages))]
    content_ids = [pid + 1 for pid in page_ids]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)

    parts = []          # (id, header_str, stream_bytes_or_None)
    parts.append((catalog_id, f"<< /Type /Catalog /Pages {pages_id} 0 R >>", None))
    parts.append((pages_id,
                  f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>", None))
    parts.append((font0_id,
                  f"<< /Type /Font /Subtype /Type0 /BaseFont /EmbeddedFont "
                  f"/Encoding /Identity-H /DescendantFonts [{cidfont_id} 0 R] >>", None))
    parts.append((cidfont_id,
                  f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /EmbeddedFont "
                  f"/CIDSystemInfo {cidsysinfo_id} 0 R /FontDescriptor {desc_id} 0 R "
                  f"/CIDToGIDMap /Identity /DW 500 /W {w_id} 0 R >>", None))
    parts.append((cidsysinfo_id,
                  "<< /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>", None))
    parts.append((w_id, f"[ 0 [ {w_entries} ] ]", None))
    parts.append((desc_id,
                  f"<< /Type /FontDescriptor /FontName /EmbeddedFont /Flags 4 "
                  f"/FontBBox [-1000 -300 2000 1100] /ItalicAngle 0 /Ascent 800 "
                  f"/Descent -200 /CapHeight 700 /StemV 80 "
                  f"/FontFile2 {fontfile_id} 0 R >>", None))
    parts.append((fontfile_id,
                  f"<< /Length {len(font_bytes)} /Length1 {len(font_bytes)} >>",
                  font_bytes))
    for i, page in enumerate(pages):
        runs = []
        for (x, y, size, gids) in page:
            hexs = "".join("%04X" % g for g in gids)
            runs.append(f"BT /F1 {size} Tf {x} {y} Td <{hexs}> Tj ET")
        content = "\n".join(runs).encode("latin-1")
        parts.append((page_ids[i],
                      f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
                      f"/Resources << /Font << /F1 {font0_id} 0 R >> >> "
                      f"/Contents {content_ids[i]} 0 R >>", None))
        parts.append((content_ids[i],
                      f"<< /Length {len(content)} >>", content))

    parts.sort(key=lambda t: t[0])
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for (oid, header, stream) in parts:
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode("latin-1")
        out += header.encode("latin-1")
        if stream is not None:
            out += b"\nstream\n" + stream + b"\nendstream"
        out += b"\nendobj\n"
    xref_pos = len(out)
    n = len(parts) + 1
    out += f"xref\n0 {n}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for oid in range(1, n):
        out += f"{offsets[oid]:010d} 00000 n \n".encode("latin-1")
    out += (f"trailer\n<< /Size {n} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode("latin-1")
    return bytes(out)


def text_to_unicode_pdf(text):
    """Dependency-free, Latvian-capable PDF from PLAIN TEXT: embed a Unicode TTF and draw
    each wrapped line as real glyphs (Type0/Identity-H). Returns PDF bytes, or None if no
    Unicode font is on the host. Shared by the invoice fallback AND the Phase-5 reporting
    suite's PDF fallback (HTML→text→this), so neither re-implements the glyph machinery."""
    if not text:
        return None
    font_path = _find_fallback_font()
    if not font_path:
        log.warning("no Unicode TTF for the PDF fallback; cannot render Latvian")
        return None
    with open(font_path, "rb") as fh:
        font_bytes = fh.read()
    tables = _ttf_tables(font_bytes)
    cmap = _ttf_cmap_unicode(font_bytes, tables)
    upm, nglyphs, widths = _ttf_metrics(font_bytes, tables)
    notdef = 0

    def to_gids(s):
        return [cmap.get(ord(ch), notdef) for ch in s]

    # Lay out the monospace-ish text: wrap, paginate, draw each line as glyph ids. A fixed
    # leading; the embedded font carries the real advances so the text is proportionally
    # spaced (good enough for the plain fallback).
    import textwrap
    raw_lines = []
    for para in text.split("\n"):
        raw_lines.extend(textwrap.wrap(para, width=95) or [""])
    per_page, leading, x0, y0, size = 56, 13, 50, 800, 10
    chunks = [raw_lines[i:i + per_page] for i in range(0, len(raw_lines), per_page)] or [[""]]
    pages = []
    for chunk in chunks:
        runs = []
        y = y0
        for ln in chunk:
            runs.append((x0, y, size, to_gids(ln)))
            y -= leading
        pages.append(runs)
    return _pdf_unicode_doc(font_bytes, pages, lines_widths=(upm, nglyphs, widths))


def _invoice_pdf_fallback(invoice_id):
    """Dependency-free, Latvian-capable PDF: embed a Unicode TTF and draw invoice_text as
    real glyphs (Type0/Identity-H). Returns PDF bytes, or None if no Unicode font is on
    the host (we still NEVER fall back to the latin-1 `?` path)."""
    return text_to_unicode_pdf(invoice_text(invoice_id))


def invoice_pdf(invoice_id, lang=None):
    """Render the invoice to a print-ready PDF (bytes), Latvian/Unicode-correct.

    i18n: `lang` is passed through to invoice_html (the FIXED labels are localized; DB
    values are never translated). `lang=None` resolves the current request language;
    DEFAULT 'en' renders byte-identically to before.

    PRIMARY: the designed HTML/CSS template (invoice_html) rendered via the wkhtmltopdf
    CLI — UTF-8 + system fonts, so Latvian diacritics render natively and the invoice
    looks like a real, well-laid-out document. FALLBACK (no wkhtmltopdf on the host):
    a dependency-free PDF that EMBEDS a Unicode TrueType font (DejaVuSans) and writes the
    text as Unicode (Type0/Identity-H) — so `ā š ž ē ī ū ķ ļ ņ ģ č` are real glyphs, NEVER
    the latin-1 `?` the legacy shared text_to_pdf produced. A DRAFT is watermarked/labelled
    'DRAFT — not a valid invoice'. Returns the PDF bytes, or None if the invoice is
    unknown / both renderers are unavailable."""
    html = invoice_html(invoice_id, lang=lang)
    if html:
        try:
            data = _render_pdf_wkhtmltopdf(html)
            if data:
                return data
        except Exception as e:                          # never let the primary crash us
            log.warning("invoice_pdf(%s) wkhtmltopdf path failed: %s", invoice_id, e)
    # DEGRADE: Latvian-capable, dependency-free fallback (never the broken latin-1 path).
    try:
        return _invoice_pdf_fallback(invoice_id)
    except Exception as e:
        log.warning("invoice_pdf(%s) fallback failed: %s", invoice_id, e)
        return None


# ============================================================ PHASE 2: e-invoice (UBL)
# EN-16931 / PEPPOL BIS Billing 3.0 UBL 2.1 export of a SALES invoice. This is the
# OUTBOUND structured form Latvia mandates (B2G now, B2B from 2028; voluntary from
# Mar 2026) and the format the embedded hybrid PDF carries. It mirrors the shapes in
# `einvoice_export.py` (which exports REGISTERED inbound invoices) but reads the sales
# invoice's OWN data (invoices/invoice_lines + the issuer/customer snapshot).
#
# DATA -> EN-16931 BUSINESS-TERM MAPPING
#   cbc:CustomizationID  <- PEPPOL BIS Billing 3.0 customization URN     (BT-24)
#   cbc:ProfileID        <- PEPPOL billing process                       (BT-23)
#   cbc:ID               <- gap-free invoice number                      (BT-1)
#   cbc:IssueDate        <- issue date (ISO)                             (BT-2)
#   cbc:DueDate          <- payment due date                            (BT-9)
#   cbc:InvoiceTypeCode  <- 380 (commercial invoice)                    (BT-3)
#   cbc:DocumentCurrencyCode <- currency                                 (BT-5)
#   cbc:TaxCurrencyCode  <- 'EUR' when currency != EUR (VAT-in-EUR rule) (BT-6)
#   cbc:Note             <- reverse-charge / simplified wording          (BT-22)
#   cac:AccountingSupplierParty (BG-4): name BT-27, postal address BG-5, VAT id BT-31
#   cac:AccountingCustomerParty (BG-7): name BT-44, postal address BG-8, VAT id BT-48
#   cac:PaymentMeans/.../cbc:ID (IBAN)                                   (BG-16/BT-84)
#   cac:TaxTotal (BG-22): cbc:TaxAmount BT-110 (+ BT-111 in EUR for FX);
#       per-category cac:TaxSubtotal (BG-23): TaxableAmount BT-116, TaxAmount BT-117,
#       TaxCategory ID BT-118 + Percent BT-119 (+ ExemptionReason BT-120 when not S)
#   cac:LegalMonetaryTotal (BG-22): LineExtension BT-106, TaxExclusive BT-109,
#       TaxInclusive BT-112, Payable BT-115
#   cac:InvoiceLine (BG-25): ID BT-126, InvoicedQuantity BT-129/130, LineExtension BT-131,
#       Item/Name BT-153, ClassifiedTaxCategory BT-151/152, Price/PriceAmount BT-146
#
# REVERSE CHARGE: tax category 'AE', 0%, the mandatory exemption reason + the BT-22 note.
# Other zero-VAT lines on a normal invoice map to category 'Z' (zero-rated) with the
# exemption reason. Amounts via money.f2; XML escaping via ElementTree.

# UBL 2.1 namespaces (EN-16931 invoice syntax binding) — same set as einvoice_export.
_NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
# PEPPOL BIS Billing 3.0 / EN-16931 identifiers.
PEPPOL_CUSTOMIZATION_ID = ("urn:cen.eu:en16931:2017#compliant#"
                           "urn:fdc:peppol.eu:2017:poacc:billing:3.0")
PEPPOL_PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
_INVOICE_TYPE_CODE = "380"      # commercial invoice (BT-3)
_CREDIT_NOTE_TYPE_CODE = "381"  # credit note (BT-3) — UNTDID 1001 code 381
_TAX_SCHEME_VAT = "VAT"
_UNIT_DEFAULT = "C62"           # UN/ECE Rec 20 "one" (dimensionless)
_REVERSE_CHARGE_REASON = ("Reverse charge — VAT to be accounted for by the recipient "
                          "(Art. 196 Directive 2006/112/EC).")
_ZERO_RATE_REASON = "Zero-rated supply."


def _eq(tag):
    pref, local = tag.split(":", 1)
    return "{%s}%s" % (_NS[pref], local)


def _esub(parent, tag, text=None, attrib=None):
    el = ET.SubElement(parent, _eq(tag), attrib or {})
    if text is not None:
        el.text = str(text)
    return el


def _emoney(v):
    return f"{money.f2(v or 0):.2f}"


def _eamt(parent, tag, value, currency):
    return _esub(parent, tag, _emoney(value), {"currencyID": currency})


def _tax_category_for(rate, reverse_charge):
    """Map a (rate, reverse_charge) pair to a UBL tax-category code + an optional
    EN-16931 exemption-reason text. Standard-rated -> ('S', None); reverse charge ->
    ('AE', the reverse-charge reason); a plain 0% line on a normal invoice -> ('Z',
    the zero-rate reason). Percent is always emitted (0 for AE/Z)."""
    if reverse_charge:
        return "AE", _REVERSE_CHARGE_REASON
    if money.f2(rate or 0) == 0.0:
        return "Z", _ZERO_RATE_REASON
    return "S", None


def _party_block(root, role_tag, party):
    """Emit an AccountingSupplier/CustomerParty block: name (BT-27/44), postal address
    (BG-5/8) and the VAT PartyTaxScheme (BT-31/48). Missing values are simply omitted
    (the customer block on a SIMPLIFIED invoice legitimately carries less)."""
    apx = _esub(root, role_tag)
    party_el = _esub(apx, "cac:Party")
    name = (party.get("name") or "").strip()
    addr = (party.get("address") or "").strip()
    country = (party.get("country") or "").strip().upper()
    vat = (party.get("vat_number") or "").strip()
    # PostalAddress (BG-5 / BG-8). We carry the free-form address line + country code.
    pa = _esub(party_el, "cac:PostalAddress")
    if addr:
        _esub(pa, "cbc:StreetName", addr)
    ctry = _esub(pa, "cac:Country")
    if not country and len(vat) >= 2 and vat[:2].isalpha():
        country = vat[:2].upper()
    if country:
        _esub(ctry, "cbc:IdentificationCode", country)
    # PartyTaxScheme (BT-31 / BT-48) — only when a VAT id is present.
    if vat:
        pts = _esub(party_el, "cac:PartyTaxScheme")
        _esub(pts, "cbc:CompanyID", vat)
        ts = _esub(pts, "cac:TaxScheme")
        _esub(ts, "cbc:ID", _TAX_SCHEME_VAT)
    # PartyLegalEntity carries the registration name (BT-27 / BT-44 legal name).
    ple = _esub(party_el, "cac:PartyLegalEntity")
    _esub(ple, "cbc:RegistrationName", name or "")
    if (party.get("reg_no") or "").strip():
        _esub(ple, "cbc:CompanyID", str(party.get("reg_no")).strip())
    # PartyName (display name) — last so name is unambiguous to a lenient reader.
    pn = _esub(party_el, "cac:PartyName")
    _esub(pn, "cbc:Name", name or "")
    return apx


def einvoice_xml(invoice_id):
    """Build the EN-16931 / PEPPOL BIS Billing 3.0 UBL 2.1 Invoice for an ISSUED sales
    invoice. Returns the XML bytes (utf-8, with declaration). Raises ValueError for a
    DRAFT / unknown invoice (a draft has no legal number, so it has no e-invoice).

    NET basis per the invoice currency; per-line money.f2 quantization so the BG-23
    per-rate subtotals tie to the BG-22 totals. Reverse charge -> category 'AE' + 0% +
    the mandatory note. A foreign currency additionally states the VAT total in EUR
    (BT-6 TaxCurrencyCode + a second TaxAmount)."""
    v = _invoice_view(invoice_id)
    if not v:
        raise ValueError("invoice not found")
    inv, lines = v["invoice"], v["lines"]
    # An e-invoice exists for any NON-DRAFT (numbered) document — issued and every later
    # state (sent / partially_paid / paid). A draft has no legal number, so no e-invoice.
    if inv.get("status") == STATUS_DRAFT or not inv.get("number"):
        raise ValueError("only an ISSUED invoice has an e-invoice (a draft has no legal "
                         "number) — issue it first")
    # PROFORMA / QUOTE are NOT tax invoices — they have no EN-16931 e-invoice (they carry no
    # output VAT and must never be filed). Convert to an invoice first.
    if inv.get("doc_type") in NON_LEGAL_TYPES:
        raise ValueError("a proforma invoice / quote is not a tax invoice — it has no "
                         "e-invoice; convert it to an invoice first")
    issuer, customer = v["issuer"], v["customer"]
    currency = (inv.get("currency") or DEFAULT_CURRENCY).strip().upper()
    rc = bool(inv.get("reverse_charge"))
    # the document-discount allocation per rate (post-document-discount net/vat are in by_rate).
    by_rate = v["by_rate"]
    doc_disc_total = money.f2((v.get("totals") or {}).get("doc_discount") or 0)

    for pref, uri in _NS.items():
        ET.register_namespace("" if pref == "inv" else pref, uri)
    root = ET.Element(_eq("inv:Invoice"))

    _esub(root, "cbc:CustomizationID", PEPPOL_CUSTOMIZATION_ID)      # BT-24
    _esub(root, "cbc:ProfileID", PEPPOL_PROFILE_ID)                 # BT-23
    _esub(root, "cbc:ID", inv.get("number"))                       # BT-1
    _esub(root, "cbc:IssueDate", inv.get("issue_date") or "")       # BT-2
    if inv.get("due_date"):
        _esub(root, "cbc:DueDate", inv.get("due_date"))            # BT-9
    # BT-3 document type: 380 (commercial invoice) or 381 (credit note). A CREDIT NOTE also
    # carries a BillingReference to the ORIGINAL invoice (BG-3 / BT-25 + BT-26).
    is_cn = inv.get("doc_type") == DOC_CREDIT_NOTE
    _esub(root, "cbc:InvoiceTypeCode",
          _CREDIT_NOTE_TYPE_CODE if is_cn else _INVOICE_TYPE_CODE)  # BT-3
    if is_cn:
        orig = get_invoice(inv.get("corrects_invoice_id")) if inv.get("corrects_invoice_id") else None
        if orig and orig.get("number"):
            br = _esub(root, "cac:BillingReference")               # BG-3
            idr = _esub(br, "cac:InvoiceDocumentReference")
            _esub(idr, "cbc:ID", orig.get("number"))              # BT-25
            if orig.get("issue_date"):
                _esub(idr, "cbc:IssueDate", orig.get("issue_date"))  # BT-26
    # BT-22 document note(s): reverse-charge / simplified wording.
    if rc:
        _esub(root, "cbc:Note", _REVERSE_CHARGE_REASON)
    if inv.get("simplified"):
        _esub(root, "cbc:Note",
              f"Simplified invoice (gross <= EUR {SIMPLIFIED_GROSS_CEILING_EUR:.0f}, "
              "EU VAT Dir. Art. 238).")
    _esub(root, "cbc:DocumentCurrencyCode", currency)             # BT-5
    # BT-6 VAT accounting currency: 'EUR' when the document currency is not EUR, so the
    # VAT total can be (and is) also stated in EUR below (LV/EU rule).
    eur_vat = None
    if currency != "EUR":
        eur_vat, _fx, _src = vat_total_eur(inv)
        if eur_vat is not None:
            _esub(root, "cbc:TaxCurrencyCode", "EUR")             # BT-6

    # parties (BG-4 / BG-7)
    _party_block(root, "cac:AccountingSupplierParty", issuer)
    _party_block(root, "cac:AccountingCustomerParty", customer)

    # payment means + IBAN (BG-16 / BT-84) when the issuer carries an IBAN.
    iban = (issuer.get("iban") or "").strip()
    if iban:
        pm = _esub(root, "cac:PaymentMeans")
        _esub(pm, "cbc:PaymentMeansCode", "30")   # credit transfer
        fa = _esub(pm, "cac:PayeeFinancialAccount")
        _esub(fa, "cbc:ID", iban)
        if (issuer.get("bank") or "").strip():
            _esub(fa, "cbc:Name", str(issuer.get("bank")).strip())

    # ----- DOCUMENT-level AllowanceCharge (BG-20) for an invoice-level discount -----
    # The invoice-level discount is allocated per VAT rate (each rate's taxable net is reduced
    # proportionally — VAT on the post-discount net). EN-16931 carries this as one document
    # AllowanceCharge PER RATE (ChargeIndicator false, BT-92 amount, BT-93 base = the rate's
    # pre-document-discount net, BT-95 TaxCategory). BT-107 (sum of allowances) ties out to
    # the total document discount; BT-109 (TaxExclusive) is net AFTER allowances.
    # `by_rate` carries each rate's POST-document-discount net/vat + the `alloc` removed.
    # Build a rate->pre-document-discount-net map from the line totals for the allowance base.
    pre_doc_net_by_rate = {}
    for ln in lines:
        r = 0.0 if rc else money.f2(ln.get("vat_rate") or 0)
        pre_doc_net_by_rate[r] = money.f2(
            (pre_doc_net_by_rate.get(r) or 0) + money.f2(ln.get("line_net") or 0))
    if doc_disc_total > 0:
        for b in by_rate:
            alloc = money.f2(b.get("alloc") or 0)
            if alloc <= 0:
                continue
            rate = 0.0 if rc else money.f2(b.get("rate") or 0)
            cat, reason = _tax_category_for(rate, rc)
            ac = _esub(root, "cac:AllowanceCharge")              # BG-20
            _esub(ac, "cbc:ChargeIndicator", "false")            # allowance (not a charge)
            _esub(ac, "cbc:AllowanceChargeReason", "Document discount")  # BT-97
            _eamt(ac, "cbc:Amount", alloc, currency)             # BT-92
            _eamt(ac, "cbc:BaseAmount",
                  pre_doc_net_by_rate.get(rate, 0), currency)    # BT-93
            tc = _esub(ac, "cac:TaxCategory")                    # BT-95 category
            _esub(tc, "cbc:ID", cat)
            _esub(tc, "cbc:Percent", f"{rate * 100:g}")
            if reason:
                _esub(tc, "cbc:TaxExemptionReason", reason)
            ts = _esub(tc, "cac:TaxScheme")
            _esub(ts, "cbc:ID", _TAX_SCHEME_VAT)

    # ----- TaxTotal (BG-22) + per-category subtotals (BG-23) -----
    # The TaxSubtotals are the POST-document-discount per-rate nets/VATs (from by_rate), so
    # the VAT is on the post-discount taxable amount and the totals tie out.
    by_cat = {}
    for b in by_rate:
        rate = 0.0 if rc else money.f2(b.get("rate") or 0)
        cat, reason = _tax_category_for(rate, rc)
        key = (cat, rate)
        bucket = by_cat.setdefault(key, {"net": [], "vat": [], "reason": reason})
        bucket["net"].append(money.f2(b.get("net") or 0))
        bucket["vat"].append(money.f2(b.get("vat") or 0))
    tax_total = _esub(root, "cac:TaxTotal")
    doc_vat = money.fsum([money.f2(b.get("vat") or 0) for b in by_rate])
    _eamt(tax_total, "cbc:TaxAmount", doc_vat, currency)           # BT-110
    for (cat, rate), b in sorted(by_cat.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        sub = _esub(tax_total, "cac:TaxSubtotal")
        _eamt(sub, "cbc:TaxableAmount", money.fsum(b["net"]), currency)   # BT-116
        _eamt(sub, "cbc:TaxAmount", money.fsum(b["vat"]), currency)       # BT-117
        tc = _esub(sub, "cac:TaxCategory")
        _esub(tc, "cbc:ID", cat)                                  # BT-118
        _esub(tc, "cbc:Percent", f"{rate * 100:g}")              # BT-119
        if b["reason"]:
            _esub(tc, "cbc:TaxExemptionReason", b["reason"])     # BT-120
        ts = _esub(tc, "cac:TaxScheme")
        _esub(ts, "cbc:ID", _TAX_SCHEME_VAT)
    # BT-111: VAT total in the accounting currency (EUR) for a foreign-currency invoice.
    if currency != "EUR" and eur_vat is not None:
        tt_eur = _esub(root, "cac:TaxTotal")
        _eamt(tt_eur, "cbc:TaxAmount", eur_vat, "EUR")            # BT-111

    # ----- LegalMonetaryTotal (BG-22) -----
    # BT-106 LineExtension = Σ line net (post LINE discount, PRE document discount).
    # BT-107 AllowanceTotal = the document discount (sum of document allowances).
    # BT-109 TaxExclusive = BT-106 − BT-107 (post BOTH discounts) = the by_rate net total.
    line_ext_total = money.fsum([money.f2(ln.get("line_net") or 0) for ln in lines])
    net_total = money.fsum([money.f2(b.get("net") or 0) for b in by_rate])
    lmt = _esub(root, "cac:LegalMonetaryTotal")
    _eamt(lmt, "cbc:LineExtensionAmount", line_ext_total, currency)   # BT-106
    if doc_disc_total > 0:
        _eamt(lmt, "cbc:AllowanceTotalAmount", doc_disc_total, currency)  # BT-107
    _eamt(lmt, "cbc:TaxExclusiveAmount", net_total, currency)      # BT-109
    gross = money.f2(net_total + doc_vat)
    _eamt(lmt, "cbc:TaxInclusiveAmount", gross, currency)         # BT-112
    _eamt(lmt, "cbc:PayableAmount", gross, currency)              # BT-115

    # ----- InvoiceLine (BG-25) -----
    for i, ln in enumerate(lines, 1):
        rate = 0.0 if rc else money.f2(ln.get("vat_rate") or 0)
        cat, _reason = _tax_category_for(rate, rc)
        il = _esub(root, "cac:InvoiceLine")
        _esub(il, "cbc:ID", str(i))                              # BT-126
        qty = ln.get("quantity")
        unit = (ln.get("unit") or "").strip() or _UNIT_DEFAULT
        try:
            qty_text = f"{float(qty):g}"
        except (TypeError, ValueError):
            qty_text = "1"
        _esub(il, "cbc:InvoicedQuantity", qty_text,
              {"unitCode": _ubl_unit_code(unit)})                 # BT-129/130
        # BT-131 line net = (qty × price) − line allowances (post LINE discount). The line
        # discount, when present, is a LINE AllowanceCharge (BG-27/28).
        _eamt(il, "cbc:LineExtensionAmount", ln.get("line_net"), currency)  # BT-131
        line_disc = money.f2(ln.get("discount_amount") or 0)
        if line_disc > 0:
            lac = _esub(il, "cac:AllowanceCharge")               # BG-27/28
            _esub(lac, "cbc:ChargeIndicator", "false")           # allowance (not a charge)
            _esub(lac, "cbc:AllowanceChargeReason", "Line discount")  # BT-139
            _eamt(lac, "cbc:Amount", line_disc, currency)        # BT-136
            _eamt(lac, "cbc:BaseAmount",
                  ln.get("gross_amount"), currency)              # BT-137 (pre-discount net)
        item = _esub(il, "cac:Item")
        _esub(item, "cbc:Name", (ln.get("description") or "Item"))         # BT-153
        ctc = _esub(item, "cac:ClassifiedTaxCategory")
        _esub(ctc, "cbc:ID", cat)                                # BT-151
        _esub(ctc, "cbc:Percent", f"{rate * 100:g}")            # BT-152
        cts = _esub(ctc, "cac:TaxScheme")
        _esub(cts, "cbc:ID", _TAX_SCHEME_VAT)
        price = _esub(il, "cac:Price")
        _eamt(price, "cbc:PriceAmount", ln.get("unit_price_net"), currency)  # BT-146

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# Map a stored unit token to a UN/ECE Rec 20 code where we recognise it; else pass the
# token through (a free-form unit is still a valid unitCode for a lenient reader).
_UNIT_MAP = {"h": "HUR", "hr": "HUR", "hour": "HUR", "l": "LTR", "ltr": "LTR",
             "pcs": "C62", "pc": "C62", "ea": "C62", "kg": "KGM", "km": "KMT",
             "day": "DAY", "month": "MON"}


def _ubl_unit_code(unit):
    u = (unit or "").strip()
    return _UNIT_MAP.get(u.lower(), u or _UNIT_DEFAULT)


def einvoice_filename(invoice_id):
    """A filesystem-safe download name for the e-invoice XML of an ISSUED invoice (or
    credit note)."""
    inv = get_invoice(invoice_id)
    num = (inv.get("number") if inv else None) or f"draft-{invoice_id}"
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(num)).strip("_") or "invoice"
    prefix = "CreditNote" if (inv and inv.get("doc_type") == DOC_CREDIT_NOTE) else "Invoice"
    return f"{prefix}_{safe}.xml"


# ============================================================ PHASE 2: hybrid PDF
# Factur-X/ZUGFeRD-style hybrid: the Phase-1 compliant PDF with the UBL XML embedded as
# `factur-x.xml` (the name our OWN reader, extract._FACTURX_NAMES, probes for), so the
# document round-trips back into a draft through extract.parse_einvoice. pikepdf does the
# embed; if it is unavailable we DEGRADE GRACEFULLY to the plain PDF (the route then offers
# the XML as a separate download) — we never crash.
FACTURX_ATTACHMENT_NAME = "factur-x.xml"


def invoice_pdf_hybrid(invoice_id):
    """Return hybrid-PDF bytes: the Phase-1 compliant PDF with the EN-16931 UBL XML
    embedded as `factur-x.xml` (AFRelationship Alternative, mime text/xml). Raises
    ValueError for a draft / unknown invoice (no legal e-invoice). If pikepdf is
    unavailable the embed DEGRADES to the plain PDF (best-effort) — never crashes."""
    xml_bytes = einvoice_xml(invoice_id)        # raises for a draft/unknown
    pdf_bytes = invoice_pdf(invoice_id)
    if not pdf_bytes:
        raise ValueError("could not render the base PDF")
    try:
        import pikepdf
    except Exception as e:
        log.warning("pikepdf unavailable - hybrid PDF degrades to plain PDF: %s", e)
        return pdf_bytes
    try:
        return _embed_facturx(pdf_bytes, xml_bytes)
    except Exception as e:
        log.warning("invoice_pdf_hybrid(%s) embed failed - returning plain PDF: %s",
                    invoice_id, e)
        return pdf_bytes


def _embed_facturx(pdf_bytes, xml_bytes):
    """Embed `xml_bytes` into `pdf_bytes` as the Factur-X `factur-x.xml` attachment via
    pikepdf: an AFRelationship=Alternative associated file in the EmbeddedFiles name tree,
    mime text/xml. Returns the new PDF bytes. Raises on a pikepdf error (caller degrades)."""
    import pikepdf
    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    try:
        af = pikepdf.AttachedFileSpec(pdf, xml_bytes, mime_type="text/xml",
                                      description="EN-16931 e-invoice (PEPPOL BIS 3.0)")
        # AFRelationship 'Alternative' = the XML is an alternative representation of the
        # PDF (the Factur-X convention for the embedded structured invoice).
        try:
            af.relationship = pikepdf.Name.Alternative
        except Exception:
            pass
        pdf.attachments[FACTURX_ATTACHMENT_NAME] = af
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()
    finally:
        pdf.close()


# ============================================================ PHASE 4: email the invoice
# EMAIL: send the ISSUED invoice (or credit note) to the customer, attaching the HYBRID PDF
# (the human+machine-readable Factur-X document) PLUS the standalone EN-16931 e-invoice XML.
# Reuses notify.py's injectable SMTP transport (notify._settings_transport()) so no live
# SMTP is touched in tests (a fake transport is injected). On success the status moves to
# `sent` (= issued + emailed) and the send is recorded (when + to whom). Best-effort: never
# raises; returns (ok, error) with a clear message when SMTP isn't configured or the
# customer has no email.

def _cover_note(invoice_id, lang=None):
    """The short cover-note body (plain_text, escaped_html) in the document's language for
    the customer email. Fixed labels via i18n.t; the invoice number/issuer name are
    HTML-escaped in the html part. Pure-ish; never raises."""
    import i18n
    L = i18n.normalize(lang) if lang is not None else i18n.current_lang()

    def _t(s):
        return i18n.t(s, L)

    v = _invoice_view(invoice_id) or {}
    inv = v.get("invoice") or {}
    issuer = v.get("issuer") or {}
    is_cn = v.get("is_credit_note")
    num = inv.get("number") or ""
    head = (_t("Credit note %s") if is_cn else _t("Invoice %s")) % num
    intro = _t("Please find the invoice attached (PDF and e-invoice XML).")
    sign = issuer.get("name") or ""
    text = "\n".join([head, "", intro, "", sign])
    html = (f"<p>{esc(head)}</p><p>{esc(intro)}</p>"
            + (f"<p>{esc(sign)}</p>" if sign else ""))
    return text, html, head


def _invoice_recipient_email(invoice_id):
    """The customer's email for this invoice — from the SNAPSHOT once issued (so a later
    customer-book edit never re-targets a filed invoice), else the live customer book.
    Returns '' when none is known. Never raises."""
    import json
    inv = get_invoice(invoice_id) or {}
    snap = inv.get("customer_snapshot")
    if snap:
        try:
            d = json.loads(snap)
            em = (d.get("email") or "").strip()
            if em:
                return em
        except Exception as e:
            log.warning("recipient email: bad customer snapshot for %s: %s", invoice_id, e)
    cust = get_customer(inv.get("customer_id")) if inv.get("customer_id") else None
    return ((cust or {}).get("email") or "").strip()


def send_invoice(invoice_id, to=None, lang=None, transport=None):
    """Email an ISSUED invoice (or credit note) to the customer, attaching the HYBRID PDF
    and the standalone e-invoice XML. `to` overrides the customer's stored email; `lang`
    sets the cover-note language (defaults to the current request language). `transport`
    (a notify-shaped object with .send(to, subject, html, text, attachments)) is injected
    by tests; in production it is built from admin SMTP settings.

    Only an issued (non-draft) document may be emailed (a draft has no legal number). On a
    successful send the status moves to `sent` (= issued + emailed; never regresses a
    paid/partially-paid invoice) and the send is recorded (sent_at + sent_to). Returns
    (True, "") or (False, error). Best-effort: never raises."""
    inv = get_invoice(invoice_id)
    if not inv:
        return False, "invoice not found"
    if inv.get("status") == STATUS_DRAFT or not inv.get("number"):
        return False, "only an issued invoice can be emailed — issue it first"
    recipient = (to or "").strip() or _invoice_recipient_email(invoice_id)
    if not recipient:
        return False, "the customer has no email address"

    # build the attachments: the hybrid PDF (PDF + embedded e-invoice) + the standalone XML.
    try:
        hybrid = invoice_pdf_hybrid(invoice_id)
        xml = einvoice_xml(invoice_id)
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        log.exception("send_invoice(%s): could not build attachments", invoice_id)
        return False, f"could not build the invoice documents ({str(e)[:80]})"
    if not hybrid:
        return False, "could not render the invoice PDF"
    num = (inv.get("number") or f"doc-{invoice_id}").replace("/", "-")
    prefix = "CreditNote" if inv.get("doc_type") == DOC_CREDIT_NOTE else "Invoice"
    attachments = [
        (f"{prefix}_{num}_hybrid.pdf", "application/pdf", hybrid),
        (einvoice_filename(invoice_id), "application/xml", xml),
    ]

    # resolve the transport (reuse notify's injectable SMTP transport).
    import notify
    if transport is None:
        transport = notify._settings_transport()
    if transport is None:
        return False, "email is not configured (set up the SMTP relay)"

    text, html, subject = _cover_note(invoice_id, lang=lang)
    try:
        transport.send(recipient, subject, html, text, attachments=attachments)
    except TypeError:
        # a legacy transport without the attachments kwarg — send without attachments
        # rather than fail outright (best-effort), then surface that limitation.
        try:
            transport.send(recipient, subject, html, text)
        except Exception as e:
            log.warning("send_invoice(%s) transport failed: %s", invoice_id, e)
            return False, f"could not send the email ({str(e)[:80]})"
    except Exception as e:
        log.warning("send_invoice(%s) transport failed: %s", invoice_id, e)
        return False, f"could not send the email ({str(e)[:80]})"

    # record the send: status -> sent (don't regress paid/partially_paid), sent_at/sent_to.
    try:
        when = datetime.datetime.utcnow().isoformat(timespec="seconds")
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            # only move issued -> sent; a partially_paid/paid invoice keeps its status but
            # still records that it was (re-)emailed.
            if inv.get("status") == STATUS_ISSUED:
                con.execute("UPDATE invoices SET status=?, sent_at=?, sent_to=? "
                            "WHERE id=?" + frag,
                            [STATUS_SENT, when, recipient, invoice_id, *tp])
            else:
                con.execute("UPDATE invoices SET sent_at=?, sent_to=? WHERE id=?" + frag,
                            [when, recipient, invoice_id, *tp])
            con.commit()
        finally:
            con.close()
    except Exception as e:
        # the email DID go out — log the bookkeeping failure but report success so the
        # operator isn't told the send failed when it didn't.
        log.warning("send_invoice(%s): email sent but status update failed: %s",
                    invoice_id, e)
    return True, ""


# ============================================================ PHASE 6: recurring invoices
# A RECURRING TEMPLATE auto-generates invoices on a schedule (e.g. monthly fuel-card
# billing). It is structurally a SCHEDULE + a line SPEC over the existing compose/issue
# machinery — it stores NO invoice figures and mutates NO filed invoice. At each due date
# `generate_due` builds a DRAFT (or, with auto_issue, an ISSUED) invoice from the spec via
# the same create_draft/add_line/issue path. The schedule cursor `next_run` advances
# MONTH-END SAFE (see `_add_months`), so a Jan-31 monthly recurring lands Feb-28/29 and
# never skips a month. Generation is IDEMPOTENT per (template, due-date) via the unique
# recurring_runs index, AUDITED, and NEVER-RAISE (one bad template can't block the others).
import json as _json


def _add_months(d, months):
    """Return `d` shifted by `months` calendar months, MONTH-END SAFE: if the target month
    has no matching day (e.g. Jan-31 + 1 month) we clamp to that month's last day (Feb-28/
    29), so monthly billing never skips a month or overflows into the next. Pure."""
    total = (d.year * 12 + (d.month - 1)) + months
    y, m = divmod(total, 12)
    m += 1
    # last day of the target month
    if m == 12:
        last = 31
    else:
        last = (datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)).day
    return datetime.date(y, m, min(d.day, last))


def _as_date(v, default=None):
    """Parse a YYYY-MM-DD into a date (None/blank -> default). Best-effort, never raises."""
    if not v:
        return default
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return default


def advance_next_run(template, from_date):
    """The NEXT occurrence date after `from_date` for a template's frequency/interval.
    Month-based cadences (monthly/quarterly/yearly) use `_add_months` (month-end safe);
    weekly adds 7*interval days. `from_date` is a date or YYYY-MM-DD string. Pure, never
    raises (an unknown frequency falls back to monthly)."""
    base = _as_date(from_date) or datetime.date.today()
    try:
        n = max(1, int(template.get("interval_n") or 1))
    except (TypeError, ValueError):
        n = 1
    freq = (template.get("frequency") or FREQ_MONTHLY).strip().lower()
    if freq == FREQ_WEEKLY:
        return base + datetime.timedelta(days=7 * n)
    months = _FREQ_MONTHS.get(freq, 1) * n
    return _add_months(base, months)


def _rec_dict(row):
    d = dict(row)
    d["auto_issue"] = bool(d.get("auto_issue"))
    try:
        d["lines"] = _json.loads(d.get("lines") or "[]")
    except (TypeError, ValueError):
        d["lines"] = []
    return d


def _normalize_rec_lines(lines):
    """Coerce a caller-supplied list of line dicts into the stored shape (description,
    quantity, unit, unit_price_net, vat_rate). Returns a clean list (drops empties)."""
    out = []
    for ln in (lines or []):
        desc = (str(ln.get("description") or "")).strip()
        try:
            qty = float(money.D(ln.get("quantity") or 0))
        except Exception:
            qty = 0.0
        try:
            price = float(money.D(ln.get("unit_price_net") or 0))
        except Exception:
            price = 0.0
        try:
            rate = float(ln.get("vat_rate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        if rate > 1:               # tolerate a percent (21 -> 0.21)
            rate = rate / 100.0
        if not desc and qty == 0 and price == 0:
            continue
        out.append({"description": desc, "quantity": qty,
                    "unit": (str(ln.get("unit") or "")).strip(),
                    "unit_price_net": price, "vat_rate": rate})
    return out


def create_recurring(*, customer_id, lines, frequency=FREQ_MONTHLY, interval_n=1,
                     start_date=None, end_date=None, max_occurrences=None,
                     auto_issue=False, currency=DEFAULT_CURRENCY, name="", notes="",
                     created_by=None):
    """Create a recurring template. The first due date is `start_date` (defaults to today).
    `lines` is a list of line dicts. Returns (template_dict, "") or (None, error)."""
    freq = (frequency or FREQ_MONTHLY).strip().lower()
    if freq not in FREQUENCIES:
        return None, f"unknown frequency {freq!r}"
    try:
        n = max(1, int(interval_n or 1))
    except (TypeError, ValueError):
        n = 1
    sd = _as_date(start_date) or datetime.date.today()
    norm = _normalize_rec_lines(lines)
    if not norm:
        return None, "a recurring template needs at least one line"
    try:
        mo = int(max_occurrences) if str(max_occurrences or "").strip() else None
    except (TypeError, ValueError):
        mo = None
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO recurring_templates
                   (name, customer_id, currency, lines, frequency, interval_n,
                    start_date, next_run, end_date, max_occurrences, auto_issue,
                    status, notes, created_by, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ((name or "").strip(), customer_id,
                 (currency or DEFAULT_CURRENCY).strip().upper(),
                 _json.dumps(norm, ensure_ascii=False), freq, n,
                 sd.isoformat(), sd.isoformat(),
                 (_as_date(end_date).isoformat() if _as_date(end_date) else None),
                 mo, 1 if auto_issue else 0, REC_ACTIVE, (notes or "").strip(),
                 created_by, tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM recurring_templates WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (_rec_dict(row) if row else None), ""
    except Exception as e:
        log.exception("create_recurring failed")
        return None, f"could not create recurring template ({str(e)[:80]})"


def get_recurring(template_id):
    """One recurring template (tenant-scoped), or None. Never raises."""
    if not template_id:
        return None
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            row = con.execute("SELECT * FROM recurring_templates WHERE id=?" + frag,
                              [template_id, *tp]).fetchone()
        finally:
            con.close()
        return _rec_dict(row) if row else None
    except Exception as e:
        log.warning("get_recurring(%s) failed: %s", template_id, e)
        return None


def list_recurring(status=None):
    """All recurring templates for the current tenant (active first, then next_run).
    Joins the customer name for display. Optional status filter. Never raises -> []."""
    frag, tp = tenancy.scope_clause(column="r.tenant_id")
    where = ["1=1"]
    params = []
    if status:
        where.append("r.status=?")
        params.append(status)
    sql = ("SELECT r.*, c.name AS customer_name FROM recurring_templates r "
           "LEFT JOIN bill_customers c ON c.id=r.customer_id "
           "WHERE " + " AND ".join(where) + frag
           + " ORDER BY (r.status='active') DESC, r.next_run, r.id")
    try:
        con = connect()
        try:
            rows = con.execute(sql, [*params, *tp]).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            d = _rec_dict(r)
            d["customer_name"] = r["customer_name"]
            out.append(d)
        return out
    except Exception as e:
        log.warning("list_recurring failed: %s", e)
        return []


def update_recurring(template_id, **fields):
    """Update editable fields of a recurring template (tenant-scoped). Recognises name,
    customer_id, currency, lines (list), frequency, interval_n, start_date, end_date,
    max_occurrences, auto_issue, notes. Returns (template_dict, "") or (None, error).
    NOTE: editing start_date does NOT rewind next_run (use the cursor as-is); set next_run
    explicitly via this call if you must re-seed the schedule."""
    allowed = ("name", "customer_id", "currency", "lines", "frequency", "interval_n",
               "start_date", "next_run", "end_date", "max_occurrences", "auto_issue",
               "notes", "status")
    sets, params = [], []
    for k in allowed:
        if k not in fields:
            continue
        v = fields[k]
        if k == "lines":
            v = _json.dumps(_normalize_rec_lines(v), ensure_ascii=False)
        elif k == "currency" and v:
            v = str(v).strip().upper()
        elif k == "frequency":
            v = (str(v) or FREQ_MONTHLY).strip().lower()
            if v not in FREQUENCIES:
                return None, f"unknown frequency {v!r}"
        elif k == "interval_n":
            try:
                v = max(1, int(v or 1))
            except (TypeError, ValueError):
                v = 1
        elif k in ("start_date", "next_run", "end_date"):
            dv = _as_date(v)
            v = dv.isoformat() if dv else None
        elif k == "max_occurrences":
            try:
                v = int(v) if str(v or "").strip() else None
            except (TypeError, ValueError):
                v = None
        elif k == "auto_issue":
            v = 1 if v else 0
        elif isinstance(v, str):
            v = v.strip()
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return get_recurring(template_id), ""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            con.execute(f"UPDATE recurring_templates SET {', '.join(sets)} WHERE id=?"
                        + frag, [*params, template_id, *tp])
            con.commit()
        finally:
            con.close()
        return get_recurring(template_id), ""
    except Exception as e:
        log.exception("update_recurring(%s) failed", template_id)
        return None, f"could not update recurring template ({str(e)[:80]})"


def pause_recurring(template_id):
    """Pause a template (skipped by generate_due). Returns (template_dict, "") / (None,err)."""
    return update_recurring(template_id, status=REC_PAUSED)


def resume_recurring(template_id):
    """Resume a paused template. Returns (template_dict, "") / (None, err)."""
    return update_recurring(template_id, status=REC_ACTIVE)


def delete_recurring(template_id):
    """Delete a recurring template (tenant-scoped). Generated invoices are untouched (they
    are independent legal/draft documents). Returns (True, "") or (False, error)."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            con.execute("DELETE FROM recurring_templates WHERE id=?" + frag,
                        [template_id, *tp])
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("delete_recurring(%s) failed", template_id)
        return False, f"could not delete recurring template ({str(e)[:80]})"


def _rec_exhausted(t):
    """True if a template has reached its max_occurrences cap."""
    mo = t.get("max_occurrences")
    if mo in (None, "", 0):
        return False
    try:
        return int(t.get("occurrences_done") or 0) >= int(mo)
    except (TypeError, ValueError):
        return False


def _rec_past_end(t, on_date):
    """True if `on_date` is strictly past the template's end_date (inclusive end)."""
    ed = _as_date(t.get("end_date"))
    return bool(ed and on_date > ed)


def due_templates(today=None):
    """The ACTIVE templates whose next_run <= today and which are not past their end_date
    / max_occurrences. `today` defaults to the system date. Read-only; never raises -> []."""
    today = _as_date(today) or datetime.date.today()
    out = []
    for t in list_recurring(status=REC_ACTIVE):
        nr = _as_date(t.get("next_run"))
        if not nr or nr > today:
            continue
        if _rec_exhausted(t) or _rec_past_end(t, nr):
            continue
        out.append(t)
    return out


def recurring_runs_for(template_id):
    """The generation log rows for a template (newest first), tenant-scoped. -> []."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT * FROM recurring_runs WHERE template_id=?" + frag
                + " ORDER BY id DESC", [template_id, *tp]).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("recurring_runs_for(%s) failed: %s", template_id, e)
        return []


def invoices_from_template(template_id):
    """Invoices generated from a template (newest first), tenant-scoped. -> []."""
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT * FROM invoices WHERE from_template_id=?" + frag
                + " ORDER BY id DESC", [template_id, *tp]).fetchall()
        finally:
            con.close()
        return [_inv_dict(r) for r in rows]
    except Exception as e:
        log.warning("invoices_from_template(%s) failed: %s", template_id, e)
        return []


def _claim_run(template_id, period):
    """IDEMPOTENCY GUARD: try to claim a (template, period) generation slot by INSERTing a
    recurring_runs row. Returns the new run id, or None if the slot is already taken (the
    unique index collides -> this period was already generated). Never raises -> None on a
    real error (caller treats as 'skip')."""
    try:
        con = connect()
        try:
            cur = con.execute(
                "INSERT OR IGNORE INTO recurring_runs "
                "(template_id, period, status, tenant_id) VALUES (?,?,?,?)",
                (template_id, period, "pending", tenancy.write_tenant()))
            con.commit()
            if cur.rowcount == 0:
                return None                       # slot already claimed -> idempotent skip
            return cur.lastrowid
        finally:
            con.close()
    except Exception as e:
        log.warning("_claim_run(%s,%s) failed: %s", template_id, period, e)
        return None


def _finish_run(run_id, *, invoice_id, status, message=""):
    """Record the outcome of a claimed generation slot."""
    if not run_id:
        return
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            con.execute("UPDATE recurring_runs SET invoice_id=?, status=?, message=? "
                        "WHERE id=?" + frag,
                        [invoice_id, status, (message or "")[:200], run_id, *tp])
            con.commit()
        finally:
            con.close()
    except Exception as e:
        log.warning("_finish_run(%s) failed: %s", run_id, e)


def _generate_one(template, today):
    """Generate the invoice for ONE due template at its current next_run, then advance the
    schedule. Returns (invoice_id_or_None, status) where status is 'draft'/'issued'/
    'skipped'/'error'. Idempotent per (template, period) via _claim_run. Never raises."""
    tid = int(template["id"])
    period = (_as_date(template.get("next_run")) or today).isoformat()
    run_id = _claim_run(tid, period)
    if run_id is None:
        return None, "skipped"           # already generated for this period (idempotent)
    try:
        inv, err = create_draft(customer_id=template.get("customer_id"),
                                currency=template.get("currency") or DEFAULT_CURRENCY,
                                supply_date=period,
                                notes=template.get("notes") or "",
                                created_by="recurring")
        if err or not inv:
            _finish_run(run_id, invoice_id=None, status="error",
                        message=err or "draft failed")
            return None, "error"
        iid = int(inv["id"])
        # stamp the source-template linkage on the generated invoice (traceability)
        try:
            con = connect()
            try:
                con.execute("UPDATE invoices SET from_template_id=? WHERE id=?",
                            (tid, iid))
                con.commit()
            finally:
                con.close()
        except Exception as e:
            log.warning("_generate_one(%s): linkage stamp failed: %s", tid, e)
        for ln in (template.get("lines") or []):
            _i, lerr = add_line(iid, description=ln.get("description") or "",
                                quantity=ln.get("quantity") or 0,
                                unit=ln.get("unit") or "",
                                unit_price_net=ln.get("unit_price_net") or 0,
                                vat_rate=ln.get("vat_rate") or 0)
            if lerr:
                log.warning("_generate_one(%s): add_line failed: %s", tid, lerr)
        status = "draft"
        if template.get("auto_issue"):
            issued, ierr = issue(iid, issued_by="recurring", issue_date=period)
            if ierr:
                # leave it as a draft (still a valid generated document); record the reason
                _finish_run(run_id, invoice_id=iid, status="draft",
                            message=f"auto-issue failed: {ierr}")
            else:
                status = "issued"
        _finish_run(run_id, invoice_id=iid, status=status)
        # advance the schedule cursor + bump occurrences, deactivating when exhausted.
        _advance_template_after_run(template, today)
        return iid, status
    except Exception as e:
        log.exception("_generate_one(%s) failed", tid)
        _finish_run(run_id, invoice_id=None, status="error", message=str(e)[:200])
        return None, "error"


def _advance_template_after_run(template, today):
    """After a successful generation: bump occurrences_done, move next_run to the next
    occurrence, and PAUSE the template when it hits max_occurrences or rolls past end_date.
    `today` only bounds nothing here — the cursor advances by exactly one period from the
    period just generated, so a long-idle scheduler catches up one invoice per run."""
    tid = int(template["id"])
    cur_run = _as_date(template.get("next_run")) or today
    nxt = advance_next_run(template, cur_run)
    done = int(template.get("occurrences_done") or 0) + 1
    # decide whether the template is now exhausted (max occurrences) or past its end date.
    exhausted = False
    mo = template.get("max_occurrences")
    if mo not in (None, "", 0):
        try:
            exhausted = done >= int(mo)
        except (TypeError, ValueError):
            exhausted = False
    ed = _as_date(template.get("end_date"))
    if ed and nxt > ed:
        exhausted = True
    new_status = REC_PAUSED if exhausted else template.get("status") or REC_ACTIVE
    frag, tp = tenancy.scope_clause()
    try:
        con = connect()
        try:
            con.execute("UPDATE recurring_templates SET next_run=?, occurrences_done=?, "
                        "status=? WHERE id=?" + frag,
                        [nxt.isoformat(), done, new_status, tid, *tp])
            con.commit()
        finally:
            con.close()
    except Exception as e:
        log.warning("_advance_template_after_run(%s) failed: %s", tid, e)


def generate_due(today=None):
    """Generate invoices for every DUE recurring template. For each, compose a DRAFT (or an
    ISSUED invoice when auto_issue) from the template, link it back to the template + period,
    advance the schedule cursor and bump occurrences (deactivating at end_date/max). IDEMPOTENT
    per (template, due-date) and NEVER-RAISE: a failure on one template is logged and skipped,
    it never blocks the others. Returns a list of (template_id, invoice_id_or_None, status)."""
    today = _as_date(today) or datetime.date.today()
    out = []
    for t in due_templates(today):
        try:
            iid, status = _generate_one(t, today)
        except Exception as e:        # belt-and-braces; _generate_one already guards
            log.exception("generate_due: template %s failed", t.get("id"))
            iid, status = None, "error"
        out.append((int(t["id"]), iid, status))
    return out


if __name__ == "__main__":
    # inline smoke test (the project convention)
    DB = ":memory:"
    _SCHEMA_READY = set()
    tot = compute_totals([
        {"quantity": 2, "unit_price_net": 10, "vat_rate": 0.21},
        {"quantity": 1, "unit_price_net": 100, "vat_rate": 0.09},
    ])
    assert tot["net_total"] == 120.0, tot
    assert tot["vat_total"] == 4.2 + 9.0, tot
    assert len(tot["by_rate"]) == 2
    rc = compute_totals([{"quantity": 1, "unit_price_net": 100, "vat_rate": 0.21}],
                        reverse_charge=True)
    assert rc["vat_total"] == 0.0 and rc["net_total"] == 100.0, rc
    print("invoicing.py: smoke tests PASS")
