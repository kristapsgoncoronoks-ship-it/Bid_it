"""BULK 'Confirm all clean drafts' on the waiting room, and the period-close NUDGE.

6a — `app._bulk_confirm_ready_jobs` iterates every 'ready' intake job and registers ONLY
the clean ones: zero validation errors, no synthetic/placeholder line, and (when a
coversheet_total is present) a tie-out. Errored / synthetic / mismatched drafts are SKIPPED
(left 'ready'), never force-filed. Each confirmed job is marked done and a registration job
is enqueued (the SAME seam the single confirm uses, via autopilot.autofile).

6b — `app._period_close_nudge` returns the 'run the close' nudge ONLY when the active close
period has ≥1 registered statement AND nothing is still in flight in intake.
"""
import importlib
import json

import pytest


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Point the registration path's product DBs at throwaway temp files (mirrors the
    test_register_via_engine `engine` fixture) so the bulk confirm runs end-to-end without
    touching the real repo DBs."""
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()

    import supplier_master, customer_master, import_log, validate, vat_refund
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    customer_master._SCHEMA_READY.clear()
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()
    monkeypatch.setattr(validate, "DB", str(tmp_path / "fuel_history.db"))
    # the document vault is a WORKDIR-relative store (not FFS_DATA_DIR-redirected): point it
    # at a temp dir so the bulk-confirm vaulting never writes into the tracked documents/.
    monkeypatch.setattr(vat_refund, "DOCDIR", str(tmp_path / "documents"))
    return waiting_room


def _ready_job(IQ, ref, draft):
    """Create a 'ready' intake job carrying `draft` (the editable JSON draft). Enqueue a
    tiny upload then flip it to ready with the draft attached — same shape the worker writes
    (_strip_draft drops '_' keys; coversheet_total is a '_'-free key and survives)."""
    jid, _ = IQ.enqueue(b"%PDF-1.4 demo " + ref.encode(), f"{ref}.pdf",
                        backend="none", period="2026-05", user="tester")
    clean = {k: v for k, v in draft.items() if not str(k).startswith("_")}
    con = IQ.connect()
    con.execute("UPDATE intake_jobs SET status='ready', draft=? WHERE id=?",
                (json.dumps(clean), jid))
    con.commit(); con.close()
    return jid


def _draft(ref, net=1000.0, vat=210.0, invoice_no="BE001", coversheet_total=None):
    d = {"supplier": "DKV", "statement_ref": ref, "statement_date": "2026-05-31",
         "period": "2026-05", "customer": "OUR ENTITY",
         "lines": [{"invoice_no": invoice_no, "date": "2026-05-31", "country": "Belgium",
                    "currency": "EUR", "net": net, "vat": vat}]}
    if coversheet_total is not None:
        d["coversheet_total"] = coversheet_total
    return d


def test_bulk_confirm_registers_clean_and_skips_dirty(isolated, admin_session):
    import app as A
    IQ = isolated

    clean = _ready_job(IQ, "S-CLEAN", _draft("S-CLEAN"))
    clean_tie = _ready_job(IQ, "S-TIE-OK", _draft("S-TIE-OK", coversheet_total=1210.0))
    errored = _ready_job(IQ, "S-ERR", _draft("S-ERR", net=1000.0, vat=2000.0))   # vat>net
    synthetic = _ready_job(IQ, "S-SYN", _draft("S-SYN", invoice_no="UNMATCHED"))
    mismatch = _ready_job(IQ, "S-MIS", _draft("S-MIS", coversheet_total=9999.0))  # 1210!=9999

    confirmed, skipped, reasons = A._bulk_confirm_ready_jobs("tester")

    assert confirmed == 2, f"expected 2 clean drafts registered, got {confirmed} ({reasons})"
    assert skipped == 3, f"expected 3 skipped, got {skipped} ({reasons})"

    # the two clean jobs are now done; the three dirty ones stay 'ready' for manual review
    assert IQ.get_job(clean)["status"] == "done"
    assert IQ.get_job(clean_tie)["status"] == "done"
    assert IQ.get_job(errored)["status"] == "ready"
    assert IQ.get_job(synthetic)["status"] == "ready"
    assert IQ.get_job(mismatch)["status"] == "ready"

    # a registration job was enqueued for EACH confirmed statement (the same seam as confirm)
    reg_refs = set()
    for j in IQ.jobs():
        if j.get("kind") == IQ.KIND_REGISTER and j.get("payload"):
            reg_refs.add(json.loads(j["payload"]).get("statement_ref"))
    assert {"S-CLEAN", "S-TIE-OK"} <= reg_refs, f"registration not enqueued: {reg_refs}"

    # the skip reasons name the tie-out / synthetic / validation causes
    joined = " ".join(reasons)
    assert "tie-out" in joined
    assert "synthetic" in joined


def test_bulk_confirm_no_ready_jobs_is_noop(isolated, admin_session):
    import app as A
    confirmed, skipped, reasons = A._bulk_confirm_ready_jobs("tester")
    assert (confirmed, skipped, reasons) == (0, 0, {})


# --------------------------------------------------------------------------- 6b nudge
def test_close_nudge_only_when_period_fully_registered(client, monkeypatch, tmp_path):
    """The nudge fires only when the active close period has ≥1 registered statement AND
    nothing is still in flight in intake."""
    import app as A
    import month_config
    monkeypatch.setattr(month_config, "PERIOD", "2026-05")

    counts = {"queued": 0, "waiting": 0, "held": 0, "processing": 0,
              "ready": 0, "failed": 0, "done": 3}
    stmt_count = {"n": 0}

    import waiting_room
    monkeypatch.setattr(waiting_room, "counts", lambda: dict(counts))

    class _FakeCon:
        def execute(self, *a, **k):
            class _R:
                def fetchone(_self):
                    return [stmt_count["n"]]
            return _R()
        def close(self):
            pass

    import dataproduct
    monkeypatch.setattr(dataproduct, "connect", lambda which="fuel_history", path=None: _FakeCon())

    # no registered statements yet -> no nudge
    stmt_count["n"] = 0
    assert A._period_close_nudge() is None

    # statements registered, intake idle -> nudge fires
    stmt_count["n"] = 4
    msg = A._period_close_nudge()
    assert msg and "2026-05" in msg and "monthly close" in msg

    # statements registered BUT something still pending in intake -> no nudge
    counts["ready"] = 1
    assert A._period_close_nudge() is None
