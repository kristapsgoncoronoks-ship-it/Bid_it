"""
DATA LAKE — processed/extracted invoice data stored as FILES, with the SAME storage
logic as the PDF document vault.

When an invoice is processed by an AI backend (Claude/OpenAI/Azure), the structured
result (the extraction draft, and optionally the raw model response) is archived here
as a file — separate from the original PDF/XML, which goes to the document vault. The
lake is a shared store: any module can list and read files from it and process them as
it needs (e.g. re-train parsers, audit AI output, reprocess without re-calling the API).

SAME logic as PDFs: storage goes through the SAME pluggable backends as the document
vault (local / SharePoint / FTP), under a logical folder path, with a SHA-256 in an
index (data_lake.db). Only the root differs (data_lake/ vs documents/).

    <kind> / <supplier|unknown> / <period|undated> / <sha12>_<name>

Offline-safe and dependency-free (reuses document_vault). The index DB is git-ignored.
"""
import os, json, hashlib, sqlite3

import document_vault
import db_tuning
import applog

WORKDIR = os.path.dirname(os.path.abspath(__file__))
LAKE_DIR = os.environ.get("DATA_LAKE_DIR", f"{WORKDIR}/data_lake")
DB = os.environ.get("DATA_LAKE_DB", f"{WORKDIR}/data_lake.db")
log = applog.get("data_lake")

_READY = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_lake_files (
    id INTEGER PRIMARY KEY,
    kind TEXT,                         -- ai_extract | raw_response | ...
    supplier TEXT, period TEXT,
    source_name TEXT, filename TEXT,
    stored_path TEXT, sha256 TEXT UNIQUE, size INTEGER,
    backend TEXT, meta TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_lake_kind ON data_lake_files(kind, supplier, period);
"""


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)
    if DB == ":memory:" or DB not in _READY:
        con.executescript(SCHEMA)
        if DB != ":memory:":
            _READY.add(DB)
    return con


def _path(kind, supplier, period, name):
    """Logical lake path — the same builder the document vault uses."""
    return document_vault.vault_path([kind or "misc", supplier or "unknown",
                                      period or "undated"], name)


def put(data, name, kind="ai_extract", supplier=None, period=None,
        source_name=None, meta=None):
    """Store bytes in the lake (deduped by SHA-256) and index them. Returns the
    stored locator. Uses the current document-vault backend (local/SharePoint/FTP)."""
    if not data:
        raise ValueError("empty payload")
    sha = hashlib.sha256(data).hexdigest()
    con = connect()
    existing = con.execute("SELECT stored_path FROM data_lake_files WHERE sha256=?",
                           (sha,)).fetchone()
    if existing:
        con.close()
        return existing["stored_path"]                # identical artifact already stored
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in (name or "artifact"))
    rel = _path(kind, supplier, period, f"{sha[:12]}_{safe}")
    be = document_vault.backend(LAKE_DIR)
    stored, web_url = be.put(rel, data)
    con.execute("""INSERT INTO data_lake_files
        (kind, supplier, period, source_name, filename, stored_path, sha256, size, backend, meta)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (kind, supplier, period, source_name, safe, stored, sha, len(data), be.name,
         json.dumps(meta or {})))
    con.commit(); con.close()
    return stored


def put_extraction(draft, source_name, backend_name):
    """Archive an AI extraction draft (JSON, binary stripped) into the lake."""
    clean = {k: v for k, v in (draft or {}).items() if not str(k).startswith("_")}
    blob = json.dumps(clean, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    base = os.path.splitext(os.path.basename(source_name or "extract"))[0]
    return put(blob, base + ".json", kind="ai_extract",
               supplier=(draft or {}).get("supplier"),
               period=(draft or {}).get("statement_date", "")[:7] or None,
               source_name=source_name,
               meta={"backend": backend_name, "confidence": (draft or {}).get("confidence"),
                     "lines": len((draft or {}).get("lines", []))})


def get(locator):
    """Read a lake file's bytes (routes by locator prefix, like the document vault)."""
    return document_vault.get_bytes(locator, LAKE_DIR)


def query(kind=None, supplier=None, period=None, limit=500):
    """List lake artifacts (metadata only) so any module can find files to process."""
    con = connect()
    w, p = ["1=1"], []
    if kind:     w.append("kind=?");     p.append(kind)
    if supplier: w.append("supplier=?"); p.append(supplier)
    if period:   w.append("period=?");   p.append(period)
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM data_lake_files WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT ?",
        p + [limit])]
    con.close()
    return rows


