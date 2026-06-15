"""SAF-T (Standard Audit File for Tax) — OECD-core programmable XML generator.

Covers:
- build_saft produces well-formed XML (parse back with ET.fromstring); Header,
  MasterFiles/Suppliers, TaxTable and the GeneralLedgerEntries section are present.
- RECONCILIATION: the XML's entry net/VAT totals tie to queries.q_expense(period).totals
  within per-line rounding tolerance (no transaction dropped).
- The CountryProfile seam: a different profile changes the root namespace +
  AuditFileVersion; get_profile("unknown") -> DEFAULT_PROFILE.
- filename follows the profile pattern (period + filesystem-safe entity).
- build_saft raises ValueError when no data/period; one malformed row is skipped, not a crash.
- Web: /export/saft returns 200 application/xml with an XML body (authenticated); gated.

BASIS: NET EUR, final (rebates applied); gross = net + VAT. Read-only.
"""
import xml.etree.ElementTree as ET

import queries
import reports
import saft


# ----------------------------------------------------------------- helpers

def _latest_period():
    con = reports.connect()
    try:
        ps = reports._periods(con)
        return ps[0] if ps else None
    finally:
        con.close()


def _ns(profile=None):
    return "{" + (profile or saft.DEFAULT_PROFILE).namespace + "}"


# ----------------------------------------------------------------- structure

def test_build_saft_well_formed_and_sections_present():
    name, data = saft.build_saft()
    assert isinstance(data, bytes)
    assert data.lstrip().startswith(b"<?xml")
    root = ET.fromstring(data)              # must parse (well-formed)
    ns = _ns()
    assert root.tag == ns + "AuditFile"

    hdr = root.find(ns + "Header")
    assert hdr is not None
    assert hdr.find(ns + "AuditFileVersion").text == saft.DEFAULT_PROFILE.audit_file_version
    assert hdr.find(ns + "Company") is not None
    assert hdr.find(ns + "SelectionCriteria") is not None

    mf = root.find(ns + "MasterFiles")
    assert mf is not None
    suppliers = mf.find(ns + "Suppliers")
    assert suppliers is not None
    assert len(suppliers.findall(ns + "Supplier")) >= 1
    tt = mf.find(ns + "TaxTable")
    assert tt is not None
    assert len(tt.findall(ns + "TaxTableEntry")) >= 1

    gl = root.find(ns + "GeneralLedgerEntries")
    assert gl is not None
    journal = gl.find(ns + "Journal")
    assert journal is not None
    assert len(journal.findall(ns + "Transaction")) >= 1


def test_core_banner_present():
    name, data = saft.build_saft()
    # The core-structure caveat appears as a comment and a Header element.
    assert saft.CORE_BANNER.encode() in data
    text = data.decode("utf-8")
    assert "<!--" in text and "CORE STRUCTURE" in text


# ----------------------------------------------------------------- reconciliation

def test_reconciles_with_q_expense_totals():
    period = _latest_period()
    assert period, "demo data must be loaded"
    name, data = saft.build_saft(period)
    root = ET.fromstring(data)
    ns = _ns()
    gl = root.find(ns + "GeneralLedgerEntries")
    n_entries = int(gl.find(ns + "NumberOfEntries").text)
    xml_net = float(gl.find(ns + "TotalDebit").text)
    xml_vat = float(gl.find(ns + "TotalCredit").text)

    con = reports.connect()
    try:
        ledger = queries.q_ledger(con, period)
        totals = queries.q_expense(con, period)["totals"]
    finally:
        con.close()

    # No transaction dropped: one entry per ledger row.
    assert n_entries == len(ledger)
    # Per-line money.f2 vs q_expense's sum-then-quantize -> bounded by one cent per line.
    tol = 0.01 * len(ledger) + 0.01
    assert abs(xml_net - totals["net_eur"]) <= tol
    assert abs(xml_vat - totals["vat_eur"]) <= tol

    # And the per-entry sum of the in-XML NetAmount/TaxAmount ties to the same totals.
    journal = gl.find(ns + "Journal")
    s_net = sum(float(t.find(ns + "NetAmount").text) for t in journal.findall(ns + "Transaction"))
    s_vat = sum(float(t.find(ns + "TaxAmount").text) for t in journal.findall(ns + "Transaction"))
    assert abs(s_net - totals["net_eur"]) <= tol
    assert abs(s_vat - totals["vat_eur"]) <= tol


# ----------------------------------------------------------------- profile seam

