"""Transport vertical — plug-in bounded context (ADR-P3 / ADR-0023).

EU cross-border VAT refund claims (Dir. 2008/9/EC) for road-transport fleets,
harvested as *specification* from the retired Fleet Fuel system
(`docs/plan/shared/specs/BA_fleet_fuel.md`, R1-R76). Every model here owns a
transport-only table; none of them adds a column to `invoices`, `line_items`
or `vendors` (ADR-P3 rule 1). See `docs/transport/rules.md` for the R-number
-> module -> test -> legal-source ledger.
"""

from __future__ import annotations

from app.models.transport.checklist_rule import VatChecklistRule
from app.models.transport.contract_term import VatSupplierContractTerm
from app.models.transport.customer_lifecycle import VatCountryActivation, VatCustomerLifecycle
from app.models.transport.extraction_baseline import FuelExtractionBaseline
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.note_override import VatNoteInvoiceOverride
from app.models.transport.overcharge import VatOverchargeClaim
from app.models.transport.receipt_control import VatReceiptControl, VatSupplierCadence
from app.models.transport.receipt_waiver import VatReceiptWaiver
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.models.transport.tie_out import FuelTieOutExpectation
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine

__all__ = [
    "FuelExtractionBaseline",
    "FuelTieOutExpectation",
    "FuelTransaction",
    "SupplierVatRegistration",
    "VatChecklistRule",
    "VatClaimedInvoice",
    "VatCountryActivation",
    "VatCustomerLifecycle",
    "VatNoteInvoiceOverride",
    "VatOverchargeClaim",
    "VatReceiptControl",
    "VatReceiptWaiver",
    "VatRefundClaim",
    "VatRefundClaimLine",
    "VatSupplierCadence",
    "VatSupplierContractTerm",
]
