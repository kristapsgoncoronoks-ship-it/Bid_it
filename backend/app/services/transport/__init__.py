"""Transport vertical services (ADR-P3 / ADR-0023) — business logic for the
EU cross-border VAT refund claim engine. Reads the core (invoices, vendors,
documents, fx) through its services, never through a raw model join
(`tests/test_boundaries.py::test_transport_services_do_not_import_other_domain_models`).
"""

from __future__ import annotations
