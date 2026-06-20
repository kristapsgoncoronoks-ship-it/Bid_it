"""
READ-FIRST intake + AUTO-ONBOARD of an unknown supplier + discoverable review.

Covers:
  • period derivation from the invoice/statement date (no manual field needed);
  • the VAT-prefix -> ISO country map and country_from_vat();
  • an unknown supplier with a CZ VAT id -> enqueue_onboard -> the engine worker creates
    a PROVISIONAL CZ supplier (the registration path can then resolve it);
  • a garbage / VAT-less draft does NOT create a supplier (stays UNMATCHED);
  • the provisional-supplier admin list + activate -> engine activates it;
  • /queue/review/<id> shows a READ-ONLY draft for a DONE job that produced one;
  • the provisional admin surface escapes XSS.

The worker body runs IN-PROCESS (no live thread) against throwaway temp DBs.
"""
import importlib

import pytest


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """Point every product DB the onboarding/registration path touches at temp files and
    reset the per-process schema caches so the temp DBs get a fresh schema."""
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()

    import supplier_master, import_log
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()
    return waiting_room


# ---------------------------------------------------------------------------
# VAT-prefix -> country map
# ---------------------------------------------------------------------------
def test_country_from_vat_prefix_map():
    import supplier_master as SM
    assert SM.country_from_vat("CZ12345678") == "CZ"
    assert SM.country_from_vat("DE123456789") == "DE"
    assert SM.country_from_vat("el123456789") == "GR"      # EL prefix -> ISO GR
    assert SM.country_from_vat(" cz 123 ") == "CZ"          # tolerant of spaces/case
    assert SM.country_from_vat("XX99") is None              # unknown prefix -> None
    assert SM.country_from_vat("") is None
    assert SM.country_from_vat(None) is None


# ---------------------------------------------------------------------------
# period derivation (read-first: no manual field)
# ---------------------------------------------------------------------------
def test_derive_period_from_statement_then_lines_then_default():
    import app as A
    # statement_date wins
    assert A._derive_period({"statement_date": "2026-05-31", "lines": []}) == "2026-05"
    # else earliest line date
    assert A._derive_period({"statement_date": None,
                             "lines": [{"date": "2026-04-10"}]}) == "2026-04"
    # an explicit override always wins
    assert A._derive_period({"statement_date": "2026-05-31"}, override="2026-01") == "2026-01"


# ---------------------------------------------------------------------------
# AUTO-ONBOARD: unknown supplier w/ CZ VAT -> provisional CZ supplier
# ---------------------------------------------------------------------------
def test_onboard_creates_provisional_supplier(engine):
    import supplier_master as SM
    jid, st = engine.enqueue_onboard("CZFUEL", "CZ", legal_name="CZ Fuel s.r.o.",
                                     vat_number="CZ12345678", invoice_ref="INV-1",
                                     user="alice")
    assert st == "queued"
    assert engine.process_one() == (jid, "done")
    assert engine.process_one() is None

    con = SM.connect()
    row = con.execute("SELECT * FROM suppliers WHERE code=?", ("CZFUEL",)).fetchone()
    assert row is not None
    assert row["status"] == "provisional"                  # NOT active
    assert row["home_country"] == "CZ"
    assert "auto-onboarded from invoice INV-1" in (row["notes"] or "")
    # the VAT registration was recorded for the home country
    vat = con.execute("""SELECT vat_number FROM supplier_vat_registrations
                         WHERE supplier=? AND country=?""", ("CZFUEL", "CZ")).fetchone()
    assert vat is not None and vat["vat_number"] == "CZ12345678"
    # audit attributes the write to the confirming user, not 'system'
    by = [r["changed_by"] for r in con.execute(
        "SELECT changed_by FROM audit_log WHERE tbl='suppliers'")]
    con.close()
    assert by and all(b == "alice" for b in by), f"audit actor not propagated: {by}"


def test_onboarded_supplier_is_registerable(engine):
    """After onboarding, a statement registers against the new provisional supplier — i.e.
    the draft is no longer UNMATCHED, it resolves to a real supplier code."""
    import supplier_master as SM
    j1, _ = engine.enqueue_onboard("CZFUEL", "CZ", vat_number="CZ999", user="amy")
    assert engine.process_one() == (j1, "done")
    assert SM.supplier_exists("CZFUEL") is True

    j2, _ = engine.enqueue_registration({
        "supplier": "CZFUEL", "statement_ref": "S-CZ-1", "period": "2026-05",
        "statement_date": "2026-05-31",
        "lines": [["CZ001", "2026-05-31", "Czechia", "EUR", 500.0, 105.0]],
        "customer": "OUR ENTITY", "notes": "x", "draft": "t",
    }, user="amy")
    assert engine.process_one() == (j2, "done")
    con = SM.connect()
    n = con.execute("SELECT COUNT(*) FROM supplier_statements WHERE supplier=?",
                    ("CZFUEL",)).fetchone()[0]
    con.close()
    assert n == 1


def test_onboard_idempotent_and_never_clobbers(engine):
    import supplier_master as SM
    # seed a CONFIRMED supplier directly, then a re-onboard must NOT downgrade it
    con = SM.connect()
    SM.create_provisional_supplier("CZFUEL", home_country="CZ", con=con)
    con.execute("UPDATE suppliers SET status='active' WHERE code=?", ("CZFUEL",))
    con.commit(); con.close()

    jid, _ = engine.enqueue_onboard("CZFUEL", "CZ", vat_number="CZ1", user="bob")
    assert engine.process_one() == (jid, "done")          # no-op, still done
    con = SM.connect()
    status = con.execute("SELECT status FROM suppliers WHERE code=?",
                         ("CZFUEL",)).fetchone()["status"]
    con.close()
    assert status == "active", "re-onboard must not downgrade a confirmed supplier"


