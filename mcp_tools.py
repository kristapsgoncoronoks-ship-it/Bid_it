"""
MCP TOOL LOGIC — the READ-ONLY tool functions an AI agent (Claude Desktop /
claude.ai connectors) can call to query the Fleet Fuel & VAT Refund platform's
business data. Modeled on Box's MCP server.

This module holds PLAIN Python functions only — NO `mcp` SDK dependency. The
FastMCP wiring lives in `mcp_server.py` (which guards its `mcp` import so the web
app and the test-suite never need the SDK installed). Keeping the logic here lets
the tests exercise every tool against the demo data WITHOUT the SDK.

DESIGN CONTRACT (every tool):
  • READ-ONLY. Reads the engine-owned product DBs strictly through
    `dataproduct.connect()` (a `mode=ro` handle — a stray write raises) and the
    app-owned read functions (queries.py, vat_refund read functions, metadata,
    customer/supplier master). NO write/action tools exist in v1.
  • NEVER RAISES. Returns a JSON-serializable structure on success or
    `{"error": "<message>"}` on any failure (a bad arg, a missing DB, a broken
    query). An AI agent must get a clean structured answer, never a stack trace.
  • JSON-SERIALIZABLE. Rows are converted to plain dicts/lists of str/int/float/
    None — sqlite3.Row and Decimal never leak out.
  • TENANT-AWARE. The underlying read functions already honor
    `tenancy.scope_clause()` (CRM, suppliers, claims, metadata, search). With the
    `multitenant` switch OFF (the default) this is inert; ON, a tool only sees the
    bound tenant's rows (or, under owner scope, the audited cross-tenant view).
  • NO BANK / SECRET DATA. Per CLAUDE.md's AI-privacy rule, NO tool ever returns
    an IBAN, bank-account, SWIFT, or secret field. `list_suppliers`/`list_customers`
    SELECT only safe identity columns (code / name / country / VAT id), never the
    separate *_bank_accounts tables; `_safe_dict` strips any bank-ish key as a
    belt-and-braces second line of defense.

Each tool's docstring documents its purpose + args — MCP surfaces these to the
agent, so they are written for an LLM caller.
"""
import os

import applog

log = applog.get("mcp_tools")

WORKDIR = os.path.dirname(os.path.abspath(__file__))

# Keys that must NEVER appear in any tool's output — a defense-in-depth filter over
# the explicit safe-column selects below. Matched case-insensitively as a substring,
# so e.g. "beneficiary_iban", "bank_name", "swift_code" are all caught.
_FORBIDDEN_KEY_SUBSTRINGS = (
    "iban", "bank", "swift", "bic", "account_no", "account_number",
    "secret", "password", "token", "credential", "payout_to",
)


def _is_forbidden_key(key):
    k = str(key).lower()
    return any(bad in k for bad in _FORBIDDEN_KEY_SUBSTRINGS)


def _safe_dict(d):
    """Drop any bank/secret-ish key from a result dict (belt-and-braces over the
    explicit safe-column SELECTs). The tools already select only safe columns; this
    guarantees a stray future column can never leak an IBAN/bank/secret to the agent."""
    return {k: v for k, v in dict(d).items() if not _is_forbidden_key(k)}


def _err(context, e):
    """Log a handled failure (applog — never silent) and return the agent-facing
    error envelope. Tools never raise; a caller always gets a structured value."""
    log.warning("mcp tool %s failed: %s", context, e)
    return {"error": f"{context} failed: {str(e)[:200]}"}


# --------------------------------------------------------------------------- search
def search_documents(query, limit=20):
    """Full-text search over the document/invoice corpus (registered invoices and
    vaulted documents, enriched with supplier names, VAT numbers, product terms and
    document tags/fields).

    Args:
        query: free-text search string (supplier, invoice ref, country, amount, tag…).
        limit: max results to return (1–100, default 20).

    Returns a dict {"results": [{rowkey, kind, title, snippet, link}, ...]} ranked
    best-match first, or {"error": ...}. Read-only; tenant-scoped via the search index.
    """
    try:
        import search
        n = max(1, min(int(limit or 20), 100))
        hits = search.search(query or "", limit=n)
        out = []
        for h in hits:
            out.append({
                "rowkey": h.get("rowkey"),
                "kind": h.get("kind"),
                "title": h.get("title"),
                "snippet": h.get("snip"),
                "link": h.get("link"),
            })
        return {"results": out}
    except Exception as e:  # noqa: BLE001 - tool must never raise into the agent
        return _err("search_documents", e)


