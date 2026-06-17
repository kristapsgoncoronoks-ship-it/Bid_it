"""
FULL-TEXT SEARCH (A2) — a searchable index over the document/invoice corpus.

The data-processing engine OWNS and WRITES the product DBs (`fuel_history.db`:
transactions + `invoice_documents` + `invoice_receipt_control`; `suppliers.db`:
supplier master + `supplier_invoices` + statements). This module READS that corpus
strictly READ-ONLY through `dataproduct.connect()` — it never opens a product DB
writable (a stray write would raise OperationalError, the data-product boundary).

It maintains its OWN app-owned index database `search.db` (gitignored, NOT a product
DB — the app may freely write it) holding an SQLite **FTS5** virtual table. `rebuild()`
scans the product DBs read-only and (re)materializes the index; `search()` runs an
FTS5 MATCH with bm25 ranking + a snippet. Both are best-effort: `search()` never raises
into the caller (a malformed/garbage query returns no rows), and `rebuild()` tolerates a
missing/empty product table.

Indexed corpus — one FTS row per canonical artifact, each carrying a stable `rowkey`
(so a hit deep-links) and a `link` (the page that shows it):
  • DOCUMENT  — a registered `invoice_documents` row (entity, supplier, invoice ref,
                filename, kind, dates) enriched with its matching `supplier_invoices`
                line (country, currency, gross) and supplier VAT number. link=/doc/<id>.
  • INVOICE   — a `supplier_invoices` line (supplier, country, invoice no/date, period,
                currency, gross, notes) + supplier legal name + VAT number, plus the
                product codes/descriptions seen for that supplier×country in
                `transactions`. link=/transactions?...  (the drill-down view).

A thin `Backend` seam keeps the FTS5 implementation swappable later (e.g. an external
search service) without touching callers — but it is deliberately minimal.
"""
import os
import sqlite3
import time

import applog
import dataproduct
import db_tuning
import tenancy

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned index DB (gitignored). A module-level attr so tests can repoint it the same
# way they repoint history.DB / metrics.DB.
DB = f"{WORKDIR}/search.db"

log = applog.get("search")

# Row "kinds" — what the indexed artifact is (for the result label / which link applies).
KIND_DOC = "document"
KIND_INVOICE = "invoice"


