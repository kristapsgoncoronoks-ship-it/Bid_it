from fastapi import APIRouter

from app.api.routes import (
    access, analytics, audit, auth, billing, budget, email, expenses, fx,
    invoices, issued, issuer, jobs, modules, partners, platform, recurring,
    settings, team, vendors, webhooks,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(vendors.router)
api_router.include_router(invoices.router)
api_router.include_router(analytics.router)
api_router.include_router(fx.router)
api_router.include_router(settings.router)
api_router.include_router(modules.router)
api_router.include_router(issuer.router)
# Recurring BEFORE issued: `/issued/recurring*` must not be shadowed by the
# `/issued/{invoice_id}` catch-all in the issued router.
api_router.include_router(recurring.router)
api_router.include_router(issued.router)
api_router.include_router(partners.router)
api_router.include_router(team.router)
api_router.include_router(billing.router)
api_router.include_router(platform.router)
api_router.include_router(expenses.router)
api_router.include_router(email.router)
api_router.include_router(budget.router)
api_router.include_router(access.router)
api_router.include_router(audit.router)
api_router.include_router(jobs.router)
api_router.include_router(webhooks.router)
