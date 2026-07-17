from fastapi import APIRouter

from app.api.routes import analytics, auth, invoices, vendors

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(vendors.router)
api_router.include_router(invoices.router)
api_router.include_router(analytics.router)
