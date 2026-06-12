"""Data lake: AI-processed extraction artifacts stored as files (same backend logic as
the PDF vault), deduped + indexed, and readable by any module."""
import importlib

import pytest


@pytest.fixture()
def lake(tmp_path, monkeypatch):
    import data_lake
    importlib.reload(data_lake)
    monkeypatch.setattr(data_lake, "LAKE_DIR", str(tmp_path / "data_lake"))
    monkeypatch.setattr(data_lake, "DB", str(tmp_path / "data_lake.db"))
    data_lake._READY.clear()
    return data_lake


def test_put_extraction_strips_binary_and_indexes(lake):
    draft = {"supplier": "DKV", "statement_date": "2026-05-31", "confidence": "medium",
             "lines": [{"net": 100}], "_pdf_bytes": [b"binary"]}
    loc = lake.put_extraction(draft, "DKV_May.pdf", "claude")
    assert "ai_extract/DKV/2026-05/" in loc.replace("\\", "/")
    rows = lake.query(kind="ai_extract", supplier="DKV")
    assert len(rows) == 1 and rows[0]["period"] == "2026-05" and rows[0]["backend"] == "local"
    meta, data = lake.get_file(rows[0]["id"])
    assert b"_pdf_bytes" not in data and b"binary" not in data and b"DKV" in data
    import json
    assert json.loads(meta["meta"])["backend"] == "claude"


def test_dedup_by_sha(lake):
    blob = b'{"supplier":"X"}'
    a = lake.put(blob, "x.json")
    b = lake.put(blob, "x.json")                 # identical bytes -> same locator, one row
    assert a == b
    assert len(lake.query()) == 1


def test_get_roundtrip_and_counts(lake):
    loc = lake.put(b"hello-lake", "h.txt", kind="raw_response", supplier="Q8")
    assert lake.get(loc) == b"hello-lake"
    assert lake.counts()["raw_response"]["files"] == 1


def test_query_filters(lake):
    lake.put(b"a", "a.json", kind="ai_extract", supplier="DKV", period="2026-05")
    lake.put(b"b", "b.json", kind="ai_extract", supplier="BP", period="2026-05")
    assert len(lake.query(supplier="DKV")) == 1
    assert len(lake.query(period="2026-05")) == 2


def test_explicit_delete(lake):
    loc = lake.put(b"bytes", "f.json", kind="raw_upload")
    fid = lake.query()[0]["id"]
    assert lake.delete(fid) is True
    assert lake.query() == []                    # row gone
    assert lake.delete(fid) is False             # idempotent / already gone


def test_verify_detects_corruption(lake, tmp_path):
    import os
    lake.put(b"good", "g.json", kind="raw_upload")
    bad_loc = lake.put(b"original", "b.json", kind="raw_upload")
    # tamper with the stored bytes on disk (local backend stores the path as locator)
    with open(bad_loc, "wb") as fh:
        fh.write(b"tampered")
    rows, summ = lake.verify()
    assert summ["total"] == 2 and summ["corrupt"] == 1 and summ["ok"] == 1
    assert any(r["status"] == "CORRUPT" for r in rows)
    # a missing file is flagged too
    os.remove(bad_loc)
    _rows2, summ2 = lake.verify()
    assert summ2["missing"] == 1


def test_extract_ai_path_writes_to_lake(tmp_path, monkeypatch):
    """When an AI backend produces a draft, extract() archives it in the lake."""
    import extract, data_lake
    importlib.reload(data_lake)
    monkeypatch.setattr(data_lake, "LAKE_DIR", str(tmp_path / "data_lake"))
    monkeypatch.setattr(data_lake, "DB", str(tmp_path / "data_lake.db"))
    data_lake._READY.clear()
    # force the AI branch with a stubbed backend (no network)
    monkeypatch.setattr(extract, "pdf_text", lambda b: "some invoice text")
    monkeypatch.setattr(extract, "_ai_extract",
                        lambda be, texts: {"supplier": "ACME", "confidence": "medium",
                                           "statement_date": "2026-05-01", "lines": []})
    extract.extract(b"%PDF-1.4 x", "acme.pdf", backend="claude")
    rows = data_lake.query(kind="ai_extract")
    assert len(rows) == 1 and rows[0]["supplier"] == "ACME"
