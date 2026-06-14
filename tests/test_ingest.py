"""
INTAKE LAYER — ingest.py source adapters (offline only).

ingest.fetch_records(supplier, spec, ctx, files=None) dispatches by source type to
the ADAPTERS map (xlsx/csv/xml/api) and yields raw records (a tuple for xlsx/csv, a
dict for xml/api). These are characterization tests that pin the current parsing /
dispatch behavior using ONLY the bundled offline fixtures (demo_supplier_invoice.xml,
demo_api_response.json) and tmp_path files — never the network or any credential.

The XML and API specs mirror the working configs in ingest.py's __main__ demo block.
"""
import json
import os

import pytest
from openpyxl import Workbook

import ingest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The exact XML spec from ingest.py's __main__ demo (UBL-style, cac/cbc namespaces).
XML_SPEC = {
    "type": "xml", "file": "demo_supplier_invoice.xml",
    "record_path": ".//cac:InvoiceLine",
    "ns": {"cac": "urn:demo:cac", "cbc": "urn:demo:cbc"},
    "fields": {"date": "cbc:DeliveryDate", "vehicle": "cbc:VehicleID",
               "station": "cbc:Station", "product": "cbc:Product",
               "qty": "cbc:Quantity", "net": "cbc:NetAmount", "vat": "cbc:VatAmount"},
}


# ----------------------------------------------------------------------------- XML
def test_xml_yields_all_line_records():
    """The demo invoice has three cac:InvoiceLine rows -> three records."""
    recs = list(ingest._xml(XML_SPEC))
    assert len(recs) == 3


def test_xml_field_mapping_on_first_row():
    """Field map resolves each xpath to the element text on the first line."""
    recs = list(ingest._xml(XML_SPEC))
    r0 = recs[0]
    assert r0 == {
        "date": "2026-06-03", "vehicle": "ABC123", "station": "Demo Station North",
        "product": "DIESEL", "qty": "450.00", "net": "675.00", "vat": "141.75",
    }


def test_xml_field_mapping_third_row():
    """The AdBlue line is mapped correctly too (distinct product/values)."""
    recs = list(ingest._xml(XML_SPEC))
    r2 = recs[2]
    assert r2["product"] == "ADBLUE"
    assert r2["net"] == "63.08"
    assert r2["station"] == "Demo Station South"


def test_xml_nonmatching_record_path_yields_zero():
    """A record_path matching no element yields zero records (no crash)."""
    spec = dict(XML_SPEC, record_path=".//cac:NoSuchElement")
    assert list(ingest._xml(spec)) == []


def test_xml_wrong_namespace_yields_zero():
    """A wrong namespace map makes the prefixed path resolve nothing -> zero records."""
    spec = dict(XML_SPEC, ns={"cac": "urn:wrong:ns", "cbc": "urn:demo:cbc"})
    assert list(ingest._xml(spec)) == []


def test_xml_missing_field_is_none():
    """A field whose xpath matches nothing in the record maps to None."""
    spec = dict(XML_SPEC)
    spec["fields"] = dict(XML_SPEC["fields"], absent="cbc:NotThere")
    recs = list(ingest._xml(spec))
    assert recs[0]["absent"] is None


# ----------------------------------------------------------------------------- API / _dig
def _load_api_payload():
    with open(f"{WORKDIR}/demo_api_response.json") as f:
        return json.load(f)


def test_dig_valid_dotted_path():
    """_dig walks a nested dotted path to the records list."""
    payload = _load_api_payload()
    recs = ingest._dig(payload, "data.transactions")
    assert isinstance(recs, list)
    assert len(recs) == 2
    assert recs[0]["vehicle"] == "XYZ789"
    assert recs[0]["product"] == "DIESEL"
    assert recs[1]["product"] == "ADBLUE"


def test_dig_single_leaf_key():
    """A single (non-dotted) key returns that leaf value."""
    payload = _load_api_payload()
    assert ingest._dig(payload, "status") == "ok"


def test_dig_missing_path_raises_keyerror():
    """_dig indexes dicts directly: a missing key raises KeyError (its real contract —
    callers/fetch_records surface the misconfiguration rather than silently yielding
    nothing)."""
    payload = _load_api_payload()
    with pytest.raises(KeyError):
        ingest._dig(payload, "data.nonexistent")
    with pytest.raises(KeyError):
        ingest._dig(payload, "totally_absent")


# ----------------------------------------------------------------------------- CSV
def test_csv_yields_dict_rows(tmp_path, monkeypatch):
    """_csvsrc yields one dict per data row, keyed by the header."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    csv_path = tmp_path / "e100.csv"
    csv_path.write_text(
        "date;vehicle;product;qty;net\n"
        "2026-06-01;ABC123;DIESEL;100.0;150.0\n"
        "2026-06-02;ABC123;ADBLUE;10.0;7.5\n",
        encoding="utf-8")
    recs = list(ingest._csvsrc({"file": "e100.csv", "delimiter": ";"}))
    assert len(recs) == 2
    assert recs[0]["date"] == "2026-06-01"
    assert recs[0]["product"] == "DIESEL"
    assert recs[1]["net"] == "7.5"


def test_csv_default_comma_delimiter(tmp_path, monkeypatch):
    """No delimiter in cfg -> default ',' is used."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    csv_path = tmp_path / "plain.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    recs = list(ingest._csvsrc({"file": "plain.csv"}))
    assert recs == [{"a": "1", "b": "2"}]


