"""Shared CSV/Excel formula-injection neutralisation (CWE-1236).

Excel, Google Sheets and LibreOffice treat a CSV/XLSX cell that *starts* with
`=`, `+`, `-`, `@`, a tab, or a carriage return as a live formula/arithmetic
trigger, not a literal string. Any free-text field a user (or a document a
user transcribes — a vendor's invoice number, an employee's expense-report
title) can influence is therefore a CSV/Excel-injection vector the moment it
reaches an exported spreadsheet (the classic `=HYPERLINK(...)`/`=cmd|...`
payload a finance/HR user opens straight in Excel).

The mitigation is the OWASP-recommended one: prefix the cell with a leading
single quote so the spreadsheet renders it as inert text instead of
evaluating it. Apply this ONLY to genuinely free-text fields — never to a
formatted numeric/money/date cell, where a leading `-` is a legitimate
negative amount, not an injection attempt; sanitizing a numeric column would
silently corrupt it (a spreadsheet reads a quote-prefixed cell as text, not
a number).

One shared implementation so every CSV/Excel writer in the codebase applies
the same rule. Previously duplicated three times with matching docstrings —
`erp_export._safe`, `audit_export._safe`, `report_writers._safe_cell` — which
is exactly how the three sibling exports (`payment_run.export_csv`,
`reimbursement.export_csv`, `explore.to_csv`) ended up unprotected: a helper
copied per-file is a helper a fourth writer can forget to copy. See
`docs/audit/remediation-roadmap.md` R1/R9.
"""

from __future__ import annotations

FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: object) -> str:
    """Neutralise CSV/Excel formula injection: prefix a leading formula
    trigger with a single quote so a cell is never evaluated as a formula.

    Apply this to free-text fields only (names, references, descriptions,
    titles) — never to a formatted numeric/money/date cell, where a leading
    `-` is a legitimate negative number, not an injection attempt.
    """
    s = "" if value is None else str(value)
    if s and s[0] in FORMULA_TRIGGERS:
        return "'" + s
    return s
