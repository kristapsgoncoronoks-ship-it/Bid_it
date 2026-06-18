"""
Tests for the MCP tool LOGIC (mcp_tools.py) — the PLAIN read-only functions, with NO
dependency on the `mcp` SDK. They run against the shipped demo data.

Coverage:
  • each tool returns the documented shape (period_kpis litres/net/vat, search finds a
    known doc, reclaimable_vat returns a number, claims/benchmark/trend/master shapes);
  • NO tool returns an IBAN / bank / secret field — a PLANTED bank value never appears;
  • a bad arg returns {"error": ...} and NEVER raises;
  • the tools are READ-ONLY (no product-DB write — a write handle would raise);
  • tenant-awareness: with the multitenant switch ON and an unbound (fail-closed) tenant,
    a tenant-scoped tool returns nothing rather than leaking;
  • `import mcp_tools` works WITHOUT the `mcp` package, and importing the repo / running
    the suite does not pull in `mcp`.
"""
import os
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKDIR not in sys.path:
    sys.path.insert(0, WORKDIR)

import mcp_tools as T

PERIOD = "2026-05"
YEAR = 2026

# Every key substring that must NEVER surface in any tool output.
_BANKISH = ("iban", "bank", "swift", "bic", "account_no", "account_number",
            "secret", "password", "token", "credential", "payout_to")


def _assert_no_bank_keys(obj):
    """Recursively assert no dict KEY is bank/secret-ish anywhere in a tool result."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            assert not any(b in kl for b in _BANKISH), f"forbidden key leaked: {k!r}"
            _assert_no_bank_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _assert_no_bank_keys(x)


# --------------------------------------------------------------------------- SDK guard
def test_import_does_not_require_mcp_sdk():
    """The whole point of the architecture: importing the tool logic (and thus the
    test-suite) must NOT need the `mcp` SDK installed."""
    assert "mcp_tools" in sys.modules
    # The repo import / test run must not have pulled in the SDK.
    assert "mcp" not in sys.modules, "the `mcp` SDK leaked into the import graph"


def test_tool_registry_is_read_only_set():
    names = {fn.__name__ for fn in T.TOOLS}
    assert "period_kpis" in names and "search_documents" in names
    # v1 is read-only: no write/action verbs in the registry.
    for n in names:
        assert not any(v in n for v in ("create", "update", "delete", "submit",
                                        "write", "set_", "withdraw", "lock"))


# --------------------------------------------------------------------------- shapes
def test_period_kpis_shape():
    r = T.period_kpis(PERIOD)
    assert "error" not in r
    for k in ("net_eur", "vat_eur", "gross_eur", "diesel_litres", "diesel_eur_l"):
        assert k in r
    assert isinstance(r["net_eur"], (int, float)) and r["net_eur"] > 0
    assert isinstance(r["diesel_litres"], (int, float)) and r["diesel_litres"] > 0


def test_reclaimable_vat_returns_number():
    r = T.reclaimable_vat(YEAR)
    assert "error" not in r
    assert isinstance(r["vat_eur"], (int, float)) and r["vat_eur"] > 0
    # period/country filters are accepted and still return a number
    r2 = T.reclaimable_vat(YEAR, period=PERIOD)
    assert "error" not in r2 and isinstance(r2["vat_eur"], (int, float))


def test_supplier_benchmark_shape():
    r = T.supplier_benchmark(PERIOD)
    assert "error" not in r and isinstance(r["rows"], list) and r["rows"]
    row = r["rows"][0]
    for k in ("supplier", "country", "litres", "doc_eur_l", "eff_eur_l"):
        assert k in row


def test_monthly_trend_shape():
    r = T.monthly_trend()
    assert "error" not in r and isinstance(r["trend"], list) and r["trend"]
    assert all({"period", "litres", "eur_l"} <= set(t) for t in r["trend"])


def test_claim_status_and_list_claims():
    r = T.claim_status(YEAR)
    assert "error" not in r and "summary" in r and isinstance(r["claims"], list)
    assert {"submitted", "approved", "paid", "outstanding"} <= set(r["summary"])
    lst = T.list_claims(YEAR, status="submitted")
    assert "error" not in lst
    assert all(c["status"] == "submitted" for c in lst["claims"])


def test_search_documents_finds_known_doc():
    r = T.search_documents("Q8", limit=5)
    assert "error" not in r and isinstance(r["results"], list) and r["results"]
    assert any("Q8" in (h.get("title") or "") for h in r["results"])


def test_list_suppliers_shape_and_codes():
    r = T.list_suppliers()
    assert "error" not in r
    codes = {s["code"] for s in r["suppliers"]}
    assert {"Q8", "BP", "DKV"}.issubset(codes)


def test_list_customers_shape():
    r = T.list_customers()
    assert "error" not in r and isinstance(r["customers"], list) and r["customers"]
    assert all({"code", "company_name", "status"} <= set(c) for c in r["customers"])


def test_overdue_document_requests_shape():
    r = T.overdue_document_requests()
    assert "error" not in r and isinstance(r["requests"], list)


def test_document_metadata_shape():
    r = T.document_metadata("doc:1")
    assert "error" not in r
    assert "tags" in r and "fields" in r
    assert isinstance(r["tags"], list) and isinstance(r["fields"], list)


# --------------------------------------------------------------------------- NO BANK DATA
def test_no_tool_returns_bank_or_secret_keys():
    """Defense-in-depth: scan every tool's output for any bank/secret-ish KEY."""
    for r in (T.period_kpis(PERIOD), T.reclaimable_vat(YEAR), T.supplier_benchmark(PERIOD),
              T.monthly_trend(), T.claim_status(YEAR), T.list_claims(YEAR),
              T.search_documents("Q8"), T.list_suppliers(), T.list_customers(),
              T.overdue_document_requests(), T.document_metadata("doc:1")):
        _assert_no_bank_keys(r)