# --------------------------------------------------------------------------- index DB
def _index_connect():
    """Open the WRITABLE handle to the APP-OWNED search.db (NOT a product DB — writing it
    is allowed). Plain sqlite3 + db_tuning (WAL + busy_timeout), like the other app-owned
    runtime DBs."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    return con


def _ensure_schema(con):
    """Create the FTS5 virtual table if absent. `rowkey`/`kind`/`link`/`title` are
    UNINDEXED structured columns (returned with a hit, not matched); the remaining
    columns are the searchable text. contentless-external is unnecessary here — the
    index is small and fully rebuilt, so a plain (content-stored) FTS5 table keeps
    snippet() cheap and the rebuild a simple DELETE + INSERT."""
    # If a PRE-tenant corpus exists (built before the tenant_id column was added), drop it
    # so the (re)create below lands the new schema. The index is fully rebuilt from the
    # product DBs, so dropping a stale index loses nothing — the next rebuild repopulates it.
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(corpus)").fetchall()}
        if cols and "tenant_id" not in cols:
            con.execute("DROP TABLE corpus")
    except sqlite3.OperationalError:
        pass
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS corpus USING fts5("
        "rowkey UNINDEXED, kind UNINDEXED, link UNINDEXED, title UNINDEXED, "
        "tenant_id UNINDEXED, "
        "supplier, vat_number, ref, parties, country, dates, products, amounts, "
        "filename, body, "
        "tokenize='unicode61 remove_diacritics 2')")


def _has_tenant_col(con, table):
    """True iff `table` carries a tenant_id column. Tolerant of a missing table -> False.
    Lets _scan stamp the real source tenant when the engine product DB has the P1 column,
    and fall back to DEFAULT_TENANT_ID otherwise (so a pre-P1 / test product corpus indexes
    byte-identically to before)."""
    try:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        return "tenant_id" in cols
    except sqlite3.OperationalError:
        return False


# --------------------------------------------------------------------------- read corpus
def _txt(v):
    """Render any DB value to a trimmed string for the index (numbers -> text so an
    amount/ref is searchable). None/empty -> ''."""
    if v is None:
        return ""
    if isinstance(v, float):
        # avoid trailing-zero noise but keep the cents searchable as text
        return ("%g" % v) if v == int(v) else ("%.2f" % v)
    return str(v).strip()


def _supplier_lookup(scon):
    """{code: legal_name} and {(supplier,country): vat_number} from suppliers.db
    (read-only). Tolerates a missing table (returns empty maps)."""
    names, vats = {}, {}
    try:
        for r in scon.execute("SELECT code, legal_name FROM suppliers"):
            names[r["code"]] = _txt(r["legal_name"])
    except sqlite3.OperationalError as e:
        log.debug("suppliers table not readable: %s", e)
    try:
        for r in scon.execute(
                "SELECT supplier, country, vat_number FROM supplier_vat_registrations"):
            vats[(r["supplier"], r["country"])] = _txt(r["vat_number"])
    except sqlite3.OperationalError as e:
        log.debug("supplier_vat_registrations not readable: %s", e)
    return names, vats


def _supplier_products(fcon):
    """{supplier: "code1 desc1 | code2 desc2 ..."} of the distinct product codes/
    descriptions seen per supplier in the validated `transactions`. Lets an invoice be
    found by a product term (e.g. 'AdBlue', a toll code). Read-only; tolerant of a
    missing table."""
    out = {}
    try:
        seen = {}
        for r in fcon.execute(
                "SELECT DISTINCT supplier, product, product_group FROM transactions"):
            bag = seen.setdefault(r["supplier"], [])
            for v in (r["product"], r["product_group"]):
                t = _txt(v)
                if t and t not in bag:
                    bag.append(t)
        out = {s: " ".join(b) for s, b in seen.items()}
    except sqlite3.OperationalError as e:
        log.debug("transactions not readable for product enrichment: %s", e)
    return out


def _doc_invoice_index(scon):
    """{(supplier,invoice_no): row} of supplier_invoices for enriching a document with its
    line (country/currency/gross). supplier_invoices lives in suppliers.db, so this is
    called with the read-only suppliers handle; tolerates the table's absence."""
    out = {}
    try:
        for r in scon.execute(
                "SELECT supplier, country, invoice_no, invoice_date, period, currency, "
                "gross_total, notes FROM supplier_invoices"):
            out.setdefault((r["supplier"], r["invoice_no"]), r)
    except sqlite3.OperationalError as e:
        log.debug("supplier_invoices not readable: %s", e)
    return out


def _metadata_text(rowkey):
    """The A3 metadata blob (tag names + custom-field values) for a document `rowkey`
    (`doc:<id>`), or "" if there is none / metadata is unavailable. Best-effort and NEVER
    raises — search must keep working even if the app-owned metadata.db is absent/empty."""
    try:
        import metadata
        return metadata.index_text(rowkey)
    except Exception as e:
        log.debug("metadata enrichment skipped for %s: %s", rowkey, e)
        return ""


