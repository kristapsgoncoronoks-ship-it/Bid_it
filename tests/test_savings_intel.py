"""
SAVINGS & INTELLIGENCE — the consolidated fuel-cost intelligence surface (M2).

savings_intel.summary() is a THIN orchestrator: it sums what the three existing
detectors (pricing_intelligence.internal_benchmark / contract_audit.audit /
anomaly.find) already produce, quantizing the headline EUR figures HALF_UP via
money.f2/fsum, and exposes total_addressable_eur = overpay + recoverable.

We monkeypatch the three detectors to fixed rows so the test asserts the
ORCHESTRATION (sum + money quantization + total identity + ordering + escaping),
not any detection logic. We also assert the /intel page renders the escaped totals,
escapes an HTML-injection string in a country/action name, and never leaks any
VAT/claim figure.
"""
import re

import money


# Fixed detector outputs. Overpay rows mirror internal_benchmark()'s shape; breach
# flags mirror contract_audit.audit()'s shape. The cent figures are chosen so a naive
# float sum would drift from the HALF_UP money basis.
_OVERPAY_ROWS = [
    {"country": "Belgium", "bucket": "2026-05", "best_price": 1.3893,
     "best_supplier": "Q8", "suppliers": 2, "your_avg": 1.5402, "spread": 0.30,
     "litres": 94883.0, "overpay_eur": 14312.31},
    {"country": "Spain", "bucket": "2026-05", "best_price": 1.2538,
     "best_supplier": "Q8", "suppliers": 3, "your_avg": 1.3330, "spread": 0.12,
     "litres": 40509.0, "overpay_eur": 3208.36},
    # an injection string in the country name proves the page escapes DB values
    {"country": "<script>x</script>", "bucket": "2026-05", "best_price": 1.10,
     "best_supplier": "BP", "suppliers": 2, "your_avg": 1.15, "spread": 0.05,
     "litres": 1000.0, "overpay_eur": 12.34},
]
_OVERPAY_SUMM = {"total_overpay": money.f2(14312.31 + 3208.36 + 12.34),
                 "litres": 136392.0, "cells": 3, "multi_supplier_cells": 3}

_BREACH_FLAGS = [
    {"supplier": "Q8", "country": "Belgium", "station": "Antwerp", "period": "2026-05",
     "product": "Diesel", "litres": 5000.0, "issue": "short discount",
     "expected": 0.05, "actual": 0.03, "recover_eur": 100.05, "note": ""},
    {"supplier": "BP", "country": "Spain", "station": "Madrid", "period": "2026-05",
     "product": "Diesel", "litres": 2000.0, "issue": "over ceiling",
     "expected": 1.40, "actual": 1.45, "recover_eur": 50.02, "note": ""},
]
_BREACH_SUMM = {"total_recover": money.f2(100.05 + 50.02), "flags": 2,
                "by_supplier": {}, "rules": 2}

# anomaly.find() returns a list of (kind, level, msg) tuples; summary uses len().
_ANOMALY_FLAGS = [("station_price", "warn", "a"), ("off_period", "warn", "b"),
                  ("volume_spike", "warn", "c")]


def _patch_detectors(monkeypatch):
    import pricing_intelligence
    import contract_audit
    import anomaly
    import savings_intel
    monkeypatch.setattr(pricing_intelligence, "internal_benchmark",
                        lambda *a, **k: (_OVERPAY_ROWS, _OVERPAY_SUMM))
    monkeypatch.setattr(contract_audit, "audit",
                        lambda *a, **k: (_BREACH_FLAGS, _BREACH_SUMM))
    monkeypatch.setattr(anomaly, "find", lambda *a, **k: _ANOMALY_FLAGS)
    # a fixed period so summary() never touches the DB for _latest_period()
    monkeypatch.setattr(savings_intel, "_latest_period", lambda: "2026-05")