def get_file(file_id):
    """Return (metadata, bytes) for one artifact, or (None, None)."""
    con = connect()
    r = con.execute("SELECT * FROM data_lake_files WHERE id=?", (file_id,)).fetchone()
    con.close()
    if not r:
        return None, None
    return dict(r), get(r["stored_path"])


def counts():
    con = connect()
    rows = con.execute("SELECT kind, COUNT(*) n, SUM(size) b FROM data_lake_files GROUP BY kind").fetchall()
    con.close()
    return {r["kind"]: {"files": r["n"], "bytes": r["b"] or 0} for r in rows}


def parser_priority(con=None):
    """Mine the AI-extraction archive to rank which suppliers most need a deterministic
    ``parse_<x>()`` in extract.py's PARSER registry (Phase-3 parser priorities).

    Every ``kind='ai_extract'`` row is a PDF that fell THROUGH to the AI backend because
    no deterministic parser existed for it. Aggregating those per supplier — weighted by
    volume and LOW confidence — surfaces the best parser-build ROI: each new parser
    removes that supplier's PDFs from the AI path. ``confidence`` lives inside the per-row
    ``meta`` JSON (values like "high"/"medium"/"low", or absent/None).

    Read-only; NEVER raises (a bad/empty/None ``meta`` row is tolerated, not fatal — on
    any internal error returns ``[]``). Accepts an optional open connection; opens/closes
    its own when ``con is None``.

    Returns a list of dicts, one per supplier, sorted by ``weighted_score`` desc then
    ``ai_count`` desc. Each dict has the shape::

        {"supplier": str,        # NULL/empty supplier collapses to "(unknown)"
         "ai_count": int,        # number of AI extractions for that supplier
         "high": int, "medium": int, "low": int, "unknown": int,  # confidence histogram
         "backends": [str, ...], # distinct AI backend(s) seen (meta["backend"], else
                                 #   the storage-backend column), sorted (context)
         "weighted_score": int}  # priority heuristic (see weights below)

    Priority heuristic (favours HIGH VOLUME + LOW CONFIDENCE)::

        weighted_score = low*3 + unknown*2 + medium*2 + high*1

    so a supplier with many low-confidence AI extractions ranks highest (best ROI for a
    new parser), and equal-confidence suppliers rank by sheer volume.
    """
    own = con is None
    try:
        if own:
            con = connect()
        rows = con.execute(
            "SELECT supplier, backend, meta FROM data_lake_files WHERE kind='ai_extract'"
        ).fetchall()
    except Exception:
        return []
    finally:
        if own and con is not None:
            con.close()

    agg = {}
    for r in rows:
        sup = (r["supplier"] or "").strip() or "(unknown)"
        a = agg.get(sup)
        if a is None:
            a = agg[sup] = {"supplier": sup, "ai_count": 0,
                            "high": 0, "medium": 0, "low": 0, "unknown": 0,
                            "_backends": set()}
        a["ai_count"] += 1
        conf, meta_be = None, None
        try:
            m = json.loads(r["meta"]) if r["meta"] else {}
            if isinstance(m, dict):
                conf = m.get("confidence")
                meta_be = m.get("backend")           # the AI backend; the column is storage
        except Exception:
            conf, meta_be = None, None
        bucket = str(conf).strip().lower() if conf is not None else ""
        if bucket not in ("high", "medium", "low"):
            bucket = "unknown"
        a[bucket] += 1
        be = (str(meta_be) if meta_be else (r["backend"] or "")).strip()
        if be:
            a["_backends"].add(be)

    out = []
    for a in agg.values():
        a["weighted_score"] = a["low"] * 3 + a["unknown"] * 2 + a["medium"] * 2 + a["high"] * 1
        a["backends"] = sorted(a.pop("_backends"))
        out.append(a)
    out.sort(key=lambda x: (x["weighted_score"], x["ai_count"]), reverse=True)
    return out


