from fastapi import APIRouter

from app.api.routes import (
    access, analytics, audit, auth, billing, budget, email, expenses, fx,
    invoices, issued, issuer, modules, partners, platform, settings, team, vendors,
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