def test_summary_sums_with_money_quantization(monkeypatch):
    _patch_detectors(monkeypatch)
    import savings_intel
    s = savings_intel.summary()

    exp_overpay = money.fsum(r["overpay_eur"] for r in _OVERPAY_ROWS)
    exp_recover = money.fsum(f["recover_eur"] for f in _BREACH_FLAGS)
    assert s["avoidable_overpay_eur"] == money.f2(exp_overpay)
    assert s["recoverable_contract_eur"] == money.f2(exp_recover)
    assert s["anomaly_count"] == len(_ANOMALY_FLAGS)
    # the headline identity: total addressable = overpay + recoverable (HALF_UP)
    assert s["total_addressable_eur"] == money.f2(exp_overpay + exp_recover)
    assert s["total_addressable_eur"] == money.f2(
        s["avoidable_overpay_eur"] + s["recoverable_contract_eur"])


def test_by_country_breakdown_and_ordering(monkeypatch):
    _patch_detectors(monkeypatch)
    import savings_intel
    s = savings_intel.summary()
    by = {c["country"]: c for c in s["by_country"]}
    # Belgium = overpay 14312.31 + recover 100.05
    assert by["Belgium"]["overpay_eur"] == money.f2(14312.31)
    assert by["Belgium"]["recover_eur"] == money.f2(100.05)
    assert by["Belgium"]["addressable_eur"] == money.f2(14312.31 + 100.05)
    # Spain = overpay 3208.36 + recover 50.02
    assert by["Spain"]["addressable_eur"] == money.f2(3208.36 + 50.02)
    # sorted by addressable € descending
    addr = [c["addressable_eur"] for c in s["by_country"]]
    assert addr == sorted(addr, reverse=True)
    # per-country addressable totals reconcile to the headline
    assert money.f2(sum(c["addressable_eur"] for c in s["by_country"])) == \
        s["total_addressable_eur"]


def test_top_actions_ordered_by_eur(monkeypatch):
    _patch_detectors(monkeypatch)
    import savings_intel
    s = savings_intel.summary()
    eurs = [a["eur"] for a in s["top_actions"]]
    assert eurs == sorted(eurs, reverse=True)
    # one action per overpay row + one per breach flag
    assert len(s["top_actions"]) == len(_OVERPAY_ROWS) + len(_BREACH_FLAGS)


def test_summary_carries_no_vat_or_claim_keys(monkeypatch):
    """The surface is fuel-cost intelligence ONLY — its dict must expose no VAT/
    claim/recovery figure (those live on the admin-only recovery surface)."""
    _patch_detectors(monkeypatch)
    import savings_intel
    s = savings_intel.summary()
    blob = repr(s).lower()
    for forbidden in ("vat", "claim", "refund"):
        assert forbidden not in blob, f"{forbidden!r} leaked into the intel summary"


def test_intel_page_renders_escaped_totals(client, monkeypatch):
    _patch_detectors(monkeypatch)
    r = client.get("/intel")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)
    # headline totals are rendered (integer EUR formatting on the page)
    total = "{:,.0f}".format(money.f2(
        money.fsum(x["overpay_eur"] for x in _OVERPAY_ROWS)
        + money.fsum(f["recover_eur"] for f in _BREACH_FLAGS)))
    assert total in body
    # the injection string from the country name is ESCAPED, never raw
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;x&lt;/script&gt;" in body
    # NET-EUR/L basis is stated on the page
    assert "NET EUR/L" in body


def test_intel_page_does_not_leak_vat_figures(client, monkeypatch):
    """The /intel page must not surface any VAT-claim figure — the only allowed
    mention of VAT is the basis disclaimer ('VAT excluded' / 'VAT/claim figures are
    kept on the admin-only Recovery page')."""
    _patch_detectors(monkeypatch)
    body = client.get("/intel").get_data(as_text=True)
    # scope to the page CONTENT (<main>…</main>) — the shared header carries a "VAT
    # refunds" nav link on every page, which is chrome, not a figure on this surface.
    main = body[body.index("<main>"):body.index("</main>")]
    # strip the known disclaimer/basis sentences, then assert no other VAT mention
    cleaned = main.replace("VAT excluded", "").replace(
        "VAT/claim figures are kept on the admin-only Recovery page", "")
    assert not re.search(r"reclaimable vat|vat refund|refundable|claim", cleaned, re.I)


def test_export_intel_downloads_xlsx(client, monkeypatch):
    _patch_detectors(monkeypatch)
    r = client.get("/export/intel")
    assert r.status_code == 200, r.status_code
    assert r.headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    # xlsx is a zip — first bytes are the PK signature
    assert r.get_data()[:2] == b"PK"
