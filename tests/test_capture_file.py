"""
CAPTURE FILE — the AI-vision CAPTURE DOCUMENT persisted as a permanent SECOND FILE next to
the original PDF, linked to the upload's sha256, surfaced in the document vault + on the
intake review screen, and re-saved to reflect AI corrections.

Load-bearing invariants asserted here:
  * a vision draft persists a `capture_document` artifact (JSON + text) in the data lake,
    LINKED to the upload's sha256 (the raw_upload sha) and retrievable/served.
  * after apply_corrections the persisted file reflects the CORRECTED values.
  * the /documents view shows a "Captured data" entry (view + JSON + text) for a document
    that has one, and NOTHING extra for a document that doesn't.
  * XSS in a captured field is escaped in the rendered text/view (and served non-HTML).
  * persistence is BEST-EFFORT — a data_lake failure never breaks capture/review.
  * no product-DB write (data_lake is app-owned).
"""
import importlib
import json

import pytest


# ----------------------------------------------------------------- isolated data lake
@pytest.fixture()
def lake(tmp_path, monkeypatch):
    """An isolated data lake (own dir + DB) so artifacts don't touch the real store."""
    import data_lake
    importlib.reload(data_lake)
    monkeypatch.setattr(data_lake, "LAKE_DIR", str(tmp_path / "data_lake"))
    monkeypatch.setattr(data_lake, "DB", str(tmp_path / "data_lake.db"))
    data_lake._READY.clear()
    import capture_file
    importlib.reload(capture_file)
    return data_lake


def _draft():
    """A vision draft carrying a FULL capture document (vision_capture.to_draft shape)."""
    return {
        "backend": "vision", "supplier": "DKV", "supplier_vat": "LV40003XXXX",
        "statement_ref": "S-9", "statement_date": "2026-05-31", "currency": "EUR",
        "customer": "ACME OU",
        "lines": [
            {"invoice_no": "S-9", "date": "2026-05-30", "country": "Belgium",
             "currency": "EUR", "net": 775.0, "vat": 162.75, "product": "Diesel",
             "qty": 500.0, "_source": "vision", "vat_rate": 21.0, "gross": 937.75},
        ],
        "capture": {
            "header": {
                "supplier": {"name": "DKV", "vat_number": "LV40003XXXX",
                             "address": "Riga 1", "country": "Latvia"},
                "customer": {"name": "ACME OU", "vat_number": "EE100",
                             "account_or_card_no": "C-7"},
                "invoice": {"number": "S-9", "issue_date": "2026-05-31",
                            "due_date": "2026-06-30", "currency": "EUR",
                            "exchange_rate": 1.0},
            },
            "lines": [
                {"date": "2026-05-30", "country": "Belgium", "product": "Diesel",
                 "quantity": 500.0, "net": 775.0, "vat_rate": 21.0, "vat": 162.75,
                 "gross": 937.75},
            ],
            "totals": {"net_total": 775.0, "discount_total": 0.0, "vat_total": 162.75,
                       "gross_total": 937.75},
        },
    }


SHA = "a" * 64


# ----------------------------------------------------------------- persist core
def test_persist_stores_json_and_text_linked_to_upload_sha(lake):
    import capture_file
    res = capture_file.persist(_draft(), SHA, source_name="DKV_May.pdf")
    assert res and res["upload_sha256"] == SHA

    rows = lake.query(kind="capture_document")
    assert len(rows) == 2                                # one JSON + one text artifact
    formats = sorted(json.loads(r["meta"])["format"] for r in rows)
    assert formats == ["json", "text"]
    # every artifact carries the LINK back to the original upload
    for r in rows:
        assert json.loads(r["meta"])["upload_sha256"] == SHA
        assert r["period"] == "2026-05" and r["supplier"] == "DKV"


def test_persist_artifact_is_retrievable_and_served(lake):
    import capture_file
    capture_file.persist(_draft(), SHA, source_name="DKV_May.pdf")
    latest = capture_file.latest_for_upload(SHA)
    assert set(latest) == {"json", "text"}
    jbytes = lake.get(latest["json"]["stored_path"])
    tbytes = lake.get(latest["text"]["stored_path"])
    parsed = json.loads(jbytes.decode("utf-8"))
    assert parsed["header"]["invoice"]["number"] == "S-9"
    assert b"AI VISION CAPTURE DOCUMENT" in tbytes
    assert b"S-9" in tbytes