# --------------------------------------------------------------------------- VAT
def reclaimable_vat(year, period=None, country=None):
    """Reclaimable cross-border VAT (the refund owed by foreign tax authorities) over
    the validated transactions. NET EUR basis. This is the input VAT eligible for a
    2008/9/EC refund, aggregated from the engine-owned transactions.

    Args:
        year: the claim year, e.g. 2026 (required).
        period: optional reporting period filter "YYYY-MM" (a single month).
        country: optional refund-country name filter, e.g. "Belgium".

    Returns {"year", "period", "country", "vat_eur", "net_eur", "litres", "lines"}
    or {"error": ...}. `vat_eur` is the reclaimable VAT total. Read-only, tenant-scoped.
    """
    try:
        import dataproduct
        import tenancy
        import money
        try:
            yr = int(year)
        except (TypeError, ValueError):
            return {"error": f"year must be a 4-digit number, got {year!r}"}

        frag, params = tenancy.scope_clause()
        w = ["period LIKE ?"]
        p = [f"{yr}-%"]
        if period:
            w.append("period = ?")
            p.append(str(period))
        if country:
            w.append("country = ?")
            p.append(str(country))
        where_sql = " AND ".join(w) + frag
        con = dataproduct.connect("fuel_history")
        try:
            r = con.execute(
                f"""SELECT SUM(vat_eur) vat, SUM(net_eur) net, SUM(qty) litres,
                           COUNT(*) lines
                    FROM transactions WHERE {where_sql}""",
                [*p, *params]).fetchone()
        finally:
            con.close()
        return {
            "year": yr,
            "period": period,
            "country": country,
            "vat_eur": money.f2(r["vat"] or 0),
            "net_eur": money.f2(r["net"] or 0),
            "litres": round(r["litres"] or 0.0, 1),
            "lines": r["lines"] or 0,
        }
    except Exception as e:  # noqa: BLE001
        return _err("reclaimable_vat", e)


def claim_status(year=None):
    """Status of VAT refund claims (submitted / approved / paid) with aging and the
    recoverable receivable, from the VAT refund engine's claim records.

    Args:
        year: optional year filter, e.g. 2026; omit for all years.

    Returns {"summary": {submitted, approved, paid, outstanding}, "claims": [...] }
    where each claim is {entity, country, period, vat_eur, status, status_code,
    status_label, submitted, paid, age_days}. NO bank/payout-route fields. Read-only,
    tenant-scoped. Returns {"error": ...} on failure.
    """
    try:
        import vat_refund
        rows, summary = vat_refund.recovery_report(int(year) if year else None)
        claims = []
        for r in rows:
            code = r.get("status_code")
            claims.append({
                "entity": r.get("entity"),
                "country": r.get("country"),
                "period": r.get("period"),
                "vat_eur": r.get("vat_eur"),
                "status": r.get("status"),
                "status_code": code,
                "status_label": vat_refund.STATUS_LABELS.get(code, code),
                "submitted": r.get("submitted"),
                "paid": r.get("paid"),
                "age_days": r.get("age_days"),
            })
        return {
            "summary": {k: summary.get(k) for k in
                        ("submitted", "approved", "paid", "outstanding")},
            "claims": claims,
        }
    except Exception as e:  # noqa: BLE001
        return _err("claim_status", e)