def test_create_provisional_requires_country():
    import supplier_master as SM
    with pytest.raises(ValueError):
        SM.create_provisional_supplier("X", home_country=None)


# ---------------------------------------------------------------------------
# garbage / VAT-less draft -> NO supplier invented (stays UNMATCHED)
# ---------------------------------------------------------------------------
def test_vatless_draft_does_not_onboard(monkeypatch, tmp_path):
    """The read-first notice must NOT enqueue an onboard job for an unknown supplier with
    no usable VAT id — it leaves the draft UNMATCHED."""
    import app as A, waiting_room as IQ
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    # an unknown supplier: force the existence check to 'unknown'
    monkeypatch.setattr(A, "_supplier_known", lambda code: False)

    draft = {"supplier": "MYSTERY", "supplier_vat": "", "statement_ref": "R1",
             "statement_date": "2026-05-31", "lines": []}
    with A.app.test_request_context("/extract"):
        html = A._read_first_notice(draft, "2026-05")
    assert "UNMATCHED" in html
    # nothing was enqueued
    assert [j for j in IQ.jobs() if j.get("kind") == IQ.KIND_ONBOARD] == []


def test_unknown_supplier_with_vat_enqueues_onboard(monkeypatch, tmp_path):
    import app as A, waiting_room as IQ
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    monkeypatch.setattr(A, "_supplier_known", lambda code: False)

    draft = {"supplier": "CZFUEL", "supplier_vat": "CZ12345678", "statement_ref": "R1",
             "statement_date": "2026-05-31", "lines": []}
    with A.app.test_request_context("/extract"):
        html = A._read_first_notice(draft, "2026-05")
    assert "provisional" in html.lower()
    reg = [j for j in IQ.jobs() if j.get("kind") == IQ.KIND_ONBOARD]
    assert len(reg) == 1 and reg[0]["status"] == "queued"


def test_vat_in_name_field_not_unmatched(monkeypatch, tmp_path):
    """REGRESSION: the extractor put a VAT id (LV…) in the supplier-NAME field with no
    separate VAT captured. It must NOT be left UNMATCHED — the VAT-shaped name is promoted
    to the effective VAT so an unknown supplier is auto-onboarded by its country (LV)."""
    import app as A, waiting_room as IQ
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    monkeypatch.setattr(A, "_supplier_known", lambda code: False)
    # force 'unknown' so we exercise the onboard path (not a demo-DB match)
    monkeypatch.setattr(A, "_resolve_supplier_code", lambda name, vat=None: None)

    draft = {"supplier": "LV43603043473", "supplier_vat": "", "statement_ref": "BE95489/5413791",
             "statement_date": "2026-03-31", "lines": []}
    with A.app.test_request_context("/extract"):
        html = A._read_first_notice(draft, "2026-03")
    assert "UNMATCHED" not in html
    assert "provisional" in html.lower()
    reg = [j for j in IQ.jobs() if j.get("kind") == IQ.KIND_ONBOARD]
    assert len(reg) == 1 and reg[0]["status"] == "queued"


# ---------------------------------------------------------------------------
# provisional admin surface: list + activate + XSS escaping
# ---------------------------------------------------------------------------
def test_provisional_list_and_activate(engine):
    import supplier_master as SM
    con = SM.connect()
    SM.create_provisional_supplier("CZFUEL", home_country="CZ", con=con)
    provs = SM.list_provisional(con=con)
    con.close()
    assert [p["code"] for p in provs] == ["CZFUEL"]

    jid, _ = engine.enqueue_activate("CZFUEL", user="admin")
    assert engine.process_one() == (jid, "done")
    con = SM.connect()
    status = con.execute("SELECT status FROM suppliers WHERE code=?",
                         ("CZFUEL",)).fetchone()["status"]
    assert SM.list_provisional(con=con) == []
    con.close()
    assert status == "active"


def test_provisional_card_escapes_xss(monkeypatch):
    import app as A
    payload = '<script>alert(1)</script>'
    monkeypatch.setattr("supplier_master.list_provisional",
                        lambda con=None: [{"code": "X", "legal_name": payload,
                                           "home_country": "CZ", "notes": payload}])
    with A.app.test_request_context("/suppliers"):
        from flask import session
        session["role"] = "admin"
        html = A._provisional_suppliers_card()
    assert payload not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# discoverable review: a DONE job that produced a draft still shows it
# ---------------------------------------------------------------------------
def test_done_job_shows_read_only_draft(engine, monkeypatch):
    import extract as EX
    monkeypatch.setattr(EX, "extract", lambda data, name, backend=None, strict=False: {
        "supplier": "ACME", "supplier_vat": "CZ1", "statement_ref": "R9",
        "statement_date": "2026-05-31", "currency": "EUR",
        "lines": [{"invoice_no": "I1", "date": "2026-05-31", "country": "Czechia",
                   "net": 100, "vat": 21, "_source": "parser"}],
        "backend": "stub", "confidence": "high", "_pdf_bytes": [("a.pdf", b"%PDF")]})
    jid, _ = engine.enqueue(b"%PDF-1.4 x", "a.pdf", backend="none")
    assert engine.process_one() == (jid, "ready")
    # commit/complete it -> done, but the draft column survives
    assert engine.complete(jid) is True
    assert engine.get_job(jid)["status"] == "done"

    stored = engine.get_stored_draft(jid)
    assert stored is not None and stored["supplier"] == "ACME"
    # the read-only renderer surfaces the read fields + escapes
    import app as A
    html = A._read_only_draft_view(engine.get_job(jid), stored)
    assert "ACME" in html and "I1" in html and "read-only" in html.lower()
