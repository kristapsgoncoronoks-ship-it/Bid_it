"""Data-residency enforcement (ADR-0022).

A regional deployment (`service_region`) should only serve tenants pinned to that
region. The load balancer does the real routing (by host / tenant → region); this
is the **belt-and-braces backstop** that refuses a request which somehow reached
the wrong region's data plane, so a misroute fails closed instead of reading or
writing a tenant's data in the wrong jurisdiction.

Inert unless `enforce_region_pinning` is on (single-region deployments pay
nothing — the check short-circuits before any work).
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.core.config import settings


def region_ok(region: str | None) -> bool:
    if not settings.enforce_region_pinning:
        return True
    return region == settings.service_region


def assert_region(org) -> None:
    """Raise 421 (Misdirected Request) when the tenant is pinned to another region."""
    if not region_ok(getattr(org, "region", None)):
        raise HTTPException(
            status_code=status.HTTP_421_MISDIRECTED_REQUEST,
            detail=f"This workspace's data region is '{org.region}', not '{settings.service_region}'.",
            headers={"X-Tenant-Region": org.region},
        )
