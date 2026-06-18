"""
invoice_issue.py — build + persist the CUSTOMER SERVICE-FEE INVOICE (what we bill a
transport client for the VAT-recovery work) and track its Dokobit signing lifecycle.

This is the document side of the Dokobit seam (dokobit.py is the network side). It is
PURELY ADDITIVE and read-only over the recovery data:
  * it never touches or changes a VAT figure, gate, lock, fee or the claim lifecycle —
    it only READS vat_refund.recovery_report() (the same fee/recovery figures the
    Recovery page and reports.fee_report_workbook show) and writes its OWN app-owned
    `invoice_issue.db` (NOT a product DB);
  * `build_invoice(...)` derives a structured invoice dict (number, dates, seller, buyer,
    currency, lines, totals) with every EUR figure quantized via money.f2;
  * `render_html(...)` is an esc-safe printable rendering (never f-strings a raw DB value
    into the page);
  * the invoice NUMBER is sequential + never reused, allocated from a per-year counter in
    `issued_invoices` (the persisted ledger) — `INV<year>-<seq:04d>`.

THE FEE (what we invoice): the agency service fee on the recovered VAT — `fee_eur` on the
recovery row (the SAME frozen fee figure reports.fee_report_workbook uses), at the contract
fee % over the recovered VAT. Our home VAT on that fee is added as a line at the configurable
`invoice_fee_vat_pct` rate (default 0 — many cross-border B2B service fees are reverse-charged;
set the rate if you charge domestic VAT on the fee). NET EUR throughout (VAT shown explicitly).

Public read functions never raise — on a broken/missing DB they degrade and log via applog.
"""
import datetime
import os
import sqlite3

import applog
import db_migrate
import money
from markupsafe import escape as esc

WORKDIR = os.path.dirname(os.path.abspath(__file__))


def _db_path():
    # Resolve under the redirectable data root when set (test isolation), else alongside
    # the module — same convention the app-owned DBs use.
    try:
        import paths
        return paths.db_path("invoice_issue.db")
    except Exception:
        return os.path.join(WORKDIR, "invoice_issue.db")


log = applog.get("invoice_issue")

# Lifecycle of an issued invoice in our ledger (NOT a VAT status — purely the document):
#   draft   — built + stored, Dokobit OFF (or not yet sent)
#   sent    — uploaded + a Dokobit signing opened (awaiting signatures)
#   signed  — Dokobit reported signing_completed; the signed PDF is vaulted
STATUSES = ("draft", "sent", "signed")

# APPEND new statements at the END (db_migrate keeps positions stable).
_MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS issued_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT UNIQUE,
        customer TEXT,
        country TEXT,
        period TEXT,
        currency TEXT DEFAULT 'EUR',
        recovered_vat_eur REAL,
        fee_net_eur REAL,
        fee_vat_eur REAL,
        gross_eur REAL,
        status TEXT DEFAULT 'draft',
        signing_token TEXT,
        sign_url TEXT,
        unsigned_doc_ref TEXT,
        signed_doc_ref TEXT,
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
]
_READY = set()


def connect():
    db = _db_path()
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    if db == ":memory:" or db not in _READY:
        db_migrate.apply(con, "invoice_issue", _MIGRATIONS)
        if db != ":memory:":
            _READY.add(db)
    return con


# ---------------------------------------------------------------- settings
def _fee_vat_pct():
    """Our home VAT rate on the service fee, as a PERCENT (default 0 — cross-border B2B
    fee is commonly reverse-charged). Clamped to [0, 100]; unparseable -> 0. Never raises."""
    try:
        import auth
        v = float(auth.get_setting("invoice_fee_vat_pct", "0") or 0)
        return v if 0 <= v <= 100 else 0.0
    except (TypeError, ValueError):
        return 0.0
    except Exception as e:
        log.warning("invoice_issue: could not read invoice_fee_vat_pct: %s", e)
        return 0.0


def _seller():
    """Our organisation block (the issuer), from app settings (reuses the branding/org
    name) with safe blanks. Pure read; never raises."""
    def g(key, default=""):
        try:
            import auth
            return (auth.get_setting(key, default) or default).strip()
        except Exception:
            return default
    return {
        "name": g("org_name", "") or g("brand_name", "") or "Our company",
        "reg_number": g("org_reg_number", ""),
        "vat_number": g("org_vat_number", ""),
        "address": g("org_address", ""),
        "iban": g("org_iban", ""),
    }


def _terms_days():
    try:
        import auth
        return int(float(auth.get_setting("invoice_terms_days", "14") or 14))
    except (TypeError, ValueError):
        return 14
    except Exception:
        return 14


