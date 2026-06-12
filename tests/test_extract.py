"""The AI extraction backend must return invoice transactions, supplier data, and
the exchange rate when the invoice shows one."""
import json

import extract as EX


def _draft(monkeypatch, payload):
    monkeypatch.setattr(EX, "_AI", {"claude": lambda texts: json.dumps(payload)})
    return EX._ai_extract("claude", [("doc.pdf", "text")])


def test_extracts_transactions_supplier_and_fx(monkeypatch):
    d = _draft(monkeypatch, {
        "supplier": "BP", "supplier_vat": "PL527-020-0000",
        "statement_ref": "0261167596", "currency": "PLN", "customer": "SIA Test",
        "lines": [
            {"invoice_no": "INV1", "date": "2026-05-15", "country": "Poland",
             "currency": "PLN", "net": "1000.0", "vat": "230.0", "fx_rate": "4.27"},
            {"invoice_no": "INV2", "date": "2026-05-16", "country": "Belgium",
             "currency": "EUR", "net": 500, "vat": 105, "fx_rate": None},
            {"invoice_no": "INV3", "country": "Sweden", "currency": "SEK",
             "net": 2000, "vat": 500},  # no fx_rate key at all
        ],
    })
    assert d["supplier"] == "BP" and d["supplier_vat"] == "PL527-020-0000"
    lines = d["lines"]
    assert lines[0]["fx_rate"] == 4.27                 # rate captured
    assert lines[1]["fx_rate"] is None                 # explicit null (EUR line)
    assert lines[2]["fx_rate"] is None                 # missing key -> None
    # money normalized to floats
    assert lines[0]["net"] == 1000.0 and lines[0]["vat"] == 230.0
    assert d["backend"] == "claude"


def test_bad_fx_rate_is_null_not_crash(monkeypatch):
    d = _draft(monkeypatch, {"supplier": "X", "currency": "EUR",
                             "lines": [{"invoice_no": "A", "net": "n/a", "vat": None,
                                        "fx_rate": "garbage"}]})
    ln = d["lines"][0]
    assert ln["fx_rate"] is None and ln["net"] == 0.0 and ln["vat"] == 0.0


def test_amounts_round_half_up_into_draft(monkeypatch):
    # .005 residues must store the accounting (HALF_UP) result; bare round()
    # (banker's) would persist 100.00 / 2.67 in the draft.
    d = _draft(monkeypatch, {"supplier": "X", "currency": "EUR",
                             "lines": [{"invoice_no": "A", "net": 100.005,
                                        "vat": 2.675}]})
    ln = d["lines"][0]
    assert ln["net"] == 100.01 and ln["vat"] == 2.68


def test_backends_are_provider_neutral():
    # the pipeline supports Claude, ChatGPT (OpenAI) and Azure, selected by env
    assert set(EX._AI) >= {"claude", "openai", "azure"}
