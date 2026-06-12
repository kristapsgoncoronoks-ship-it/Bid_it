"""
Web-layer smoke tests: every GET page serves 200 when logged in, and the CSRF
guard rejects tokenless POSTs while accepting tokened ones.
"""
import re


def _get_routes():
    import app as A
    routes = []
    for rule in A.app.url_map.iter_rules():
        p = str(rule.rule)
        if "GET" not in rule.methods or "<" in p:
            continue
        # export_fee needs a claim's query args, export_master needs a generated
        # workbook on disk; both legitimately 404 without those, so skip the smoke.
        if rule.endpoint in ("static", "logout", "export_fee", "export_master"):
            continue
        routes.append(p)
    return sorted(set(routes))


def test_all_get_pages_200(client):
    failures = []
    for path in _get_routes():
        r = client.get(path)
        # /setup self-redirects to /login once an admin exists
        if r.status_code == 200:
            continue
        if path == "/setup" and r.status_code == 302:
            continue
        failures.append((path, r.status_code))
    assert not failures, f"non-200 routes: {failures}"


def test_csrf_rejects_tokenless_post(client):
    r = client.post("/data", data={"_dbk": "x"})
    assert r.status_code == 400, f"expected 400 (CSRF), got {r.status_code}"


def test_csrf_token_present_in_forms(client):
    # A representative POST form must carry the hidden _csrf field.
    html = client.get("/data").get_data(as_text=True)
    assert 'name="_csrf"' in html, "data page form missing CSRF token"


def test_intake_queue_flow(client, monkeypatch, tmp_path):
    """Upload via the waiting room -> job is parked -> drain makes it ready ->
    the review screen opens with the intake_job marker for confirm."""
    import io, re
    import waiting_room as IQ
    import extract as EX
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    monkeypatch.setattr(EX, "extract", lambda data, name, backend=None, strict=False: {
        "supplier": "DEMO", "statement_ref": "S1", "statement_date": "2026-05-01",
        "lines": [{"invoice_no": "INV1", "net": 100, "vat": 21, "country": "DE"}],
        "backend": backend or "stub", "confidence": "low", "_pdf_bytes": [(name, data)]})

    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/extract").get_data(as_text=True)).group(1)
    r = client.post("/extract", data={
        "_csrf": tok, "__mode": "queue", "backend": "none", "period": "2026-05",
        "file": (io.BytesIO(b"%PDF-1.4 demo invoice"), "batch.pdf")},
        content_type="multipart/form-data")
    assert r.status_code == 302 and "/queue" in r.headers["Location"]
    assert IQ.counts()["queued"] == 1

    # the waiting room lists the queued job
    qhtml = client.get("/queue").get_data(as_text=True)
    assert "batch.pdf" in qhtml and "Document waiting room" in qhtml

    # background worker equivalent: drain -> ready
    assert IQ.drain() == 1
    jid = IQ.jobs()[0]["id"]
    assert IQ.get_job(jid)["status"] == "ready"

    # the review screen opens with the intake_job marker so confirm can close it
    rev = client.get(f"/queue/review/{jid}").get_data(as_text=True)
    assert 'name="intake_job"' in rev and "DEMO" in rev and "INV1" in rev