def _scan(fcon, scon):
    """Scan the product DBs READ-ONLY and yield index rows (dicts matching the FTS5
    columns). `fcon` = read-only fuel_history handle (documents + transactions),
    `scon` = read-only suppliers handle (supplier master + invoices). Never raises on a
    missing table — a product not yet built simply contributes nothing."""
    names, vats = _supplier_lookup(scon)
    products = _supplier_products(fcon)
    inv_by_key = _doc_invoice_index(scon)

    _DEFAULT_T = tenancy.DEFAULT_TENANT_ID

    # ---- DOCUMENTS (invoice_documents, fuel_history.db) -> /doc/<id>
    doc_has_t = _has_tenant_col(fcon, "invoice_documents")
    try:
        doc_rows = fcon.execute(
            "SELECT id, entity, supplier, invoice_ref, filename, kind, uploaded_at"
            + (", tenant_id" if doc_has_t else "")
            + " FROM invoice_documents").fetchall()
    except sqlite3.OperationalError as e:
        log.debug("invoice_documents not readable: %s", e)
        doc_rows = []
    for d in doc_rows:
        sup = d["supplier"]
        legal = names.get(sup, "")
        inv = inv_by_key.get((sup, d["invoice_ref"]))
        country = _txt(inv["country"]) if inv else ""
        currency = _txt(inv["currency"]) if inv else ""
        gross = _txt(inv["gross_total"]) if inv else ""
        inv_date = _txt(inv["invoice_date"]) if inv else ""
        vat = vats.get((sup, country)) or ""
        rowkey = f"doc:{d['id']}"
        title = (f"{_txt(sup)} — invoice {_txt(d['invoice_ref'])}"
                 if d["invoice_ref"] else f"{_txt(sup)} — {_txt(d['filename'])}")
        # A3 — fold this document's metadata (tag names + custom-field values) into the
        # searchable body, keyed by the SAME `doc:<id>` rowkey metadata.py stores against,
        # so a document becomes findable by its tag/field text. Best-effort: empty/broken
        # metadata contributes "" and never breaks the rebuild (search must not depend on
        # the app-owned metadata DB existing).
        meta = _metadata_text(rowkey)
        yield {
            "rowkey": rowkey, "kind": KIND_DOC, "link": f"/doc/{d['id']}",
            "tenant_id": (d["tenant_id"] if doc_has_t else _DEFAULT_T) or _DEFAULT_T,
            "title": title,
            "supplier": " ".join(x for x in (_txt(sup), legal) if x),
            "vat_number": vat,
            "ref": _txt(d["invoice_ref"]),
            "parties": _txt(d["entity"]),
            "country": country,
            "dates": " ".join(x for x in (_txt(d["uploaded_at"]), inv_date) if x),
            "products": products.get(sup, ""),
            "amounts": " ".join(x for x in (gross, currency) if x),
            "filename": _txt(d["filename"]),
            "body": " ".join(x for x in (_txt(d["kind"]), _txt(d["filename"]), meta) if x),
        }

    # ---- SUPPLIER INVOICES (supplier_invoices, suppliers.db) -> /transactions?...
    inv_has_t = _has_tenant_col(scon, "supplier_invoices")
    try:
        inv_rows = scon.execute(
            "SELECT supplier, country, invoice_no, invoice_date, period, currency, "
            "gross_total, notes"
            + (", tenant_id" if inv_has_t else "")
            + " FROM supplier_invoices").fetchall()
    except sqlite3.OperationalError as e:
        log.debug("supplier_invoices not readable for invoice index: %s", e)
        inv_rows = []
    for r in inv_rows:
        sup = r["supplier"]
        legal = names.get(sup, "")
        vat = vats.get((sup, r["country"])) or ""
        rowkey = f"inv:{_txt(sup)}|{_txt(r['country'])}|{_txt(r['invoice_no'])}"
        link = (f"/transactions?supplier={_txt(sup)}"
                f"&country={_txt(r['country'])}"
                + (f"&period={_txt(r['period'])}" if r["period"] else ""))
        yield {
            "rowkey": rowkey, "kind": KIND_INVOICE, "link": link,
            "tenant_id": (r["tenant_id"] if inv_has_t else _DEFAULT_T) or _DEFAULT_T,
            "title": f"{_txt(sup)} — invoice {_txt(r['invoice_no'])} ({_txt(r['country'])})",
            "supplier": " ".join(x for x in (_txt(sup), legal) if x),
            "vat_number": vat,
            "ref": _txt(r["invoice_no"]),
            "parties": "",
            "country": _txt(r["country"]),
            "dates": " ".join(x for x in (_txt(r["invoice_date"]), _txt(r["period"])) if x),
            "products": products.get(sup, ""),
            "amounts": " ".join(x for x in (_txt(r["gross_total"]), _txt(r["currency"])) if x),
            "filename": "",
            "body": _txt(r["notes"]),
        }


# --------------------------------------------------------------------------- backend seam
class Backend:
    """Thin seam over the index so the FTS5 implementation can be swapped later (e.g. an
    external search service) without touching the page/admin wiring. The default —
    `Fts5Backend` — is the only one shipped."""

    def rebuild(self, rows):
        raise NotImplementedError

    def query(self, query, limit):
        raise NotImplementedError


# FTS5 query MATCH escaping: wrap each whitespace-separated token in double quotes (an
# FTS5 string literal), doubling any embedded quote. This neutralises FTS5 operator
# punctuation (NEAR, *, :, ^, parentheses, AND/OR) so a raw user query — including one
# with stray quotes — is a safe phrase/prefix search that can never be a syntax error.
def _fts_query(raw):
    toks = [t for t in (raw or "").split() if t.strip()]
    if not toks:
        return ""
    parts = []
    for t in toks:
        parts.append('"' + t.replace('"', '""') + '"')
    return " ".join(parts)


