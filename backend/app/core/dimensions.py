"""Cost-allocation dimensions shared by invoices and expenses.

Free-text tags (no master table yet — simple to start, normalise later) that let
a company slice spend by the cost object it belongs to: a cost center, a
department, a project, a vehicle (transport fleets), or a property (property
managers). Employee is already modelled on expense reports.

One definition, reused by the models, schemas, and the by-dimension analytics so
a new dimension is added in exactly one place.
"""
from __future__ import annotations

# attribute/column name → human label. `property_ref` avoids shadowing the
# Python `property` builtin while reading as "Property" in the UI.
DIMENSIONS: dict[str, str] = {
    "cost_center": "Cost center",
    "department": "Department",
    "project": "Project",
    "vehicle": "Vehicle",
    "property_ref": "Property",
}

DIMENSION_KEYS: tuple[str, ...] = tuple(DIMENSIONS)

# Column width for every dimension tag.
MAX_LEN = 80


def is_dimension(key: str) -> bool:
    return key in DIMENSIONS