def test_planted_supplier_bank_value_never_surfaces(tmp_path, monkeypatch):
    """Plant a real-looking IBAN in a supplier row + its bank-account table, then assert
    list_suppliers NEVER returns that value (it selects identity columns only and never
    reads the *_bank_accounts table)."""
    import sqlite3
    import dataproduct
    secret_iban = "LT999000111122223333"
    db = tmp_path / "suppliers.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE suppliers (code TEXT PRIMARY KEY, legal_name TEXT, group_name TEXT,
            home_country TEXT, status TEXT, notes TEXT, tenant_id TEXT DEFAULT 'default');
        CREATE TABLE supplier_bank_accounts (supplier TEXT, iban TEXT, bank TEXT, swift TEXT);
    """)
    con.execute("INSERT INTO suppliers (code, legal_name, group_name, home_country, "
                "status, notes) VALUES (?,?,?,?,?,?)",
                ("ZZ", "ZZ Fuels", "ZZ Group", "LT", "active",
                 f"pay to {secret_iban}"))   # plant the IBAN even in a free-text column
    con.execute("INSERT INTO supplier_bank_accounts VALUES (?,?,?,?)",
                ("ZZ", secret_iban, "Secret Bank", "SECRSWIFT"))
    con.commit(); con.close()

    # Repoint the read-only product window at the planted DB.
    orig = dict(dataproduct._PATHS)
    monkeypatch.setitem(dataproduct._PATHS, "suppliers", str(db))
    try:
        r = T.list_suppliers()
    finally:
        dataproduct._PATHS.update(orig)

    assert "error" not in r
    codes = {s["code"] for s in r["suppliers"]}
    assert "ZZ" in codes, "planted supplier should be listed (by identity)"
    blob = repr(r)
    assert secret_iban not in blob, "a planted IBAN leaked into list_suppliers output"
    assert "SECRSWIFT" not in blob and "Secret Bank" not in blob
    # The free-text `notes` column (which held the IBAN) must not be exposed either.
    assert all("notes" not in s for s in r["suppliers"])


# --------------------------------------------------------------------------- bad args
def test_bad_args_return_error_never_raise():
    assert "error" in T.period_kpis(None)
    assert "error" in T.period_kpis("")
    assert "error" in T.reclaimable_vat("not-a-year")
    assert "error" in T.supplier_benchmark(None)
    assert "error" in T.list_claims(None)
    assert "error" in T.document_metadata("")
    # garbage / odd inputs still return a structured value, never raise
    assert isinstance(T.search_documents(None), dict)
    assert isinstance(T.search_documents("!!! \"unclosed"), dict)


# --------------------------------------------------------------------------- read-only
def test_tools_are_read_only_no_product_write():
    """The product DB window is read-only: a write through it raises OperationalError.
    The tools only ever SELECT, so this guard simply confirms the boundary is intact —
    no tool could write even if it tried."""
    import sqlite3
    import dataproduct
    con = dataproduct.connect("fuel_history")
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("CREATE TABLE _mcp_should_fail (x)")
    finally:
        con.close()


# --------------------------------------------------------------------------- tenancy
def test_tenant_scoped_tools_fail_closed_when_unbound(monkeypatch):
    """With multitenant ON and NO tenant bound (and not owner scope), scope_clause()
    fails CLOSED (" AND 1=0"). A tenant-scoped tool must then return an empty set, never
    leak across tenants. We force the switch ON and clear the thread context."""
    import tenancy
    import customer_master
    # Build the CRM schema BEFORE arming multitenant: customer_master.connect() lazily
    # seeds checklist_rules (stamping the write-tenant) the first time it opens a given
    # DB file. Under per-test data isolation each test gets a fresh customers.db, so warm
    # it now (multitenant OFF -> write_tenant() == 'default'); otherwise the seed would
    # run inside list_customers() below where require_tenant() correctly refuses.
    customer_master.connect().close()
    monkeypatch.setattr(tenancy, "multitenant_enabled", lambda: True)
    tenancy.reset_tenant()   # neither owner nor a tenant bound -> fail closed
    try:
        sup = T.list_suppliers()
        cust = T.list_customers()
        claims = T.claim_status(YEAR)
        vat = T.reclaimable_vat(YEAR)
    finally:
        tenancy.reset_tenant()
    # Fail-closed: tenant-scoped reads return nothing (not an error, not a leak).
    assert "error" not in sup and sup["suppliers"] == []
    assert "error" not in cust and cust["customers"] == []
    assert "error" not in claims and claims["claims"] == []
    # reclaimable_vat aggregates to 0 over the (empty) scoped set.
    assert "error" not in vat and (vat["vat_eur"] or 0) == 0
