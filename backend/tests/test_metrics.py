"""Deterministic-capture-rate metric (Phase 1.6): every parse is counted by
method so the dashboard can compute the share read without AI/manual entry."""
import pytest

_CSV = (
    b"vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    b"Acme,INV-M-1,2026-01-01,Widget,1,10.00,10.00,0\n"
)


def _sample(method: str) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value("invoiceiq_documents_parsed_total", {"method": method}) or 0.0


def test_deterministic_method_set():
    from app.core import metrics

    # The set that counts as "captured without AI/manual".
    assert {"csv", "json", "e-invoice-xml", "text-layer"} <= metrics.DETERMINISTIC_METHODS
    assert "ai" not in metrics.DETERMINISTIC_METHODS
    assert "failed" not in metrics.DETERMINISTIC_METHODS


def test_parse_records_method_counter():
    pytest.importorskip("prometheus_client")
    from app.services.parser import parse_invoice_file

    before = _sample("csv")
    draft = parse_invoice_file("invoice.csv", _CSV)
    assert draft.method == "csv"
    assert _sample("csv") == before + 1  # deterministic capture counted


def test_failed_parse_is_counted():
    pytest.importorskip("prometheus_client")
    from app.services.parser import parse_invoice_file

    before = _sample("failed")
    with pytest.raises(ValueError):
        parse_invoice_file("mystery.txt", b"not an invoice")
    assert _sample("failed") == before + 1


def test_record_parse_is_safe_without_a_method():
    from app.core import metrics

    before = _sample("unknown")
    metrics.record_parse(None)  # must not raise; buckets under "unknown"
    pytest.importorskip("prometheus_client")
    assert _sample("unknown") == before + 1
