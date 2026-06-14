"""BP PLN->EUR FX convergence (§B Part B): _bp prefers the dated ECB rate
(ecb_rates.rate_for, PLN per 1 EUR => divide) and falls back to the month_config
config rate (EUR per 1 PLN => multiply) when no ECB PLN coverage exists.

The fallback path must stay byte-identical to the pre-change behaviour, which is what
the demo/consolidate exercises (this env ships no ecb_rates.db PLN coverage)."""
import importlib

import pytest


CONFIG_RATE = 1 / 4.27        # EUR per 1 PLN (month_config.FX convention)
CTX = {"fx": {"EUR_PER_PLN": CONFIG_RATE}}

# A synthetic BP workbook row: (lp, card, date, reg, loc, prod, cat, qty, unit, gross,
# vatp, vat, net) — the 13-col unpack in _bp. PLN figures, DD/MM/YY date string.
def _row(date="27/05/26", net=100.0, vat=8.0, gross=108.0):
    return ("1", "CARD1", date, "REG1", "Station X", "DIESEL", "fuel",
            50.0, 2.0, gross, 8.0, vat, net)


@pytest.fixture()
def specs(tmp_path, monkeypatch):
    """Reload supplier_specs/ecb_rates with the ECB cache pointed at a tmp DB and the
    per-date memo cleared, so each test is isolated."""
    import ecb_rates
    importlib.reload(ecb_rates)
    monkeypatch.setattr(ecb_rates, "DB", str(tmp_path / "ecb_test.db"))
    import supplier_specs
    importlib.reload(supplier_specs)
    # the reloaded supplier_specs holds the freshly-reloaded ecb_rates module
    supplier_specs.ecb_rates = ecb_rates
    supplier_specs._PLN_RATE_CACHE.clear()
    return supplier_specs, ecb_rates


def _seed(ecb_rates, date, rate, ccy="PLN"):
    con = ecb_rates.connect()
    con.execute("INSERT OR REPLACE INTO ecb_fx (date,currency,rate) VALUES (?,?,?)",
                (date, ccy, rate))
    con.commit(); con.close()


def test_ecb_path_divides_by_dated_rate(specs):
    supplier_specs, ecb_rates = specs
    # seed the dated ECB PLN rate (PLN per 1 EUR) for the line's ISO date
    _seed(ecb_rates, "2026-05-27", 4.31)
    m = supplier_specs._bp(_row(date="27/05/26", net=100.0, vat=8.0), CTX)
    # ECB path: divide by 4.31 (NOT multiply by the config 1/4.27)
    assert m["net_eur"] == pytest.approx(100.0 / 4.31)
    assert m["vat_eur"] == pytest.approx(8.0 / 4.31)
    assert m["net_eur"] != pytest.approx(100.0 * CONFIG_RATE)


def test_config_fallback_multiplies_when_no_ecb(specs):
    supplier_specs, _ = specs
    # no ECB PLN row seeded -> config fallback, byte-identical to today
    m = supplier_specs._bp(_row(net=100.0, vat=8.0), CTX)
    assert m["net_eur"] == 100.0 * CONFIG_RATE
    assert m["vat_eur"] == 8.0 * CONFIG_RATE


def test_pln_rate_memoized_one_lookup_per_date(specs, monkeypatch):
    supplier_specs, ecb_rates = specs
    _seed(ecb_rates, "2026-05-27", 4.31)
    calls = {"n": 0}
    orig = ecb_rates.rate_for

    def counting(ccy, on_date=None):
        calls["n"] += 1
        return orig(ccy, on_date)

    monkeypatch.setattr(supplier_specs.ecb_rates, "rate_for", counting)
    for _ in range(5):
        supplier_specs._bp(_row(date="27/05/26"), CTX)
    assert calls["n"] == 1  # 5 lines, same date -> a single DB lookup


def test_pln_rate_never_raises_on_broken_ecb(specs, monkeypatch):
    supplier_specs, _ = specs

    def boom(*a, **k):
        raise RuntimeError("ecb exploded")

    monkeypatch.setattr(supplier_specs.ecb_rates, "rate_for", boom)
    supplier_specs._PLN_RATE_CACHE.clear()
    # must swallow the error, return None, and use the config fallback
    m = supplier_specs._bp(_row(net=100.0, vat=8.0), CTX)
    assert m["net_eur"] == 100.0 * CONFIG_RATE


def test_dkv_q8_untouched_invoice_eur(specs):
    """Sanity: DKV/Q8 still emit invoice-stated EUR, not a config/ECB conversion."""
    supplier_specs, _ = specs
    # Q8: net is already EUR (net_eur == net)
    q8_row = ("CARD", "27/05/26", "10:00", "DIESEL", "STN", "PL", 23, "EUR",
              1.5, 4.27, 100.0, 50.0, 100.0, 0, 0, 0, 0, 0, 95.0, 0)
    q = supplier_specs._q8(q8_row, CTX)
    assert q["net_eur"] == 100.0
    # DKV: net_eur prorated from the invoice's own EUR figure (col 17)
    dkv_row = ("INV", "VEH", "27/05/26", "10:00", "BR", "City", "DIESEL", 50.0,
               2.0, 1.8, 100.0, 5.0, 0.0, 95.0, 23.75, 118.75, 11.0)
    d = supplier_specs._dkv(dkv_row, CTX)
    assert d["net_eur"] == pytest.approx(11.0 * 95.0 / 118.75)