def test_upload_ok_confirms_and_sends_for_processing(client, monkeypatch, tmp_path):
    """A good batch is confirmed OK, durably archived, and sent for processing (queue
    mode redirects to the waiting room with an 'Upload OK' message)."""
    import io, re
    import waiting_room as IQ
    import data_lake as DL, import_log as IL
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    monkeypatch.setattr(DL, "LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setattr(DL, "DB", str(tmp_path / "lake.db")); DL._READY.clear()
    monkeypatch.setattr(IL, "DB", str(tmp_path / "imp.db")); IL._READY.clear()

    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/extract").get_data(as_text=True)).group(1)
    r = client.post("/extract", data={
        "_csrf": tok, "__mode": "queue", "backend": "none", "period": "2026-05",
        "file": (io.BytesIO(b"%PDF-1.4 good batch"), "good.pdf")},
        content_type="multipart/form-data")
    assert r.status_code == 302 and "Upload+OK" in r.headers["Location"]
    assert IQ.counts()["queued"] == 1                      # sent for processing
    assert DL.counts()["raw_upload"]["files"] == 1         # durably archived
    assert IL.recent(limit=1)[0]["status"] == "received"   # logged OK


def test_upload_bad_rejected_purged_and_resubmit(client, monkeypatch, tmp_path):
    """A batch whose stored copy fails verification is REJECTED: nothing is processed,
    the bad data is purged from the lake, and the user is told to re-upload the whole
    batch."""
    import io, re
    import data_lake as DL, import_log as IL
    monkeypatch.setattr(DL, "LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setattr(DL, "DB", str(tmp_path / "lake.db")); DL._READY.clear()
    monkeypatch.setattr(IL, "DB", str(tmp_path / "imp.db")); IL._READY.clear()
    # force the verification read-back to return the wrong bytes -> hash mismatch
    monkeypatch.setattr(DL, "get", lambda loc: b"CORRUPTED")

    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/extract").get_data(as_text=True)).group(1)
    r = client.post("/extract", data={
        "_csrf": tok, "__mode": "now", "backend": "none", "period": "2026-05",
        "file": (io.BytesIO(b"%PDF-1.4 real bytes"), "bad.pdf")},
        content_type="multipart/form-data")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "batch rejected" in body and "re-upload the entire batch" in body
    assert DL.query() == []                                # bad data purged
    assert IL.recent(limit=1)[0]["status"] == "failed"     # logged Bad


def test_upload_empty_file_rejected(client, monkeypatch, tmp_path):
    """An empty upload is rejected with the resubmit message and stores nothing."""
    import io, re
    import data_lake as DL, import_log as IL
    monkeypatch.setattr(DL, "LAKE_DIR", str(tmp_path / "lake"))
    monkeypatch.setattr(DL, "DB", str(tmp_path / "lake.db")); DL._READY.clear()
    monkeypatch.setattr(IL, "DB", str(tmp_path / "imp.db")); IL._READY.clear()
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/extract").get_data(as_text=True)).group(1)
    r = client.post("/extract", data={
        "_csrf": tok, "__mode": "now", "backend": "none", "period": "2026-05",
        "file": (io.BytesIO(b""), "empty.pdf")},
        content_type="multipart/form-data")
    body = r.get_data(as_text=True)
    assert "batch rejected" in body and "re-upload the entire batch" in body
    assert DL.query() == []


def test_intake_upload_gating_and_override(client, monkeypatch, tmp_path):
    """While the waiting room has unprocessed docs, queueing a new one is blocked;
    an admin temporary override lets it through; 'Send / restart all' clears it."""
    import io, re
    import waiting_room as IQ
    import extract as EX
    import auth
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    auth.set_setting("intake_override_until", "0")        # clean slate

    # extractor is "out of tokens" -> first queued doc gets stuck (held)
    state = {"broke": True}
    def maybe(data, name, backend=None, strict=False):
        if state["broke"] and strict:
            raise EX.TransientExtractionError("openai: insufficient_quota")
        return {"supplier": "D", "lines": [], "backend": "stub", "_pdf_bytes": [(name, data)]}
    monkeypatch.setattr(EX, "extract", maybe)
    monkeypatch.setattr(IQ, "MAX_TOKEN_RETRIES", 1)

    def csrf(path="/extract"):
        return re.search(r'name="_csrf" value="([^"]+)"',
                         client.get(path).get_data(as_text=True)).group(1)

    def queue_upload(name):
        return client.post("/extract", data={
            "_csrf": csrf(), "__mode": "queue", "backend": "openai", "period": "2026-05",
            "file": (io.BytesIO(b"%PDF-1.4 " + name.encode()), name)},
            content_type="multipart/form-data")

    assert queue_upload("one.pdf").status_code == 302    # first upload accepted
    assert IQ.drain() == 1 and IQ.counts()["held"] == 1  # stuck -> backlog of 1

    # second upload is now BLOCKED (backlog present, no override)
    r = queue_upload("two.pdf")
    assert r.status_code == 200 and "New uploads are paused" in r.get_data(as_text=True)
    assert IQ.pending_count() == 1                        # two.pdf was NOT added

    # admin enables the temporary override -> upload now goes through
    r = client.post("/queue", data={"_csrf": csrf("/queue"), "__act": "override_on"})
    assert "override enabled" in r.get_data(as_text=True).lower()
    assert queue_upload("three.pdf").status_code == 302

    # tokens are back; "Send / restart all" clears the whole backlog
    state["broke"] = False
    r = client.post("/queue", data={"_csrf": csrf("/queue"), "__act": "send_all"})
    assert "Restarted" in r.get_data(as_text=True)
    assert IQ.pending_count() == 0
    auth.set_setting("intake_override_until", "0")        # don't leak override state


def test_vat_module_is_admin_only(admin_session):
    """The VAT-refund module (claims/readiness/recovery) is admin-only; a processor is
    blocked and the nav link is hidden."""
    import app as A, auth
    auth.add_user("vatproc", "Pw!23456", role="processor")
    cp = A.app.test_client()
    cp.post("/login", data={"username": "vatproc", "password": "Pw!23456"})
    for path in ("/vat", "/readiness", "/recovery"):
        assert cp.get(path).status_code == 403, path
    assert "VAT refunds" not in cp.get("/").get_data(as_text=True)


def test_admin_module_toggle(client):
    """An admin can switch whole parts of the app on/off in the Admin panel."""
    import re
    def tok():
        return re.search(r'name="_csrf" value="([^"]+)"',
                         client.get("/admin").get_data(as_text=True)).group(1)
    assert client.get("/fx").status_code == 200
    on = {"mod_analytics": "on", "mod_intake": "on", "mod_compliance": "on", "mod_vat": "on"}
    client.post("/admin", data={"_csrf": tok(), "__act": "set_modules", **on})  # fx omitted -> off
    assert client.get("/fx").status_code == 403
    assert client.get("/savings").status_code == 200       # analytics still on
    assert "FX vs ECB" not in client.get("/").get_data(as_text=True)
    # turn analytics off too -> its pages 403 and the menu disappears
    client.post("/admin", data={"_csrf": tok(), "__act": "set_modules",
                                "mod_intake": "on", "mod_compliance": "on", "mod_vat": "on"})
    assert client.get("/savings").status_code == 403
    assert ">Analytics<" not in client.get("/").get_data(as_text=True)
    # re-enable everything (don't leak state to other tests)
    client.post("/admin", data={"_csrf": tok(), "__act": "set_modules",
                                **on, "mod_analytics": "on", "mod_fx": "on"})
    assert client.get("/fx").status_code == 200 and client.get("/savings").status_code == 200


def test_worklist_card_actions(monkeypatch):
    import app as A
    import vat_refund as VR
    monkeypatch.setattr(VR, "claims_overview", lambda y: {
        "to_submit": [
            {"entity": "Acme", "country": "DE", "period": "2026-Q1",
             "vat_eur": 1500, "ready": True, "issues": []},
            {"entity": "Beta", "country": "PL", "period": "2026-Q1",
             "vat_eur": 800, "ready": False, "issues": ["country not activated"]},
        ], "open": []})
    monkeypatch.setattr(VR, "recovery_report", lambda y: ([
        {"entity": "Acme", "country": "DE", "period": "2025-Q4", "vat_eur": 1000,
         "status": "submitted", "age_days": 200, "paid_amount": None,
         "fee_eur": None, "fee_pct": 8, "fee_min": 130, "fee_billed_date": None,
         "payout_to": None, "fee_invoice_no": None},
        {"entity": "Acme", "country": "PL", "period": "2025-Q3", "vat_eur": 2000,
         "status": "paid", "age_days": "", "paid_amount": 2000,
         "fee_eur": 160, "fee_pct": 8, "fee_min": 130, "fee_billed_date": "2026-01-01",
         "payout_to": "customer", "fee_invoice_no": None},
    ], {}))
    html = A._worklist_card(2026)
    assert "Submit Acme" in html           # ready-to-submit
    assert "Unblock Beta" in html          # blocked, with reason
    assert "country not activated" in html
    assert "Chase Acme" in html            # aging > 120 days
    assert "Invoice fee for Acme" in html  # billed, payout customer, not invoiced


def test_worklist_card_empty(monkeypatch):
    import app as A
    import vat_refund as VR
    monkeypatch.setattr(VR, "claims_overview", lambda y: {"to_submit": [], "open": []})
    monkeypatch.setattr(VR, "recovery_report", lambda y: ([], {}))
    assert "Nothing outstanding" in A._worklist_card(2026)


def test_compare_multi_supplier_filter(client):
    import re
    html = client.get("/compare?period=ALL&supplier=Q8&supplier=BP").get_data(as_text=True)
    shown = set(re.findall(r"<td>(Q8|BP|TFC|E100|MOEVE|DKV)</td>", html))
    assert shown <= {"Q8", "BP"}, f"filter leaked other suppliers: {shown}"
    assert shown, "expected Q8/BP rows"


def test_compare_has_multiselect_and_totals(client):
    html = client.get("/compare").get_data(as_text=True)
    assert 'name="supplier" multiple' in html
    assert 'name="station" multiple' in html
    assert "TOTAL (" in html


def test_export_compare_returns_xlsx(client):
    r = client.get("/export/compare?period=ALL&supplier=Q8")
    assert r.status_code == 200
    assert r.get_data()[:2] == b"PK"  # xlsx is a zip


def test_processor_blocked_from_admin(admin_session):
    import app as A
    import auth
    auth.add_user("proc_test", "Pw!23456", role="processor")
    c = A.app.test_client()
    c.post("/login", data={"username": "proc_test", "password": "Pw!23456"})
    # no Admin nav link, and /admin is forbidden
    assert "/admin" not in c.get("/").get_data(as_text=True)
    assert c.get("/admin").status_code == 403
    # but normal operations are allowed by default
    assert c.get("/data").status_code == 200
    assert c.get("/pricing").status_code == 200


def test_admin_can_revoke_processor_capability(admin_session):
    import re
    import app as A
    import auth
    auth.add_user("proc2", "Pw!23456", role="processor")
    ca = A.app.test_client()
    ca.post("/login", data={"username": admin_session["user"], "password": admin_session["pw"]})
    adm = ca.get("/admin").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', adm).group(1)
    # save perms with pricing unchecked -> revoked
    ca.post("/admin", data={"_csrf": tok, "__act": "perms", "perm_data_import": "on",
                            "perm_invoice_control": "on", "perm_vat_claims": "on",
                            "perm_documents": "on", "perm_exports": "on"})
    cp = A.app.test_client()
    cp.post("/login", data={"username": "proc2", "password": "Pw!23456"})
    assert cp.get("/pricing").status_code == 403
    assert cp.get("/data").status_code == 200
    # restore so other tests/state are unaffected
    auth.set_permission("processor", "pricing", True)


def test_savings_page_renders_chart(client):
    html = client.get("/savings").get_data(as_text=True)
    assert "<svg" in html
    assert "Avoidable overpay" in client.get("/").get_data(as_text=True)


def test_transactions_drilldown(client):
    r = client.get("/transactions?period=ALL&supplier=Q8")
    assert r.status_code == 200
    assert "Transactions —" in r.get_data(as_text=True)


def test_export_stations_xlsx(client):
    r = client.get("/export/stations")
    assert r.status_code == 200 and r.get_data()[:2] == b"PK"


def test_login_required_redirect():
    import app as A
    c = A.app.test_client()  # not logged in
    r = c.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_vat_advance_and_withdraw_routes(client, monkeypatch):
    """The /vat POST wires the status dropdown (with note/deadline) to set_status_code
    and the withdraw button to withdraw_claim."""
    import vat_refund as VR
    calls = {}
    monkeypatch.setattr(VR, "set_status_code",
                        lambda con, e, c, p, code, note=None, deadline=None:
                        calls.update(adv=(e, c, p, code, note, deadline)) or (True, "status → 2 Submitted"))
    monkeypatch.setattr(VR, "withdraw_claim",
                        lambda con, e, c, p: calls.update(wd=(e, c, p)) or (True, "status -> withdrawn"))
    import re
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/vat").get_data(as_text=True)).group(1)
    r = client.post("/vat", data={"_csrf": tok, "entity": "Acme", "country": "DE",
                                  "ref_period": "2026-Q1", "status": "2",
                                  "note": "filed via portal", "deadline": ""})
    assert "status → 2 Submitted" in r.get_data(as_text=True)
    assert calls["adv"] == ("Acme", "DE", "2026-Q1", "2", "filed via portal", None)
    r = client.post("/vat", data={"_csrf": tok, "__act": "withdraw", "entity": "Acme",
                                  "country": "DE", "ref_period": "2026-Q1", "status": ""})
    assert "withdrawn" in r.get_data(as_text=True) and calls["wd"] == ("Acme", "DE", "2026-Q1")


def test_customers_template_upload_generate_and_unfilled_warning(client, monkeypatch, tmp_path):
    """Template upload -> generate fills customer data; unfilled fields warn on screen
    first and 'force' downloads anyway."""
    import io, re
    import customer_master as CD
    monkeypatch.setattr(CD, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(CD, "_SCHEMA_READY", set())
    monkeypatch.setattr(CD, "DOCDIR", str(tmp_path / "docs"))
    CD.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123")

    def tok():
        return re.search(r'name="_csrf" value="([^"]+)"',
                         client.get("/customers").get_data(as_text=True)).group(1)

    r = client.post("/customers", data={
        "_csrf": tok(), "__act": "add_template", "tname": "Contract", "tkind": "signed_contract",
        "file": (io.BytesIO(b"Contract: {{company_name}} reg {{reg_number}} sign {{signer}}"),
                 "contract.txt")}, content_type="multipart/form-data")
    assert "uploaded" in r.get_data(as_text=True)
    con = CD.connect(); tid = CD.list_templates(con)[0]["id"]; con.close()

    # unfilled {{signer}} -> on-screen warning, no download
    r = client.post("/customers", data={"_csrf": tok(), "__act": "gen_doc", "code": "ACME",
                                        "tid": str(tid), "gen_country": "", "file_as": ""})
    body = r.get_data(as_text=True)
    assert "field(s) have no data" in body and "signer" in body and "Download anyway" in body

    # force -> the file downloads, customer data filled
    r = client.post("/customers", data={"_csrf": tok(), "__act": "gen_doc", "code": "ACME",
                                        "tid": str(tid), "gen_country": "", "file_as": "",
                                        "force": "1"})
    assert r.headers.get("Content-Disposition", "").startswith("attachment")
    out = r.get_data(as_text=True)
    assert "Acme SIA" in out and "LV123" in out and "{{signer}}" in out
