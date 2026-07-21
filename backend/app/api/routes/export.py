from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_admin_or_above
from app.services import erp_export

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/formats")
async def list_formats(current: CurrentUser):
    """Accounting/ERP export formats available from the ledger."""
    return [
        {
            "key": f,
            "label": {
                "generic": "Generic accounting CSV",
                "xero": "Xero (Bills)",
                "quickbooks": "QuickBooks (Bills)",
            }[f],
        }
        for f in erp_export.FORMATS
    ]


@router.get("/accounting")
async def export_accounting(
    current: CurrentUser,
    db: DbSession,
    fmt: str = Query("generic"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
):
    """Export the received-invoice ledger for a period into an accounting-package
    import file (CSV). NET EUR basis; read-only; formula-injection-safe."""
    if not is_admin_or_above(current):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only an admin can export the accounting ledger"
        )
    if fmt not in erp_export.FORMATS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown format '{fmt}'. Available: {', '.join(erp_export.FORMATS)}.",
        )
    rows = await erp_export.ledger(db, current.org_id, date_from, date_to)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No invoices in this period")
    filename, text = erp_export.render(fmt, rows)
    return Response(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
