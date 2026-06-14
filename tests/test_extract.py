"""The AI extraction backend must return invoice transactions, supplier data, and
the exchange rate when the invoice shows one. ZIP intake must reject zip-bombs."""
import io
import json
import zipfile

import pytest

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


# ---------------------------------------------------------------- _num amount parser
@pytest.mark.parametrize("raw,expected", [
    ("7 059,83", 7059.83),                  # ASCII thousands space
    ("7 059,83", 7059.83),             # non-breaking thousands space
    ("1.776,96", 1776.96),                  # dotted thousands (was silently 0.0 before)
    ("1 776,96", 1776.96),                  # plain thousands space
    ("1234.56", 1234.56),                   # plain dot-decimal e-invoice amount
    ("0", 0.0),
    ("", 0.0),
    (None, 0.0),                            # None must not raise (callers rely on numeric)
    ("abc", 0.0),
])
def test_num_parses_currency_amounts(raw, expected):
    out = EX._num(raw)
    assert out == expected
    assert isinstance(out, float)           # storage stays REAL; callers expect a float


def test_num_quantizes_half_up_not_bankers():
    # .xx5 residues must round HALF_UP (money.f2), not banker's HALF_EVEN.
    # banker's would give 2.66 / 0.12; HALF_UP gives 2.67 / 0.13. Comma decimals are
    # used here because in the European basis a DOT before exactly 3 digits is a
    # thousands separator (so "2.665" reads as 2665, not 2.665).
    assert EX._num("2,665") == 2.67
    assert EX._num("0,125") == 0.13


# ---------------------------------------------------------------- ZIP bomb caps
def _zip_of(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_normal_zip_still_unpacks():
    data = _zip_of([("a.pdf", b"%PDF-1.4 a"), ("sub/b.pdf", b"%PDF-1.4 b"),
                    ("__MACOSX/junk.pdf", b"x"), ("notes.txt", b"skip me")])
    files = EX.unpack(data, "batch.zip")
    assert [n for n, _ in files] == ["a.pdf", "b.pdf"]
    assert files[0][1] == b"%PDF-1.4 a"


def test_high_ratio_zip_bomb_rejected():
    # 60 MB of zeros compresses to a few KB but exceeds the 50 MB per-member cap.
    bomb = _zip_of([("huge.pdf", b"\0" * (EX.ZIP_MAX_MEMBER_BYTES + 1024))])
    assert len(bomb) < 1024 * 1024              # tiny on the wire, huge inflated
    with pytest.raises(EX.ZipLimitError, match="zip-bomb"):
        EX.unpack(bomb, "bomb.zip")


def test_member_count_cap(monkeypatch):
    monkeypatch.setattr(EX, "ZIP_MAX_MEMBERS", 5)
    data = _zip_of([(f"f{i}.pdf", b"%PDF") for i in range(6)])
    with pytest.raises(EX.ZipLimitError, match="cap"):
        EX.unpack(data, "many.zip")


def test_cumulative_total_cap(monkeypatch):
    # each member is under the per-member cap, but together they cross the total
    monkeypatch.setattr(EX, "ZIP_MAX_MEMBER_BYTES", 1024)
    monkeypatch.setattr(EX, "ZIP_MAX_TOTAL_BYTES", 2048)
    data = _zip_of([(f"f{i}.pdf", b"\0" * 1000) for i in range(3)])
    with pytest.raises(EX.ZipLimitError):
        EX.unpack(data, "total.zip")


def test_lying_header_caught_by_chunked_read():
    # headers can lie: even if the declared size passes, the actual decompressed
    # stream is metered and cut off at the budget.
    with pytest.raises(EX.ZipLimitError, match="zip-bomb"):
        EX._read_capped(io.BytesIO(b"\0" * 4096), "liar.pdf", budget=1024)
    assert EX._read_capped(io.BytesIO(b"\0" * 512), "ok.pdf", budget=1024) == b"\0" * 512


def test_collect_xml_has_same_caps(monkeypatch):
    monkeypatch.setattr(EX, "ZIP_MAX_MEMBER_BYTES", 1024)
    data = _zip_of([("inv.xml", b"<x>" + b"\0" * 4096 + b"</x>")])
    with pytest.raises(EX.ZipLimitError):
        EX._collect_xml(data, "batch.zip")
    # garbage that is not a ZIP still degrades to "no XML found"
    assert EX._collect_xml(b"not a zip at all", "garbage.zip") == []
