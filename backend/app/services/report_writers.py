"""General report-to-Excel and report-to-PDF writers (board I1.5).

The charter asks for Excel/CSV/PDF reports. CSV exists everywhere
(`explore.to_csv`, `erp_export`, `audit_export`) and the invoice PDF writer
(`invoice_pdf.py`) is production-grade, but there was no *general* report
writer. This module fills that gap for the one report surface I1.5 scopes:
any cut produced by the self-service Explore pivot engine
(`app.services.explore.run`).

Both writers consume the **same** ``result`` dict `explore.run()`/`to_csv()`
already produce — ``{"measure": {...}, "dimensions": [...], "rows": [...]}``
— so the three export formats (JSON/CSV/XLSX/PDF) can never disagree: none of
them re-derives or re-aggregates a figure, they only re-serialise the one
canonical result.

Formula-injection safety (the same convention already used independently by
`erp_export._safe` and `audit_export._safe` for CSV) is extended here to
Excel: a leading ``=``, ``+``, ``-``, ``@``, tab or CR turns a cell into a
live formula/arithmetic trigger when a spreadsheet app opens the file, so
every string cell gets the same neutralising quote prefix. PDF text is never
executed as a formula, so the PDF writer does not need it.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from app.core.csv_safety import sanitize_cell as _safe_cell

# `_safe_cell` is `app.core.csv_safety.sanitize_cell` imported under its
# original local name (board R1/R9 — one shared implementation, no per-file
# copy; see `test_safe_cell_neutralises_every_trigger`).


def _header_and_rows(result: dict) -> tuple[list[str], list[list[str]]]:
    dims = result["dimensions"]
    measure_label = result["measure"]["label"]
    header = [d["label"] for d in dims] + [measure_label]
    rows = [[row.get(d["key"], "") for d in dims] + [row["value"]] for row in result["rows"]]
    return header, rows


def to_xlsx(result: dict) -> bytes:
    """Render an Explore cut as an .xlsx workbook. One sheet, a bold header
    row, formula-injection-safe cells throughout (dimension AND measure
    columns — a negative money value renders with a leading '-', which is as
    live a formula trigger in Excel as a leading '=')."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    header, rows = _header_and_rows(result)

    wb = Workbook()
    ws = wb.active
    ws.title = "Explore"

    ws.append([_safe_cell(h) for h in header])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([_safe_cell(v) for v in row])

    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_INK = "#1e293b"
_MUTED = "#64748b"
_BRAND = "#2f57d4"
_LINE = "#e2e8f0"


def to_pdf(result: dict) -> bytes:
    """Render an Explore cut as a simple tabular PDF report: title, a
    generated-at stamp, and one table. Deliberately minimal — no logo, no
    embedded XML, no PDF/A-3 conformance (that hardening step belongs to
    F1.4, over the invoice writer, not here)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    header, rows = _header_and_rows(result)
    dims = result["dimensions"]
    measure_label = result["measure"]["label"]
    dim_label = " x ".join(d["label"] for d in dims) if dims else None
    title_text = f"{measure_label} by {dim_label}" if dim_label else measure_label

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Title"], fontSize=18, textColor=colors.HexColor(_BRAND)
    )
    small = ParagraphStyle(
        "small", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor(_MUTED)
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=title_text,
    )
    story: list = [
        Paragraph(title_text, title_style),
        Paragraph(f"Generated at {datetime.now(UTC).isoformat()}", small),
        Spacer(1, 10),
    ]

    table_data = [header] + rows
    tbl = Table(table_data, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_BRAND)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor(_LINE)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor(_INK)),
            ]
        )
    )
    story.append(tbl)

    doc.build(story)
    return buf.getvalue()