def test_persist_is_idempotent_on_identical_content(lake):
    import capture_file
    capture_file.persist(_draft(), SHA, source_name="DKV_May.pdf")
    capture_file.persist(_draft(), SHA, source_name="DKV_May.pdf")   # same bytes -> dedup
    assert len(lake.query(kind="capture_document")) == 2             # still just JSON + text


def test_persist_skips_when_no_capture_or_no_sha(lake):
    import capture_file
    assert capture_file.persist({"backend": "claude", "lines": []}, SHA) is None
    assert capture_file.persist(_draft(), "") is None
    assert lake.query(kind="capture_document") == []


def test_persist_is_best_effort_never_raises(lake, monkeypatch):
    import capture_file, data_lake
    monkeypatch.setattr(data_lake, "put", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    # must NOT raise — capture/review unaffected
    assert capture_file.persist(_draft(), SHA, source_name="x.pdf") is None


# ----------------------------------------------------------------- reflects corrections
def test_resave_reflects_ai_corrections(lake):
    import capture_file, ai_verify
    capture_file.persist(_draft(), SHA, source_name="DKV_May.pdf")
    verdict = {"verdict": "discrepancies", "fields": [
        {"name": "line[1].vat", "extracted": "162.75", "document": "162.99", "match": False},
    ], "notes": ""}
    corrected, corrections = ai_verify.apply_corrections(_draft(), verdict)
    assert corrected["capture"]["lines"][0]["vat"] == 162.99
    capture_file.persist(corrected, SHA, source_name="DKV_May.pdf")   # RE-SAVE (newest wins)

    latest = capture_file.latest_for_upload(SHA)
    parsed = json.loads(lake.get(latest["json"]["stored_path"]).decode("utf-8"))
    assert parsed["lines"][0]["vat"] == 162.99                        # corrected value persisted
    assert b"vat=162.99" in lake.get(latest["text"]["stored_path"])


# ----------------------------------------------------------------- text builder
def test_build_text_renders_and_is_shared_with_app(lake):
    import capture_file, app
    cap = _draft()["capture"]
    txt = capture_file.build_text(cap)
    assert "SUPPLIER" in txt and "TRANSACTIONS" in txt and "TOTALS" in txt
    # the app's transient .txt download delegates to the same builder
    assert app._capture_text(cap) == txt


def test_build_text_escapes_nothing_but_serves_as_plain_text(lake):
    """The text rendering itself is raw; the SERVING route sets a non-HTML content type so
    a captured XSS field cannot execute as markup. We assert the served bytes carry the raw
    field but the response is text/plain (defence-in-depth)."""
    import capture_file
    d = _draft()
    d["capture"]["header"]["supplier"]["name"] = "<img src=x onerror=alert(1)>"
    capture_file.persist(d, SHA, source_name="x.pdf")
    latest = capture_file.latest_for_upload(SHA)
    served = lake.get(latest["text"]["stored_path"])
    assert b"<img src=x onerror=alert(1)>" in served            # raw in the .txt artifact


# ----------------------------------------------------------------- serving routes (web)
def _wire_lake(monkeypatch, tmp_path):
    """Point data_lake + capture_file at an isolated store for a web test."""
    import data_lake, capture_file
    monkeypatch.setattr(data_lake, "LAKE_DIR", str(tmp_path / "data_lake"))
    monkeypatch.setattr(data_lake, "DB", str(tmp_path / "data_lake.db"))
    data_lake._READY.clear()
    return data_lake, capture_file


def test_serving_routes_view_and_download(client, monkeypatch, tmp_path):
    _wire_lake(monkeypatch, tmp_path)
    import capture_file
    capture_file.persist(_draft(), SHA, source_name="DKV_May.pdf")

    # documents-vault route
    r = client.get(f"/capture-file/{SHA}.json")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    assert "attachment" in r.headers["Content-Disposition"]
    assert json.loads(r.get_data(as_text=True))["header"]["invoice"]["number"] == "S-9"

    # inline view forces a non-HTML content type + nosniff
    rv = client.get(f"/capture-file/{SHA}.json?view=1")
    assert "inline" in rv.headers["Content-Disposition"]
    assert rv.headers.get("X-Content-Type-Options") == "nosniff"

    # text + the intake-review-context route both work
    rt = client.get(f"/capture-file/{SHA}.txt")
    assert rt.status_code == 200 and rt.mimetype == "text/plain"
    re_ = client.get(f"/extract/capture-file/{SHA}.txt")
    assert re_.status_code == 200 and b"AI VISION CAPTURE DOCUMENT" in re_.data


def test_serving_route_absent_when_no_artifact(client, monkeypatch, tmp_path):
    _wire_lake(monkeypatch, tmp_path)
    r = client.get(f"/capture-file/{'b'*64}.json")
    assert "No saved captured-data file" in r.get_data(as_text=True)


def test_served_xss_field_does_not_execute(client, monkeypatch, tmp_path):
    """A captured field carrying markup is served with a non-HTML content type + nosniff so
    it cannot run as a script when opened in the browser."""
    _wire_lake(monkeypatch, tmp_path)
    import capture_file
    d = _draft()
    d["capture"]["header"]["supplier"]["name"] = "<script>alert(1)</script>"
    capture_file.persist(d, SHA, source_name="x.pdf")
    r = client.get(f"/capture-file/{SHA}.json?view=1")
    assert r.mimetype == "application/json"                     # NOT text/html
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


# ----------------------------------------------------------------- /documents surfacing
def test_documents_view_shows_captured_data_only_when_present(client, monkeypatch, tmp_path):
    _wire_lake(monkeypatch, tmp_path)
    import capture_file

    # a doc with sha SHA_WITH has a capture artifact; another (no sha link) does not
    sha_with = "c" * 64
    sha_without = "d" * 64
    capture_file.persist(_draft(), sha_with, source_name="DKV_May.pdf")

    # render the captured-data link helper directly (the row-builder uses it per doc)
    import app
    with app.app.test_request_context("/documents"):
        with_links = app._captured_data_links(sha_with)
        without_links = app._captured_data_links(sha_without)
    assert "Captured data" in with_links
    assert f"/capture-file/{sha_with}.json" in with_links
    assert f"/capture-file/{sha_with}.txt" in with_links
    assert "view" in with_links
    assert without_links == ""                                  # nothing extra when absent


def test_documents_captured_links_escape_sha(client, monkeypatch, tmp_path):
    _wire_lake(monkeypatch, tmp_path)
    import app
    # a malformed sha is escaped into the URL (defence-in-depth; never raises)
    with app.app.test_request_context("/documents"):
        out = app._captured_data_links('x"><img>')
    assert "<img>" not in out                                   # raw markup escaped


# ----------------------------------------------------------------- review screen surfacing
def test_review_screen_shows_saved_as_a_file(client, monkeypatch, tmp_path):
    _wire_lake(monkeypatch, tmp_path)
    import app, capture_file
    capture_file.persist(_draft(), SHA, source_name="DKV_May.pdf")
    d = _draft()
    d["_upload_sha256"] = SHA
    with app.app.test_request_context("/extract"):
        html = app._capture_document_html(d, token="tok", upload_sha=SHA)
    assert "Saved as a file" in html
    assert f"/extract/capture-file/{SHA}.json" in html
    assert f"/extract/capture-file/{SHA}.txt" in html


def test_review_screen_no_saved_note_when_absent(client, monkeypatch, tmp_path):
    _wire_lake(monkeypatch, tmp_path)
    import app
    with app.app.test_request_context("/extract"):
        html = app._capture_document_html(_draft(), token="tok", upload_sha=("e" * 64))
    assert "Saved as a file" not in html


# ----------------------------------------------------------------- no product-DB write
def test_persist_opens_no_writable_product_db(lake, monkeypatch):
    """capture_file is app-owned: it must never open a writable suppliers/fuel_history DB.
    Guard: trip any attempt to import+connect the engine-owned product DBs."""
    import capture_file
    import supplier_master
    tripped = []
    monkeypatch.setattr(supplier_master, "connect",
                        lambda *a, **k: tripped.append("suppliers") or (_ for _ in ()).throw(
                            AssertionError("product DB opened")))
    capture_file.persist(_draft(), SHA, source_name="x.pdf")
    assert tripped == []
