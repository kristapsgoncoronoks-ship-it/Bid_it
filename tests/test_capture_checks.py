"""Capture-time deterministic checks (A2): IBAN MOD-97, VAT-ID structure, duplicates.

Advisory only — these surface findings to the reviewer; they never mutate a figure or
flip can_commit. Pure and offline."""
import capture_checks as CC


# ---------------------------------------------------------------- IBAN MOD-97
def test_iban_valid_real_examples():
    for good in ("DE89 3704 0044 0532 0130 00", "GB82WEST12345698765432",
                 "FR1420041010050500013M02606", "LV80BANK0000435195001",
                 "EE382200221020145685"):
        assert CC.iban_valid(good), good


def test_iban_rejects_bad_check_digit_and_length():
    assert not CC.iban_valid("DE89370400440532013001")     # last digit wrong -> MOD-97 fails
    assert not CC.iban_valid("DE8937040044053201300")      # too short for DE (22)
    assert not CC.iban_valid("XX00")                        # structurally invalid
    assert not CC.iban_valid("")                            # empty


def test_iban_finding_only_on_present_and_bad():
    assert CC.iban_finding("") is None
    assert CC.iban_finding("DE89370400440532013000") is None
    f = CC.iban_finding("DE89370400440532013001")
    assert f and f["severity"] == "error" and f["field"] == "iban"


# ---------------------------------------------------------------- VAT-ID structure
def test_vat_id_structure():
    assert CC.vat_id_valid("DE811569869") is True
    assert CC.vat_id_valid("ATU12345678") is True
    assert CC.vat_id_valid("NL123456789B01") is True
    assert CC.vat_id_valid("DE12345") is False             # too short for DE
    assert CC.vat_id_valid("XX123") is None                # unknown country -> can't judge
    assert CC.vat_id_valid("notavat") is None


def test_vat_id_finding_warns_only_on_malformed():
    assert CC.vat_id_finding("DE811569869") is None
    assert CC.vat_id_finding("") is None
    assert CC.vat_id_finding("XX999") is None              # uncheckable -> no cry-wolf
    f = CC.vat_id_finding("DE12345")
    assert f and f["severity"] == "warn"


def test_vies_check_is_offline_graceful():
    # no fetcher -> not checked, never raises
    r = CC.vies_check("DE811569869")
    assert r["checked"] is False and r["valid"] is None
    # injected fetcher that blows up -> degrades, still no raise
    r = CC.vies_check("DE811569869", fetcher=lambda *a: 1 / 0)
    assert r["checked"] is False
    # injected fetcher that confirms
    r = CC.vies_check("DE811569869", fetcher=lambda cc, body: True)
    assert r["checked"] is True and r["valid"] is True


# ---------------------------------------------------------------- duplicates
def test_in_batch_duplicate_normalized():
    lines = [{"invoice_no": "DE-001", "net": 100, "vat": 19},
             {"invoice_no": "DE 001", "net": 100, "vat": 19}]   # same after normalization
    f = CC.find_duplicates("DKV", lines)
    assert len(f) == 1 and f[0]["severity"] == "warn" and f[0]["invoice_no"] == "DE 001"


def test_prior_duplicate_is_error_cross_entity():
    seen = [CC.invoice_key("DKV", {"invoice_no": "OLD/1", "net": 50, "vat": 10})]
    lines = [{"invoice_no": "OLD-1", "net": 50, "vat": 10}]     # normalizes to the seen key
    f = CC.find_duplicates("DKV", lines, seen=seen)
    assert len(f) == 1 and f[0]["severity"] == "error"


def test_no_false_duplicate_on_different_amount():
    seen = [CC.invoice_key("DKV", {"invoice_no": "X1", "net": 50, "vat": 10})]
    lines = [{"invoice_no": "X1", "net": 51, "vat": 10}]        # same no, different net
    assert CC.find_duplicates("DKV", lines, seen=seen) == []


def test_blank_invoice_no_skipped():
    assert CC.find_duplicates("DKV", [{"invoice_no": "", "net": 1, "vat": 0}]) == []


# ---------------------------------------------------------------- combined + integration
def test_run_combines_all():
    findings = CC.run(
        [{"invoice_no": "A1", "net": 100, "vat": 19},
         {"invoice_no": "A1", "net": 100, "vat": 19}],
        supplier="DKV", supplier_vat="DE12345", iban="DE89370400440532013001",
        seen=())
    fields = {f["field"] for f in findings}
    assert fields == {"iban", "vat_id", "duplicate"}


def test_validate_batch_capture_is_advisory():
    import validate
    lines = [{"invoice_no": "A1", "date": "2026-05-31", "country": "Germany",
              "net": 100.0, "vat": 19.0}]
    base = validate.validate_batch(lines)
    assert "capture" not in base                            # omitted -> unchanged
    out = validate.validate_batch(lines, capture={
        "supplier": "DKV", "supplier_vat": "DE12345",
        "iban": "DE89370400440532013001", "seen": ()})
    assert out["can_commit"] == base["can_commit"]          # advisory: never flips commit
    assert any(f["field"] == "iban" for f in out["capture"])
