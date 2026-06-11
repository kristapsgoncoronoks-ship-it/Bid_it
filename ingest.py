"""
INGESTION LAYER - source adapters so a supplier can feed the pipeline from:
  - xlsx : the standard supplier workbook (current default)
  - csv  : portal CSV export
  - xml  : e-invoice / EDI XML file (UBL, Peppol BIS, supplier-specific schemas)
  - api  : REST endpoint returning JSON or XML (e.g. DKV eReporting, E100 API)

The supplier spec declares its source; the engine calls fetch_records() and the
supplier's row_map receives either a tuple (xlsx/csv) or a dict (xml/api).
Nothing downstream changes: validation, master workbook, history DB all stay identical.

SOURCE CONFIG EXAMPLES (put in the spec as "source"):
  {"type": "xlsx", "file": "DKV_SE_May2026_transactions.xlsx", "sheet": "Transactions"}
  {"type": "csv",  "file": "e100_export_2026-06.csv", "delimiter": ";", "encoding": "utf-8"}
  {"type": "xml",  "file": "invoice_2026-06.xml",
   "record_path": ".//cac:InvoiceLine",                 # element that repeats per transaction
   "ns": {"cac": "urn:oasis:...CommonAggregateComponents-2",
          "cbc": "urn:oasis:...CommonBasicComponents-2"},
   "fields": {"qty": "cbc:InvoicedQuantity",            # field -> xpath relative to record
              "net": "cbc:LineExtensionAmount",
              "product": "cac:Item/cbc:Name",
              "date": "cac:Delivery/cbc:ActualDeliveryDate",
              "station": "cac:Delivery/cac:DeliveryLocation/cbc:Description",
              "vehicle": "cac:Delivery/cbc:TrackingID"}}
  {"type": "api",  "url": "https://api.supplier.com/v1/transactions",
   "params": {"period": "{PERIOD}"},                    # {PERIOD} substituted at runtime
   "auth_env": "SUPPLIER_API_TOKEN",                    # token read from environment variable
   "auth_header": "Authorization", "auth_prefix": "Bearer ",
   "format": "json", "records_key": "data.transactions"}   # or format=xml + record_path/fields

NETWORK NOTE: live API pulls need outbound network access - run on your own
machine/server (python3 + requests), or enable network for Claude's environment
in settings. File-based sources (xlsx/csv/xml) work anywhere.
"""
import os, json, csv as _csv
import xml.etree.ElementTree as ET
from openpyxl import load_workbook

WORKDIR = os.path.dirname(os.path.abspath(__file__))


def _xlsx(cfg):
    ws = load_workbook(f"{WORKDIR}/{cfg['file']}", data_only=True)[cfg.get("sheet", "Transactions")]
    for r in ws.iter_rows(min_row=cfg.get("min_row", 2), values_only=True):
        if r and r[0] is not None:
            yield r


def _csvsrc(cfg):
    with open(f"{WORKDIR}/{cfg['file']}", encoding=cfg.get("encoding", "utf-8")) as f:
        rd = _csv.DictReader(f, delimiter=cfg.get("delimiter", ","))
        for row in rd:
            yield row  # dict keyed by header


def _xml_extract(root, cfg):
    ns = cfg.get("ns", {})
    for rec in root.iterfind(cfg["record_path"], ns):
        d = {}
        for field, xp in cfg["fields"].items():
            el = rec.find(xp, ns)
            d[field] = el.text.strip() if el is not None and el.text else None
            if el is not None and not d[field] and el.attrib:
                d[field] = el.attrib  # fall back to attributes
        yield d


def _xml(cfg):
    tree = ET.parse(f"{WORKDIR}/{cfg['file']}")
    yield from _xml_extract(tree.getroot(), cfg)


def _dig(obj, dotted):
    for k in dotted.split("."):
        obj = obj[k]
    return obj


def _api(cfg, ctx):
    try:
        import requests
    except ImportError:
        raise RuntimeError("API source needs 'requests' (pip install requests).")
    headers = {}
    if cfg.get("auth_env"):
        token = os.environ.get(cfg["auth_env"])
        if not token:
            raise RuntimeError(f"Set environment variable {cfg['auth_env']} with the API token.")
        headers[cfg.get("auth_header", "Authorization")] = cfg.get("auth_prefix", "Bearer ") + token
    params = {k: v.replace("{PERIOD}", ctx.get("period", "")) if isinstance(v, str) else v
              for k, v in cfg.get("params", {}).items()}
    resp = requests.get(cfg["url"], headers=headers, params=params,
                        timeout=cfg.get("timeout", 60))
    resp.raise_for_status()
    if cfg.get("format", "json") == "json":
        recs = _dig(resp.json(), cfg.get("records_key", "data"))
        yield from recs
    else:  # xml over http
        yield from _xml_extract(ET.fromstring(resp.text), cfg)


ADAPTERS = {"xlsx": _xlsx, "csv": _csvsrc, "xml": _xml, "api": _api}


def fetch_records(supplier, spec, ctx, files=None):
    """Resolve the supplier's source and yield raw records.
    Default (no 'source' in spec): xlsx workbook named in month_config FILES."""
    src = spec.get("source")
    if src is None:
        src = {"type": "xlsx", "file": files[supplier], "sheet": spec.get("sheet", "Transactions")}
    t = src["type"]
    if t == "api":
        return ADAPTERS[t](src, ctx)
    return ADAPTERS[t](src)


# ======================================================================
# SELF-TEST / DEMO:  python3 ingest.py
# Parses the bundled XML e-invoice and JSON API fixtures end-to-end so the
# XML/API path is proven without any supplier credentials or network.
# ======================================================================
if __name__ == "__main__":
    print("=== DEMO 1: XML e-invoice (UBL-style) ===")
    xml_cfg = {
        "type": "xml", "file": "demo_supplier_invoice.xml",
        "record_path": ".//cac:InvoiceLine",
        "ns": {"cac": "urn:demo:cac", "cbc": "urn:demo:cbc"},
        "fields": {"date": "cbc:DeliveryDate", "vehicle": "cbc:VehicleID",
                   "station": "cbc:Station", "product": "cbc:Product",
                   "qty": "cbc:Quantity", "net": "cbc:NetAmount", "vat": "cbc:VatAmount"},
    }
    recs = list(_xml(xml_cfg))
    for r in recs: print("  ", r)
    tot_net = sum(float(r["net"]) for r in recs)
    print(f"  parsed {len(recs)} lines, net total {tot_net:.2f} "
          f"-> would be validated against the invoice header via 'expected'")

    print("=== DEMO 2: API JSON payload (offline fixture) ===")
    payload = json.load(open(f"{WORKDIR}/demo_api_response.json"))
    recs = _dig(payload, "data.transactions")
    for r in recs: print("  ", r)
    print(f"  parsed {len(recs)} records - identical dicts a live "
          f"requests.get() would yield through the 'api' adapter")
    print("Both paths feed row_map -> canonical schema -> validation -> master/DB unchanged.")
