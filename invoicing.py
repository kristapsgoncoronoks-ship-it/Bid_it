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
import sqlite3

import applog
import audit
import db_tuning
import db_migrate
import money
import tenancy

log = applog.get("invoicing")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned invoicing DB (gitignored). A module-level attr so tests can repoint it the
# same way they repoint workflow.DB / metadata.DB.
DB = f"{WORKDIR}/invoicing.db"

# Phase-1 invoice lifecycle. `draft` is editable; `issued` is the immutable, numbered
# legal invoice. The later statuses are declared here (NOT reachable in Phase 1) so the
# column already knows them — Phase 3 adds the transitions, no migration.
STATUS_DRAFT = "draft"
STATUS_ISSUED = "issued"
STATUSES = (STATUS_DRAFT, STATUS_ISSUED, "sent", "paid", "overdue", "cancelled")

# Issuer profile is stored in security.db's app_settings under these keys (admin-only,
# audited via the normal settings audit). Kept as flat settings in Phase 1; a per-tenant
# issuers table is a Phase 2 item (see module docstring).
ISSUER_KEYS = ("name", "address", "vat_number", "reg_no", "iban", "bank",
               "series", "number_format", "payment_terms_days", "logo_text")
SETTING_PREFIX = "invoice_issuer_"

# The number format placeholders: {series}, {year}, {seq} (seq zero-padded to {pad}).
DEFAULT_NUMBER_FORMAT = "{series}-{year}-{seq:06d}"
DEFAULT_SERIES = "INV"
DEFAULT_PAYMENT_TERMS_DAYS = 14
DEFAULT_CURRENCY = "EUR"

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
"""

# Versioned migrations: APPEND new statements at the END (positions are stable).
_MIGRATIONS = []

_AUDITED_TABLES = ["bill_customers", "invoices", "invoice_lines",
                   "invoice_counters", "invoice_payments"]

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
    out["number_format"] = out.get("number_format") or DEFAULT_NUMBER_FORMAT
    out["payment_terms_days"] = out.get("payment_terms_days") or str(DEFAULT_PAYMENT_TERMS_DAYS)
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


# ============================================================ bill-to customers (CRUD)
def _cust_dict(row):
    d = dict(row)
    d["active"] = bool(d.get("active"))
    return d


def add_customer(name, *, reg_no="", vat_number="", address="", country="",
                 email="", payment_terms_days=None, notes="", created_by=None):
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
                    payment_terms_days, notes, created_by, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (name, (reg_no or "").strip(), (vat_number or "").strip(),
                 (address or "").strip(), (country or "").strip().upper(),
                 (email or "").strip(), ptd, (notes or "").strip(),
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
               "payment_terms_days", "notes", "active")
    sets, params = [], []
    for k in allowed:
        if k in fields:
            v = fields[k]
            if k == "country" and v:
                v = str(v).strip().upper()
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
def compute_line(quantity, unit_price_net, vat_rate, reverse_charge=False):
    """Per-line money: line_net = qty * unit_price_net (quantized half-up),
    line_vat = line_net * effective_rate. Under reverse charge the effective rate is 0.
    Returns (line_net, line_vat, effective_rate) as floats / float. Pure, never raises."""
    qty = money.D(quantity)
    price = money.D(unit_price_net)
    rate = money.D(0) if reverse_charge else money.D(vat_rate)
    net = money.q2(qty * price)
    vat = money.q2(net * rate)
    return float(net), float(vat), float(rate)


def compute_totals(lines, reverse_charge=False):
    """Compute the per-VAT-rate breakdown and the grand totals from a list of line dicts
    (each with quantity, unit_price_net, vat_rate). Returns a dict:

        {"lines": [{...line with line_net/line_vat/rate...}],
         "by_rate": [{"rate": 0.21, "net": .., "vat": ..}, ...] (rate asc),
         "net_total": .., "vat_total": .., "gross_total": ..}

    All amounts are floats already quantized to cents (ROUND_HALF_UP), summed exactly
    via money.fsum so a per-rate subtotal equals the sum of its lines. Pure, never raises."""
    out_lines = []
    buckets = {}   # rate(float) -> {"net":[..], "vat":[..]}
    for ln in lines:
        net, vat, rate = compute_line(
            ln.get("quantity"), ln.get("unit_price_net"), ln.get("vat_rate"),
            reverse_charge=reverse_charge)
        row = dict(ln)
        row["line_net"], row["line_vat"], row["vat_rate"] = net, vat, rate
        out_lines.append(row)
        b = buckets.setdefault(rate, {"net": [], "vat": []})
        b["net"].append(net)
        b["vat"].append(vat)
    by_rate = []
    for rate in sorted(buckets):
        b = buckets[rate]
        by_rate.append({"rate": rate, "net": money.fsum(b["net"]),
                        "vat": money.fsum(b["vat"])})
    net_total = money.fsum([r["net"] for r in by_rate])
    vat_total = money.fsum([r["vat"] for r in by_rate])
    gross_total = money.fsum([net_total, vat_total])
    return {"lines": out_lines, "by_rate": by_rate,
            "net_total": net_total, "vat_total": vat_total, "gross_total": gross_total}


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
    return d


def create_draft(customer_id=None, *, currency=DEFAULT_CURRENCY, supply_date=None,
                 notes="", reverse_charge=None, created_by=None):
    """Create a DRAFT invoice (no number, no snapshots yet). `reverse_charge` may be left
    None to auto-derive from issuer+customer at this moment (the user can still override
    later). Returns (invoice_dict, "") or (None, error)."""
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
                    reverse_charge, created_by, tenant_id)
                   VALUES ('draft', ?,?,?,?,?,?,?)""",
                (customer_id, (currency or DEFAULT_CURRENCY).strip().upper(),
                 supply_date, (notes or "").strip(), rc, created_by,
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


def add_line(invoice_id, *, description="", quantity=0, unit="", unit_price_net=0,
             vat_rate=0, goods_code=""):
    """Append a line to a DRAFT invoice and re-total. Returns (invoice_dict, "") or
    (None, error). Refuses if the invoice is issued (immutable)."""
    try:
        con = connect()
        try:
            row, err = _assert_draft(con, invoice_id)
            if err:
                return None, err
            n = con.execute("SELECT COALESCE(MAX(line_no),0) FROM invoice_lines "
                            "WHERE invoice_id=?", (invoice_id,)).fetchone()[0]
            net, vat, rate = compute_line(quantity, unit_price_net, vat_rate,
                                          reverse_charge=bool(row["reverse_charge"]))
            con.execute(
                """INSERT INTO invoice_lines
                   (invoice_id, line_no, description, quantity, unit, unit_price_net,
                    vat_rate, line_net, line_vat, goods_code, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (invoice_id, n + 1, (description or "").strip(), float(money.D(quantity)),
                 (unit or "").strip(), float(money.D(unit_price_net)), rate,
                 net, vat, (goods_code or "").strip(), tenancy.write_tenant()))
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


def set_invoice_fields(invoice_id, **fields):
    """Edit header fields of a DRAFT invoice (customer_id, currency, supply_date, notes,
    reverse_charge). Re-totals if reverse_charge changes (it flips every line's VAT).
    Returns (invoice_dict, "") or (None, error)."""
    allowed = ("customer_id", "currency", "supply_date", "notes", "reverse_charge")
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
                    # reverse charge flips every line between its rate and 0% — recompute.
                    rc = bool(fields.get("reverse_charge"))
                    for ln in con.execute(
                            "SELECT id, quantity, unit_price_net, vat_rate "
                            "FROM invoice_lines WHERE invoice_id=?", (invoice_id,)).fetchall():
                        # When turning RC OFF we cannot recover the original rate if it was
                        # stored as 0; callers re-enter rates. Here we only zero-out on ON.
                        net, vat, rate = compute_line(
                            ln["quantity"], ln["unit_price_net"], ln["vat_rate"],
                            reverse_charge=rc)
                        con.execute("UPDATE invoice_lines SET vat_rate=?, line_net=?, "
                                    "line_vat=? WHERE id=?", (rate, net, vat, ln["id"]))
                    con.commit()
        finally:
            con.close()
        _retotal(invoice_id)
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("set_invoice_fields(%s) failed", invoice_id)
        return None, f"could not update invoice ({str(e)[:80]})"


def _retotal(invoice_id):
    """Recompute and persist the header totals from the stored lines (cents-exact)."""
    con = connect()
    try:
        lines = con.execute("SELECT line_net, line_vat FROM invoice_lines "
                            "WHERE invoice_id=?", (invoice_id,)).fetchall()
        net = money.fsum([r["line_net"] for r in lines])
        vat = money.fsum([r["line_vat"] for r in lines])
        gross = money.fsum([net, vat])
        con.execute("UPDATE invoices SET net_total=?, vat_total=?, gross_total=? "
                    "WHERE id=?", (net, vat, gross, invoice_id))
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
    if not (cust.get("name") or "").strip() or not (cust.get("address") or "").strip():
        return "the customer is missing a name or address (mandatory on a VAT invoice)"
    if inv["reverse_charge"] and not (cust.get("vat_number") or "").strip():
        return ("reverse charge requires the customer's VAT number (intra-EU B2B); add it "
                "or turn reverse charge off")
    lines = get_lines(invoice_id)
    if not lines:
        return "add at least one line before issuing"
    return ""


def _snapshot_issuer(issuer):
    import json
    return json.dumps({k: issuer.get(k, "") for k in ISSUER_KEYS}, ensure_ascii=False)


def _snapshot_customer(cust):
    import json
    keep = ("id", "name", "reg_no", "vat_number", "address", "country", "email",
            "payment_terms_days")
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
                       issuer_snapshot=?, customer_snapshot=?,
                       status='issued', issued_at=?, issued_by=?
                   WHERE id=?""",
                (number, series, today, due, today,
                 _snapshot_issuer(issuer), _snapshot_customer(cust),
                 datetime.datetime.utcnow().isoformat(timespec="seconds"),
                 issued_by, invoice_id))
            con.commit()
        finally:
            con.close()
        return get_invoice(invoice_id), ""
    except Exception as e:
        log.exception("issue(%s) failed", invoice_id)
        return None, f"could not issue invoice ({str(e)[:80]})"


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
    totals = compute_totals(lines, reverse_charge=bool(inv["reverse_charge"]))
    return {"invoice": inv, "lines": lines, "issuer": issuer, "customer": customer,
            "by_rate": totals["by_rate"], "issued": issued}


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
    L = []
    if not v["issued"]:
        L.append("*** DRAFT — not a valid invoice ***")
        L.append("")
    # Art. 226(2) sequential number / (1) issue date / 'date of supply'
    L.append("INVOICE" if v["issued"] else "INVOICE (DRAFT)")
    L.append(f"Invoice number: {inv.get('number') or '(assigned at issue)'}")
    L.append(f"Issue date: {inv.get('issue_date') or '(at issue)'}")
    sd = inv.get("supply_date")
    if sd and sd != inv.get("issue_date"):
        L.append(f"Date of supply: {sd}")
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
    # Art. 226(6)/(7)/(8): per-line description, qty, unit price, rate
    L.append(f"Lines (amounts NET, {ccy}, VAT excluded):")
    L.append(f"  {'#':<3}{'Description':<34}{'Qty':>8} {'Unit price':>12} "
             f"{'Rate':>7} {'Net':>13}")
    for i, ln in enumerate(lines, 1):
        L.append(f"  {i:<3}{(ln.get('description') or '')[:34]:<34}"
                 f"{money.D(ln.get('quantity')):>8} {_fmt_money(ln.get('unit_price_net')):>12} "
                 f"{_pct(ln.get('vat_rate')):>7} {_fmt_money(ln.get('line_net')):>13}")
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
    L.append("")
    # Art. 226(11)/(11a): exemption / reverse-charge wording
    if inv.get("reverse_charge"):
        L.append(REVERSE_CHARGE_NOTE)
        L.append("")
    # payment terms + due date + IBAN
    L.append(f"Payment due: {inv.get('due_date') or '(set at issue)'}")
    if issuer.get("iban"):
        L.append(f"IBAN: {issuer.get('iban')}"
                 + (f" ({issuer.get('bank')})" if issuer.get("bank") else ""))
    if inv.get("notes"):
        L.append("")
        L.append(f"Notes: {inv.get('notes')}")
    if issuer.get("logo_text"):
        L.insert(0, issuer.get("logo_text"))
        L.insert(1, "")
    return "\n".join(L)


def invoice_pdf(invoice_id):
    """Render the invoice to PDF bytes via the app's dependency-free text_to_pdf. A DRAFT
    is watermarked/labelled 'DRAFT — not a valid invoice' (see invoice_text). Returns the
    PDF bytes, or None if the invoice is unknown / rendering is unavailable."""
    text = invoice_text(invoice_id)
    if not text:
        return None
    try:
        import customer_master
        inv = get_invoice(invoice_id)
        title = (inv.get("number") if inv else None) or "Invoice (draft)"
        return customer_master.text_to_pdf(text, title=title)
    except Exception as e:
        log.warning("invoice_pdf(%s) failed: %s", invoice_id, e)
        return None


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