def list_claims(year, status=None, country=None):
    """List individual VAT refund claims for a year, optionally filtered by status or
    refund country. A thin filter over claim_status (the same claim records).

    Args:
        year: the claim year, e.g. 2026 (required).
        status: optional status filter (submitted / approved / paid).
        country: optional refund-country name filter, e.g. "Sweden".

    Returns {"claims": [...]} (same claim shape as claim_status) or {"error": ...}.
    Read-only, tenant-scoped. NO bank/payout fields.
    """
    try:
        if not year:
            return {"error": "year is required"}
        base = claim_status(year)
        if "error" in base:
            return base
        claims = base["claims"]
        if status:
            claims = [c for c in claims if (c.get("status") or "") == str(status)]
        if country:
            claims = [c for c in claims if (c.get("country") or "") == str(country)]
        return {"claims": claims}
    except Exception as e:  # noqa: BLE001
        return _err("list_claims", e)


# --------------------------------------------------------------------------- benchmark / KPIs
def supplier_benchmark(period, country=None):
    """Per-supplier diesel price benchmark for a period — the effective NET €/L each
    supplier charged (VAT excluded, rebates applied), so the agent can compare supplier
    competitiveness. NET EUR/L basis.

    Args:
        period: the reporting period "YYYY-MM" (required), e.g. "2026-05".
        country: optional country name filter, e.g. "Belgium".

    Returns {"period", "country", "rows": [{supplier, country, litres, doc_eur_l,
    eff_eur_l}, ...]} sorted cheapest-effective first, or {"error": ...}. Read-only,
    tenant-scoped.
    """
    try:
        import dataproduct
        import queries
        if not period:
            return {"error": "period is required (YYYY-MM)"}
        con = dataproduct.connect("fuel_history")
        try:
            rows = queries.q_benchmark(con, str(period))
        finally:
            con.close()
        out = []
        for r in rows:
            if country and r["country"] != country:
                continue
            out.append({
                "supplier": r["supplier"],
                "country": r["country"],
                "litres": r["litres"],
                "doc_eur_l": r["doc"],
                "eff_eur_l": r["eff"],
            })
        return {"period": period, "country": country, "rows": out}
    except Exception as e:  # noqa: BLE001
        return _err("supplier_benchmark", e)


def period_kpis(period):
    """Headline fleet KPIs for one reporting period — total NET spend, VAT, gross,
    diesel litres and the effective diesel NET €/L. NET EUR basis.

    Args:
        period: the reporting period "YYYY-MM" (required), e.g. "2026-05".

    Returns {"period", "net_eur", "vat_eur", "gross_eur", "diesel_litres",
    "diesel_eur_l"} or {"error": ...}. Read-only, tenant-scoped.
    """
    try:
        import dataproduct
        import queries
        if not period:
            return {"error": "period is required (YYYY-MM)"}
        con = dataproduct.connect("fuel_history")
        try:
            k = queries.q_kpis(con, str(period))
        finally:
            con.close()
        if k is None:
            return {"error": f"no data for period {period}"}
        return {
            "period": period,
            "net_eur": k["net"],
            "vat_eur": k["vat"],
            "gross_eur": k["gross"],
            "diesel_litres": k["litres"],
            "diesel_eur_l": k["eurl"],
        }
    except Exception as e:  # noqa: BLE001
        return _err("period_kpis", e)


def monthly_trend():
    """Month-by-month diesel trend across all periods — litres and effective NET €/L
    per period, oldest first, for trend/charting. NET EUR/L basis.

    Returns {"trend": [{period, litres, eur_l}, ...]} or {"error": ...}. Read-only,
    tenant-scoped.
    """
    try:
        import dataproduct
        import queries
        con = dataproduct.connect("fuel_history")
        try:
            rows = queries.q_trend(con)
        finally:
            con.close()
        return {"trend": [{"period": r["period"], "litres": r["litres"],
                           "eur_l": r["eurl"]} for r in rows]}
    except Exception as e:  # noqa: BLE001
        return _err("monthly_trend", e)