# ---------------------------------------------------------------- number sequence
def next_number(year=None, con=None):
    """Allocate the NEXT sequential invoice number `INV<year>-<seq:04d>`, never reusing a
    number. The sequence is per-year and derived from the MAX existing number for that
    year in `issued_invoices` (a UNIQUE(number) column is the hard backstop). Caller holds
    the row insert in the same transaction so two issues can't collide. Never raises ->
    falls back to a timestamp-suffixed number on a broken DB."""
    year = year or datetime.date.today().year
    own = con is None
    try:
        con = con or connect()
        prefix = f"INV{year}-"
        row = con.execute(
            "SELECT number FROM issued_invoices WHERE number LIKE ? "
            "ORDER BY number DESC LIMIT 1", (prefix + "%",)).fetchone()
        seq = 1
        if row and row["number"]:
            try:
                seq = int(str(row["number"]).rsplit("-", 1)[-1]) + 1
            except (ValueError, IndexError):
                seq = 1
        return f"{prefix}{seq:04d}"
    except Exception as e:
        log.warning("invoice_issue: next_number fell back: %s", e)
        return f"INV{year}-{datetime.datetime.now():%H%M%S}"
    finally:
        if own and con is not None:
            con.close()


# ---------------------------------------------------------------- build
def build_invoice(customer, country, period, recovered_vat_eur, fee_eur,
                  number=None, currency="EUR", buyer=None, issue_date=None):
    """Build the structured service-fee invoice dict (does NOT persist).

      recovered_vat_eur — the recovered VAT the fee is charged on (informational line)
      fee_eur           — the agency service fee (the SAME frozen figure recovery shows)

    Lines: one fee line (net=fee_eur) + (when invoice_fee_vat_pct>0) a VAT line. Totals via
    money.f2. Pure; never raises on a bad amount (treats it as 0)."""
    issue_date = issue_date or datetime.date.today().isoformat()
    try:
        due = (datetime.date.fromisoformat(issue_date)
               + datetime.timedelta(days=_terms_days())).isoformat()
    except (ValueError, TypeError):
        due = issue_date
    fee_net = money.f2(fee_eur or 0)
    vat_pct = _fee_vat_pct()
    fee_vat = money.f2(money.D(fee_net) * money.D(vat_pct) / money.D(100))
    gross = money.f2(money.D(fee_net) + money.D(fee_vat))
    desc = (f"VAT-recovery service fee — {country or ''} {period or ''} "
            f"(recovered VAT EUR {money.f2(recovered_vat_eur or 0):,.2f})").strip()
    lines = [{"description": desc, "net": fee_net, "vat_rate": vat_pct,
              "vat": fee_vat, "gross": gross}]
    return {
        "number": number,
        "issue_date": issue_date,
        "due_date": due,
        "currency": currency or "EUR",
        "seller": _seller(),
        "buyer": buyer or {"name": customer, "country": country},
        "customer": customer,
        "country": country,
        "period": period,
        "recovered_vat_eur": money.f2(recovered_vat_eur or 0),
        "lines": lines,
        "totals": {"net": fee_net, "vat": fee_vat, "gross": gross},
    }


def _row(label, value):
    return (f"<tr><th style='text-align:left'>{esc(label)}</th>"
            f"<td>{esc(value)}</td></tr>")


def _eur(x):
    """A thousands-grouped 2dp EUR string for display (money.f2 first)."""
    return "{:,.2f}".format(money.f2(x or 0))


def _cell_r(value):
    return "<td style='text-align:right'>" + str(esc(value)) + "</td>"


def render_html(inv):
    """An esc-safe printable HTML rendering of a built invoice dict. Every DB/derived
    value passes through markupsafe.escape — no raw value is f-strung into the page."""
    s = inv.get("seller") or {}
    b = inv.get("buyer") or {}
    cur = inv.get("currency") or "EUR"
    line_rows = []
    for ln in inv.get("lines") or []:
        rate = ln.get("vat_rate") or 0
        line_rows.append(
            "<tr>"
            + "<td>" + str(esc(ln.get("description"))) + "</td>"
            + _cell_r(_eur(ln.get("net")))
            + _cell_r("{:g}%".format(rate))
            + _cell_r(_eur(ln.get("vat")))
            + _cell_r(_eur(ln.get("gross")))
            + "</tr>")
    t = inv.get("totals") or {}
    return (
        "<div class='invoice'>"
        "<h2>Service-fee invoice " + str(esc(inv.get("number") or "(draft)")) + "</h2>"
        "<table class='meta'>"
        + _row("Issue date", inv.get("issue_date"))
        + _row("Due date", inv.get("due_date"))
        + _row("Currency", cur)
        + "</table>"
        "<h3>From</h3><table class='meta'>"
        + _row("Seller", s.get("name"))
        + _row("Reg. number", s.get("reg_number"))
        + _row("VAT number", s.get("vat_number"))
        + _row("Address", s.get("address"))
        + "</table>"
        "<h3>To</h3><table class='meta'>"
        + _row("Customer", b.get("name") or inv.get("customer"))
        + _row("Country", b.get("country") or inv.get("country"))
        + _row("VAT number", b.get("vat_number"))
        + "</table>"
        "<h3>Lines</h3>"
        "<table class='lines'><thead><tr>"
        "<th>Description</th><th>Net</th><th>VAT %</th><th>VAT</th><th>Gross</th>"
        "</tr></thead><tbody>"
        + "".join(line_rows)
        + "</tbody><tfoot>"
        "<tr><th colspan='4' style='text-align:right'>Net</th>"
        + _cell_r(_eur(t.get("net"))) + "</tr>"
        "<tr><th colspan='4' style='text-align:right'>VAT</th>"
        + _cell_r(_eur(t.get("vat"))) + "</tr>"
        "<tr><th colspan='4' style='text-align:right'>TOTAL (" + str(esc(cur)) + ")</th>"
        "<td style='text-align:right'><b>" + str(esc(_eur(t.get("gross")))) + "</b></td></tr>"
        "</tfoot></table>"
        "<p class='note'>All figures NET EUR (final, VAT shown explicitly).</p>"
        "</div>")


