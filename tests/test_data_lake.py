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


def test_delete_locator_purges_bad_upload(lake):
    """A bad upload (stored copy failed verification) is purged by locator so corrupt
    data never lingers in the lake."""
    assert lake.delete_locator(None) == 0        # tolerant of no locator
    loc = lake.put(b"bytes", "f.json", kind="raw_upload")
    assert lake.query() and lake.get(loc) == b"bytes"
    assert lake.delete_locator(loc) >= 1         # purges the stored copy
    assert lake.query() == []                    # index row gone
    with pytest.raises(Exception):
        lake.get(loc)                            # the bytes are gone too


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


def _seed_ai(lake, supplier, confidence, n=1, backend="claude", period="2026-05"):
    """Insert n ai_extract rows for a supplier with a given confidence (unique bytes)."""
    import json, uuid
    for i in range(n):
        meta = {} if confidence is _SENTINEL else {"backend": backend, "confidence": confidence}
        blob = json.dumps({"supplier": supplier, "_uniq": uuid.uuid4().hex}).encode()
        lake.put(blob, f"{supplier}_{confidence}_{i}.json",
                 kind="ai_extract", supplier=supplier, period=period, meta=meta)


_SENTINEL = object()


def test_parser_priority_counts_and_histogram(lake):
    _seed_ai(lake, "DKV", "low", n=2)
    _seed_ai(lake, "DKV", "high", n=1)
    _seed_ai(lake, "DKV", "MEDIUM", n=1)        # case-insensitive bucketing
    pri = {p["supplier"]: p for p in lake.parser_priority()}
    d = pri["DKV"]
    assert d["ai_count"] == 4
    assert d["low"] == 2 and d["high"] == 1 and d["medium"] == 1 and d["unknown"] == 0
    assert d["backends"] == ["claude"]
    # low*3 + medium*2 + high*1 = 6 + 2 + 1 = 9
    assert d["weighted_score"] == 9


def test_parser_priority_ranking_low_volume_outranks_few_high(lake):
    _seed_ai(lake, "MANY_LOW", "low", n=4)      # score 12
    _seed_ai(lake, "FEW_HIGH", "high", n=2)     # score 2
    pri = lake.parser_priority()
    assert [p["supplier"] for p in pri][:2] == ["MANY_LOW", "FEW_HIGH"]
    assert pri[0]["weighted_score"] > pri[1]["weighted_score"]


def test_parser_priority_bad_meta_falls_to_unknown(lake):
    import json
    con = lake.connect()
    # absent meta, None meta, garbage meta, and non-dict JSON — none may raise
    con.execute("INSERT INTO data_lake_files (kind, supplier, sha256, backend, meta) "
                "VALUES ('ai_extract','GARBLE','s1','claude',NULL)")
    con.execute("INSERT INTO data_lake_files (kind, supplier, sha256, backend, meta) "
                "VALUES ('ai_extract','GARBLE','s2','claude','not json{')")
    con.execute("INSERT INTO data_lake_files (kind, supplier, sha256, backend, meta) "
                "VALUES ('ai_extract','GARBLE','s3','claude','[1,2,3]')")
    con.execute("INSERT INTO data_lake_files (kind, supplier, sha256, backend, meta) "
                "VALUES ('ai_extract','GARBLE','s4','claude',?)", (json.dumps({"confidence": None}),))
    con.commit(); con.close()
    pri = {p["supplier"]: p for p in lake.parser_priority()}
    g = pri["GARBLE"]
    assert g["ai_count"] == 4 and g["unknown"] == 4
    assert g["high"] == g["medium"] == g["low"] == 0


def test_parser_priority_null_supplier_collapses_to_unknown(lake):
    con = lake.connect()
    con.execute("INSERT INTO data_lake_files (kind, supplier, sha256, backend, meta) "
                "VALUES ('ai_extract',NULL,'n1','claude','{\"confidence\":\"low\"}')")
    con.execute("INSERT INTO data_lake_files (kind, supplier, sha256, backend, meta) "
                "VALUES ('ai_extract','   ','n2','claude','{\"confidence\":\"low\"}')")
    con.commit(); con.close()
    pri = {p["supplier"]: p for p in lake.parser_priority()}
    assert "(unknown)" in pri and pri["(unknown)"]["ai_count"] == 2


def test_parser_priority_ignores_non_ai_kinds(lake):
    lake.put(b"raw", "r.json", kind="raw_response", supplier="DKV")
    _seed_ai(lake, "DKV", "low", n=1)
    pri = lake.parser_priority()
    assert len(pri) == 1 and pri[0]["supplier"] == "DKV" and pri[0]["ai_count"] == 1


def test_parser_priority_empty_lake(lake):
    assert lake.parser_priority() == []


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