class Fts5Backend(Backend):
    """SQLite FTS5 index in the app-owned search.db."""

    def rebuild(self, rows):
        """Replace the whole index with `rows` (idempotent full reindex). Returns the row
        count. Wrapped in one transaction; the app-owned DB write is allowed."""
        rows = list(rows)
        con = _index_connect()
        try:
            _ensure_schema(con)
            con.execute("DELETE FROM corpus")
            con.executemany(
                "INSERT INTO corpus (rowkey, kind, link, title, tenant_id, supplier, "
                "vat_number, ref, parties, country, dates, products, amounts, filename, "
                "body) "
                "VALUES (:rowkey,:kind,:link,:title,:tenant_id,:supplier,:vat_number,:ref,"
                ":parties,:country,:dates,:products,:amounts,:filename,:body)",
                rows)
            con.commit()
            return len(rows)
        finally:
            con.close()

    def query(self, query, limit):
        """Run the FTS5 MATCH. Returns ranked result dicts (best bm25 first) with a
        `snippet`. Never raises — a missing index or unparseable query yields []."""
        m = _fts_query(query)
        if not m:
            return []
        # Tenant isolation: filter the index to the caller's tenant (inert when multitenant
        # is OFF — fragment is ""). tenant_id is an UNINDEXED FTS5 column, filterable with =.
        # ON + owner scope -> no filter (the audited cross-tenant analytics exception); ON +
        # a tenant -> AND tenant_id = ?; ON + no principal -> AND 1=0 (fail closed).
        frag, tp = tenancy.scope_clause("tenant_id")
        con = _index_connect()
        try:
            try:
                cur = con.execute(
                    "SELECT rowkey, kind, link, title, "
                    "snippet(corpus, -1, '<mark>', '</mark>', '…', 12) AS snip, "
                    "bm25(corpus) AS score "
                    "FROM corpus WHERE corpus MATCH ?" + frag
                    + " ORDER BY score LIMIT ?",
                    (m, *tp, int(limit)))
                return [dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError as e:
                # no such table (index never built / a pre-tenant index needs a rebuild) or
                # a query FTS5 still rejects
                log.debug("search query unsatisfied (%r): %s", query, e)
                return []
        finally:
            con.close()


_BACKEND = Fts5Backend()


# --------------------------------------------------------------------------- public API
def rebuild(fcon=None, scon=None):
    """Full reindex: scan the product DBs READ-ONLY and (re)materialize the FTS index.
    Idempotent — safe to re-run (the index is replaced wholesale, so a second run yields
    the same counts). Opens the read-only `dataproduct` windows when no connection is
    passed (tests pass temp handles). Returns {rows, seconds}.

    Best-effort: a missing product table contributes nothing rather than raising; an
    unexpected failure is logged and re-raised to the caller (the admin action surfaces
    it as an error banner)."""
    t0 = time.time()
    own_f = fcon is None
    own_s = scon is None
    try:
        if own_f:
            fcon = dataproduct.connect("fuel_history")
        if own_s:
            scon = dataproduct.connect("suppliers")
        rows = list(_scan(fcon, scon))
        n = _BACKEND.rebuild(rows)
        secs = round(time.time() - t0, 3)
        log.info("search index rebuilt: %d row(s) in %.3fs", n, secs)
        return {"rows": n, "seconds": secs}
    finally:
        if own_f and fcon is not None:
            fcon.close()
        if own_s and scon is not None:
            scon.close()


def search(query, limit=50):
    """Search the index for `query`; return up to `limit` ranked result dicts:
    {rowkey, kind, link, title, snip, score}. Best bm25 (lowest score) first.

    NEVER raises into the caller — an empty/garbage/punctuation-only query, or a missing
    index, returns []. Query punctuation is escaped (see `_fts_query`) so a raw user
    string is a safe phrase/prefix search, never an FTS5 syntax error."""
    try:
        return _BACKEND.query(query, max(1, min(int(limit or 50), 500)))
    except Exception as e:  # noqa: BLE001 - search must never raise into the page
        log.warning("search failed for %r: %s", query, e)
        return []