# ---------------------------------------------------------------- recovery sourcing
def fee_for(customer, country, period, year=None):
    """Find the recovery row for (customer, country, period) and return
    (recovered_vat_eur, fee_eur) — the SAME figures the Recovery page shows. Returns None
    when no matching open/paid claim exists. Read-only; never raises."""
    try:
        import vat_refund
        out, _summary = vat_refund.recovery_report(year)
        for o in out:
            if (o.get("entity") == customer and o.get("country") == country
                    and o.get("period") == period):
                return (o.get("vat_eur") or 0, o.get("fee_eur") or 0)
    except Exception as e:
        log.warning("invoice_issue: fee_for(%r,%r,%r) failed: %s",
                    customer, country, period, e)
    return None


# ---------------------------------------------------------------- persist
def record(inv, status="draft", signing_token=None, sign_url=None,
           unsigned_doc_ref=None, created_by="system", con=None):
    """Persist a built invoice into `issued_invoices` (allocating its number in the SAME
    transaction so the sequence never collides). Returns the stored row dict (or None on a
    write failure, which is logged). If inv['number'] is set it is used as-is; otherwise a
    sequential number is allocated."""
    own = con is None
    try:
        con = con or connect()
        number = inv.get("number") or next_number(
            (inv.get("period") or "")[:4] or None, con=con)
        t = inv.get("totals") or {}
        cur = con.execute(
            """INSERT INTO issued_invoices
               (number, customer, country, period, currency, recovered_vat_eur,
                fee_net_eur, fee_vat_eur, gross_eur, status, signing_token, sign_url,
                unsigned_doc_ref, created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (number, inv.get("customer"), inv.get("country"), inv.get("period"),
             inv.get("currency") or "EUR", money.f2(inv.get("recovered_vat_eur") or 0),
             money.f2(t.get("net") or 0), money.f2(t.get("vat") or 0),
             money.f2(t.get("gross") or 0), status, signing_token, sign_url,
             unsigned_doc_ref, created_by))
        new_id = cur.lastrowid
        con.commit()
        return get(new_id, con=con)
    except Exception as e:
        log.warning("invoice_issue: record failed: %s", e)
        return None
    finally:
        if own and con is not None:
            con.close()


def get(invoice_id, con=None):
    own = con is None
    try:
        con = con or connect()
        r = con.execute("SELECT * FROM issued_invoices WHERE id=?",
                        (invoice_id,)).fetchone()
        return dict(r) if r else None
    except Exception as e:
        log.warning("invoice_issue: get(%r) failed: %s", invoice_id, e)
        return None
    finally:
        if own and con is not None:
            con.close()


def by_signing_token(signing_token):
    """The issued invoice for a Dokobit signing_token, or None. Never raises."""
    if not signing_token:
        return None
    try:
        con = connect()
        r = con.execute("SELECT * FROM issued_invoices WHERE signing_token=?",
                        (signing_token,)).fetchone()
        con.close()
        return dict(r) if r else None
    except Exception as e:
        log.warning("invoice_issue: by_signing_token failed: %s", e)
        return None


def set_signing(invoice_id, signing_token, sign_url, status="sent"):
    """Attach a Dokobit signing_token + signer URL to an issued invoice. Never raises."""
    try:
        con = connect()
        con.execute(
            "UPDATE issued_invoices SET signing_token=?, sign_url=?, status=? WHERE id=?",
            (signing_token, sign_url, status, invoice_id))
        con.commit(); con.close()
        return True
    except Exception as e:
        log.warning("invoice_issue: set_signing failed: %s", e)
        return False


def mark_signed(signing_token, signed_doc_ref):
    """Mark the invoice for `signing_token` as signed and store the signed-doc locator.
    Returns True iff a matching row was updated. Never raises."""
    if not signing_token:
        return False
    try:
        con = connect()
        cur = con.execute(
            "UPDATE issued_invoices SET status='signed', signed_doc_ref=? "
            "WHERE signing_token=?", (signed_doc_ref, signing_token))
        con.commit()
        changed = cur.rowcount
        con.close()
        return bool(changed)
    except Exception as e:
        log.warning("invoice_issue: mark_signed failed: %s", e)
        return False


def list_invoices(limit=200):
    """Issued invoices, newest first. Never raises (-> [] on a broken/missing DB)."""
    try:
        con = connect()
        rows = con.execute(
            "SELECT * FROM issued_invoices ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("invoice_issue: list_invoices failed: %s", e)
        return []
