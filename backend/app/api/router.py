from fastapi import APIRouter

from app.api.routes import (
    access,
    analytics,
    archive,
    audit,
    auth,
    billing,
    budget,
    costing,
    currencies,
    customers,
    dashboard,
    documents,
    dunning,
    email,
    expenses,
    export,
    fx,
    integrity,
    invoice_review,
    invoices,
    issued,
    issuer,
    jobs,
    modules,
    partners,
    payment_runs,
    platform,
    privacy,
    receipts,
    reconciliation,
    recurring,
    reimbursements,
    retention,
    scim,
    settings,
    sso,
    tax_codes,
    team,
    transport,
    vendors,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(vendors.router)
api_router.include_router(invoices.router)
api_router.include_router(invoice_review.router)
api_router.include_router(analytics.router)
api_router.include_router(dunning.router)
api_router.include_router(fx.router)
api_router.include_router(settings.router)
api_router.include_router(modules.router)
api_router.include_router(tax_codes.router)
api_router.include_router(currencies.router)
api_router.include_router(costing.router)
api_router.include_router(issuer.router)
api_router.include_router(customers.router)
# Recurring BEFORE issued: `/issued/recurring*` must not be shadowed by the
# `/issued/{invoice_id}` catch-all in the issued router.
api_router.include_router(recurring.router)
api_router.include_router(issued.router)
api_router.include_router(receipts.router)
api_router.include_router(reconciliation.router)
api_router.include_router(payment_runs.router)
api_router.include_router(partners.router)
api_router.include_router(team.router)
api_router.include_router(billing.router)
api_router.include_router(platform.router)
api_router.include_router(expenses.router)
api_router.include_router(reimbursements.router)
api_router.include_router(email.router)
api_router.include_router(budget.router)
api_router.include_router(access.router)
api_router.include_router(audit.router)
api_router.include_router(jobs.router)
api_router.include_router(webhooks.router)
api_router.include_router(integrity.router)
api_router.include_router(documents.router)
api_router.include_router(export.router)
api_router.include_router(archive.router)
api_router.include_router(retention.router)
api_router.include_router(privacy.router)
api_router.include_router(sso.router)
api_router.include_router(scim.router)
# Transport vertical (ADR-P3, WO-76): the package router aggregates every
# transport route slice (claims today; fuel/recovery/excise later).
api_router.include_router(transport.router)