def test_csv_header_only_yields_nothing(tmp_path, monkeypatch):
    """A header-only file (no data rows) yields zero records (not a crash)."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("a,b\n", encoding="utf-8")
    assert list(ingest._csvsrc({"file": "empty.csv"})) == []


# ----------------------------------------------------------------------------- XLSX
def _make_xlsx(path, sheet, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_xlsx_yields_tuples_skipping_header(tmp_path, monkeypatch):
    """_xlsx skips the header (min_row default 2) and yields a tuple per data row."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    _make_xlsx(tmp_path / "wb.xlsx", "Transactions",
               [("date", "product", "qty"),
                ("2026-06-01", "DIESEL", 100.0),
                ("2026-06-02", "ADBLUE", 10.0)])
    recs = list(ingest._xlsx({"file": "wb.xlsx"}))
    assert len(recs) == 2
    assert recs[0] == ("2026-06-01", "DIESEL", 100.0)
    assert recs[1][1] == "ADBLUE"


def test_xlsx_stops_at_blank_first_cell(tmp_path, monkeypatch):
    """A row whose first cell is None is skipped (the iter guard `r[0] is not None`)."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    _make_xlsx(tmp_path / "wb.xlsx", "Transactions",
               [("date", "product"),
                ("2026-06-01", "DIESEL"),
                (None, "ORPHAN")])
    recs = list(ingest._xlsx({"file": "wb.xlsx"}))
    assert len(recs) == 1
    assert recs[0][0] == "2026-06-01"


def test_xlsx_missing_sheet_raises(tmp_path, monkeypatch):
    """Pointing at a sheet that does not exist raises KeyError (openpyxl) — the real
    behavior: a misconfigured sheet name surfaces, it is not silently empty."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    _make_xlsx(tmp_path / "wb.xlsx", "Transactions", [("a",), ("1",)])
    with pytest.raises(KeyError):
        list(ingest._xlsx({"file": "wb.xlsx", "sheet": "DoesNotExist"}))


# ----------------------------------------------------------------------------- dispatch
def test_dispatch_defaults_to_xlsx_from_files(tmp_path, monkeypatch):
    """No 'source' in the spec -> default xlsx config built from files[supplier]."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    _make_xlsx(tmp_path / "dkv.xlsx", "Transactions",
               [("date", "qty"), ("2026-06-01", 5.0)])
    recs = list(ingest.fetch_records("DKV", {}, {}, files={"DKV": "dkv.xlsx"}))
    assert recs == [("2026-06-01", 5.0)]


def test_dispatch_routes_xml(monkeypatch):
    """source.type == 'xml' routes to the _xml adapter."""
    recs = list(ingest.fetch_records("DEMO", {"source": XML_SPEC}, {}))
    assert len(recs) == 3
    assert recs[0]["product"] == "DIESEL"


def test_dispatch_routes_csv(tmp_path, monkeypatch):
    """source.type == 'csv' routes to the _csvsrc adapter."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    (tmp_path / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    spec = {"source": {"type": "csv", "file": "x.csv"}}
    recs = list(ingest.fetch_records("DEMO", spec, {}))
    assert recs == [{"a": "1", "b": "2"}]


def test_dispatch_routes_xlsx_explicit_source(tmp_path, monkeypatch):
    """An explicit source.type == 'xlsx' routes to the _xlsx adapter (with its sheet)."""
    monkeypatch.setattr(ingest, "WORKDIR", str(tmp_path))
    _make_xlsx(tmp_path / "y.xlsx", "Sheet1", [("h",), ("v1",)])
    spec = {"source": {"type": "xlsx", "file": "y.xlsx", "sheet": "Sheet1"}}
    recs = list(ingest.fetch_records("DEMO", spec, {}))
    assert recs == [("v1",)]


def test_dispatch_api_passes_ctx(monkeypatch):
    """source.type == 'api' routes to the _api adapter with ctx (offline: stub the
    adapter to capture the (cfg, ctx) call without any network)."""
    captured = {}

    def fake_api(cfg, ctx):
        captured["cfg"] = cfg
        captured["ctx"] = ctx
        yield {"ok": True}

    monkeypatch.setitem(ingest.ADAPTERS, "api", fake_api)
    src = {"type": "api", "url": "https://example.invalid/x", "records_key": "data"}
    recs = list(ingest.fetch_records("DEMO", {"source": src}, {"period": "2026-06"}))
    assert recs == [{"ok": True}]
    assert captured["ctx"] == {"period": "2026-06"}
    assert captured["cfg"]["url"] == "https://example.invalid/x"
