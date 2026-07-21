"""Acceptance evidence for the platform-foundation pieces added in this slice:
the structured error model, its API mapping, production config validation, and
the OpenAPI export.
"""

from __future__ import annotations

import json

import pytest

from app.core import errors
from app.core.config import INSECURE_SECRET_KEY, Settings

# --- structured error model ------------------------------------------------


def test_apperror_defaults_and_overrides():
    assert errors.NotFoundError("x").status == 404
    assert errors.NotFoundError("x").code == "not_found"
    assert errors.ConflictError("x").status == 409
    assert errors.ValidationError("x").status == 422
    assert errors.PermissionError("x").status == 403
    # Per-raise override wins.
    e = errors.AppError("boom", code="teapot", status=418)
    assert (e.status, e.code, e.message) == (418, "teapot", "boom")


@pytest.mark.asyncio
async def test_app_error_handler_maps_to_detail_code_shape():
    """A service AppError is rendered in the canonical wire shape {detail, code}."""
    from app.main import _app_error_handler

    resp = await _app_error_handler(None, errors.ConflictError("already exists", code="dup_code"))
    assert resp.status_code == 409
    body = json.loads(bytes(resp.body))
    assert body == {"detail": "already exists", "code": "dup_code"}


@pytest.mark.asyncio
async def test_unhandled_error_handler_never_leaks():
    from app.main import _unhandled_error_handler

    resp = await _unhandled_error_handler(None, RuntimeError("secret internal detail"))
    assert resp.status_code == 500
    body = json.loads(bytes(resp.body))
    assert body == {"detail": "Internal server error", "code": "internal_error"}
    assert "secret internal detail" not in json.dumps(body)


# --- production config validation ------------------------------------------


def test_dev_config_allows_insecure_defaults():
    # The whole test suite + local dev rely on zero-config defaults working.
    s = Settings(environment="development")
    assert s.secret_key == INSECURE_SECRET_KEY  # tolerated outside production


def test_production_rejects_insecure_secret_key():
    with pytest.raises(ValueError, match="secret_key"):
        Settings(
            environment="production",
            secret_key=INSECURE_SECRET_KEY,
            database_url="postgresql+asyncpg://u:p@db/invoiceiq",
        )


def test_production_rejects_sqlite():
    with pytest.raises(ValueError, match="SQLite"):
        Settings(
            environment="production",
            secret_key="a-real-32-byte-secret-value-here!!",
            database_url="sqlite+aiosqlite:///./invoiceiq.db",
        )


def test_valid_production_config_passes():
    s = Settings(
        environment="production",
        secret_key="a-real-32-byte-secret-value-here!!",
        database_url="postgresql+asyncpg://u:p@db/invoiceiq",
        cors_origins="https://app.invoiceiq.example",
    )
    assert s.is_production and not s.is_sqlite


# --- OpenAPI export --------------------------------------------------------


def test_openapi_export_has_paths_and_health():
    from app.main import app

    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    assert "/health" in schema["paths"]
    # Serialises cleanly (what `python -m app.openapi` writes).
    assert json.loads(json.dumps(schema))["info"]["title"]