# --------------------------------------------------------------------------- master data
def list_suppliers():
    """List fuel/toll suppliers — identity only (code, legal name, group, home
    country, status). NO bank/IBAN/SWIFT data is ever returned.

    Returns {"suppliers": [{code, legal_name, group_name, home_country, status}, ...]}
    or {"error": ...}. Read-only, tenant-scoped.
    """
    try:
        import dataproduct
        import tenancy
        frag, params = tenancy.scope_clause()
        con = dataproduct.connect("suppliers")
        try:
            rows = con.execute(
                "SELECT code, legal_name, group_name, home_country, status "
                "FROM suppliers WHERE 1=1" + frag + " ORDER BY code", params).fetchall()
        finally:
            con.close()
        return {"suppliers": [_safe_dict(r) for r in rows]}
    except Exception as e:  # noqa: BLE001
        return _err("list_suppliers", e)


def list_customers():
    """List customer entities (the transport companies whose VAT is recovered) —
    identity only (code, company name, country, VAT number, status). NO bank/IBAN data
    is ever returned (bank accounts live in a separate table this tool never reads).

    Returns {"customers": [{code, company_name, country, vat_number, status}, ...]} or
    {"error": ...}. Read-only, tenant-scoped (admin-only module).
    """
    try:
        import customer_master
        con = customer_master.connect()
        try:
            rows = customer_master.list_customers(con)
        finally:
            con.close()
        out = []
        for r in rows:
            d = dict(r)
            out.append(_safe_dict({
                "code": d.get("code"),
                "company_name": d.get("company_name"),
                "country": d.get("country"),
                "vat_number": d.get("vat_number"),
                "status": d.get("status"),
            }))
        return {"customers": out}
    except Exception as e:  # noqa: BLE001
        return _err("list_customers", e)


# --------------------------------------------------------------------------- metadata
def document_metadata(doc_ref):
    """Tags and custom-field values attached to a document/invoice reference.

    Args:
        doc_ref: the document reference (the search rowkey), e.g. "doc:12".

    Returns {"doc_ref", "tags": [name, ...], "fields": [{name, type, value, display},
    ...]} or {"error": ...}. Read-only, tenant-scoped.
    """
    try:
        import metadata
        ref = (doc_ref or "").strip()
        if not ref:
            return {"error": "doc_ref is required"}
        tags = [t.get("name") for t in metadata.tags_for(ref) if t.get("name")]
        fields = []
        for v in metadata.get_values(ref):
            fields.append({
                "name": v.get("name"),
                "type": v.get("type"),
                "value": v.get("value"),
                "display": v.get("display"),
            })
        return {"doc_ref": ref, "tags": tags, "fields": fields}
    except Exception as e:  # noqa: BLE001
        return _err("document_metadata", e)


# --------------------------------------------------------------------------- doc requests
def overdue_document_requests():
    """Open document requests on the customer-master control board — the documents the
    platform is still waiting on from customers — flagged with their age and overdue
    status.

    Returns {"requests": [{customer, company_name, kind, status, age_days, overdue,
    requested_at}, ...]} (open requests, overdue first) or {"error": ...}. Read-only,
    tenant-scoped. NO bank/secret fields.
    """
    try:
        import customer_master
        con = customer_master.connect()
        try:
            board = customer_master.document_request_board(con)
        finally:
            con.close()
        out = []
        for r in board:
            d = dict(r)
            status = d.get("status")
            # open = not yet terminal (received/cancelled)
            if status in ("received", "cancelled"):
                continue
            out.append(_safe_dict({
                "customer": d.get("customer"),
                "company_name": d.get("company_name"),
                "kind": d.get("kind"),
                "status": status,
                "age_days": d.get("age_days"),
                "overdue": bool(d.get("overdue")),
                "requested_at": d.get("requested_at"),
            }))
        out.sort(key=lambda x: (not x["overdue"], -(x.get("age_days") or 0)))
        return {"requests": out}
    except Exception as e:  # noqa: BLE001
        return _err("overdue_document_requests", e)


# The read-only tool registry — `mcp_server.py` registers each of these as an
# @mcp.tool. Listed here (not in the server) so the test-suite can enumerate the
# tool set without importing the `mcp` SDK. v1 is READ-ONLY: no write/action tools.
TOOLS = (
    search_documents,
    reclaimable_vat,
    claim_status,
    list_claims,
    supplier_benchmark,
    period_kpis,
    monthly_trend,
    list_suppliers,
    list_customers,
    document_metadata,
    overdue_document_requests,
)