def delete(file_id):
    """EXPLICITLY remove an artifact (the only way a file leaves the lake besides a
    corruption removal). Drops the stored bytes and the index row. Returns True if a
    row was removed."""
    con = connect()
    r = con.execute("SELECT stored_path FROM data_lake_files WHERE id=?", (file_id,)).fetchone()
    if not r:
        con.close()
        return False
    try:
        document_vault.delete(r["stored_path"], LAKE_DIR)
    except Exception as e:
        log.warning("data_lake: could not delete stored bytes for id %s (%s): %s",
                    file_id, r["stored_path"], e)
    con.execute("DELETE FROM data_lake_files WHERE id=?", (file_id,))
    con.commit(); con.close()
    return True


def delete_locator(stored_path):
    """Remove an artifact by its stored locator. Used to PURGE a bad upload whose stored
    copy failed verification, so corrupt/partial data never lingers in the lake. Returns
    the number of things removed (index rows and/or the physical bytes)."""
    if not stored_path:
        return 0
    con = connect()
    ids = [r["id"] for r in con.execute(
        "SELECT id FROM data_lake_files WHERE stored_path=?", (stored_path,))]
    con.close()
    n = 0
    for fid in ids:
        if delete(fid):
            n += 1
    if not ids:
        # No index row (e.g. bytes written but the index insert failed) — still try to
        # drop the physical bytes so nothing bad is left behind.
        try:
            document_vault.delete(stored_path, LAKE_DIR); n += 1
        except Exception as e:
            log.warning("data_lake: could not delete orphan bytes %s: %s", stored_path, e)
    return n


def verify():
    """Integrity check: re-read every stored artifact and compare its SHA-256 to the
    recorded hash. Returns (rows, summary{total, ok, corrupt, missing}). A file is only
    ever LOST through an explicit delete() or being flagged here as corrupt/missing."""
    con = connect()
    rows = con.execute("SELECT id, kind, supplier, filename, stored_path, sha256, size "
                       "FROM data_lake_files ORDER BY id").fetchall()
    con.close()
    out, summ = [], {"total": 0, "ok": 0, "corrupt": 0, "missing": 0}
    for r in rows:
        summ["total"] += 1
        status, detail = "OK", ""
        try:
            actual = hashlib.sha256(get(r["stored_path"])).hexdigest()
            if actual != r["sha256"]:
                status, detail = "CORRUPT", f"hash {actual[:8]} != {r['sha256'][:8]}"
        except FileNotFoundError:
            status = "MISSING"
        except Exception as e:
            status, detail = "MISSING", str(e)[:60]
        summ[{"OK": "ok", "CORRUPT": "corrupt", "MISSING": "missing"}[status]] += 1
        out.append({"id": r["id"], "kind": r["kind"], "supplier": r["supplier"],
                    "filename": r["filename"], "status": status, "detail": detail,
                    "size": r["size"]})
    return out, summ


if __name__ == "__main__":
    import tempfile, shutil
    d = tempfile.mkdtemp()
    LAKE_DIR = os.path.join(d, "data_lake"); DB = os.path.join(d, "data_lake.db"); _READY.clear()
    loc = put_extraction({"supplier": "DKV", "statement_date": "2026-05-31",
                          "confidence": "medium", "lines": [{"net": 100}],
                          "_pdf_bytes": [b"x"]}, "DKV_May.pdf", "claude")
    print("stored:", loc)
    meta, data = get_file(query(kind="ai_extract")[0]["id"])
    assert b"_pdf_bytes" not in data and b"DKV" in data
    assert put_extraction({"supplier": "DKV", "statement_date": "2026-05-31"},
                          "DKV_May.pdf", "claude")  # identical -> dedup, no error
    print("counts:", counts())
    shutil.rmtree(d, ignore_errors=True)
    print("data_lake self-test OK")