def test_country_profile_changes_namespace_and_version():
    test_profile = saft.CountryProfile(
        code="ZZ",
        namespace="urn:test:saft:zz:v9",
        schema_version="ZZ-test-9",
        audit_file_version="9.99-zz",
        file_name_pattern="SAFT_{code}_{entity}_{period}.xml",
    )
    name, data = saft.build_saft(profile=test_profile)
    root = ET.fromstring(data)
    assert root.tag == "{urn:test:saft:zz:v9}AuditFile"
    ns = "{urn:test:saft:zz:v9}"
    hdr = root.find(ns + "Header")
    assert hdr.find(ns + "AuditFileVersion").text == "9.99-zz"
    # filename carries the test profile's code, not the default OECD code.
    assert name.startswith("SAFT_ZZ_")


def test_get_profile_unknown_falls_back_to_default():
    assert saft.get_profile("definitely-not-a-real-profile") is saft.DEFAULT_PROFILE
    assert saft.get_profile("") is saft.DEFAULT_PROFILE
    assert saft.get_profile(None) is saft.DEFAULT_PROFILE
    assert saft.get_profile("OECD") is saft.DEFAULT_PROFILE


# ----------------------------------------------------------------- filename

def test_filename_follows_pattern_with_period():
    period = _latest_period()
    name, _ = saft.build_saft(period)
    assert name == f"SAFT_OECD_ALL_{saft._safe(period)}.xml"
    assert name.endswith(".xml")


def test_filename_entity_is_filesystem_safe():
    period = _latest_period()
    con = reports.connect()
    try:
        ents = sorted({r["entity"] for r in queries.q_ledger(con, period)})
    finally:
        con.close()
    entity = ents[0]
    name, _ = saft.build_saft(period, entity=entity)
    assert name.startswith("SAFT_OECD_")
    assert name.endswith(".xml")
    assert " " not in name          # no spaces / unsafe chars leaked in


# ----------------------------------------------------------------- errors / robustness

def test_raises_when_no_data():
    import unittest.mock as mock
    with mock.patch.object(reports, "_periods", return_value=[]):
        try:
            saft.build_saft()
            assert False, "expected ValueError"
        except ValueError as e:
            assert "nothing to export" in str(e)


def test_raises_when_period_has_no_rows():
    try:
        saft.build_saft("1999-01")          # resolved but empty
        assert False, "expected ValueError"
    except ValueError as e:
        assert "nothing to export" in str(e)


def test_malformed_row_skipped_not_crash():
    period = _latest_period()
    con = reports.connect()
    try:
        good = queries.q_ledger(con, period)
    finally:
        con.close()
    # A malformed row (a non-dict that raises on .get) must be skipped, not crash
    # the whole export — the 3 good rows still produce a reconciled file.
    class _Bad:
        def get(self, *a, **k):
            raise ValueError("boom: malformed ledger row")
    rows = good[:3] + [_Bad()]

    import unittest.mock as mock
    with mock.patch.object(queries, "q_ledger", return_value=rows):
        name, data = saft.build_saft(period)
    root = ET.fromstring(data)
    ns = _ns()
    gl = root.find(ns + "GeneralLedgerEntries")
    # the 3 good rows are present; the malformed one is dropped.
    assert int(gl.find(ns + "NumberOfEntries").text) == 3


# ----------------------------------------------------------------- web

def test_export_saft_route(client):
    period = _latest_period()
    r = client.get(f"/export/saft?period={period}")
    assert r.status_code == 200
    assert r.mimetype == "application/xml"
    body = r.get_data()
    root = ET.fromstring(body)              # parses -> XML body
    assert root.tag.endswith("AuditFile")


def test_export_saft_no_data_returns_friendly_200(client):
    # A period with no ledger rows must degrade gracefully (200 + friendly message),
    # NOT raise ValueError("no data loaded") into the global 500 handler.
    r = client.get("/export/saft?period=1999-01")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Nothing to export" in html
    assert "No ledger data for this period/entity" in html


def test_export_saft_is_gated():
    import app
    assert app.PERM_BY_ENDPOINT.get("export_saft") == "exports"
    assert "export_saft" in app.MODULES["analytics"][1]
    assert "export_saft" not in app.ADMIN_ONLY


def test_expenses_page_has_saft_button(client):
    period = _latest_period()
    r = client.get(f"/expenses?period={period}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Download SAF-T (XML, core structure)" in html
    assert f"/export/saft?period={period}" in html
