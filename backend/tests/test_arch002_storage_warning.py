"""ARCH-002 (audit 2026-09-05): `local` storage in production is a warning,
not a refusal — the live single-VPS stack runs it on purpose on one shared
volume, and a validator that refused it would take production down on the
next deploy (the debate's MODIFY). The warning names the condition and the
acknowledgement; the acknowledgement silences it; an object store never
warns; outside production nothing is said."""

from __future__ import annotations

import logging
import pathlib

from app.core.config import Settings, log_production_warnings

_PROD = {
    "environment": "production",
    "secret_key": "x" * 48,
    "database_url": "postgresql+asyncpg://u:p@db:5432/invoiceiq",
    "cors_origins": "https://app.example.com",
    "inbound_email_secret": "s" * 32,
}


def _settings(**over) -> Settings:
    return Settings(_env_file=None, **{**_PROD, **over})  # type: ignore[call-arg]


def test_local_storage_in_production_warns_and_names_the_way_out():
    (line,) = _settings(storage_backend="local").production_warnings()
    assert "STORAGE_BACKEND=local in production" in line
    assert "STORAGE_LOCAL_SHARED=true" in line and "STORAGE_BACKEND=s3" in line
    assert "ARCH-002" in line


def test_the_acknowledgement_silences_it_and_an_object_store_never_warns():
    assert _settings(storage_backend="local", storage_local_shared=True).production_warnings() == []
    assert _settings(storage_backend="s3").production_warnings() == []
    assert _settings(storage_backend="S3").production_warnings() == []


def test_nothing_is_said_outside_production():
    s = Settings(_env_file=None, environment="development", storage_backend="local")  # type: ignore[call-arg]
    assert s.production_warnings() == []


def test_the_warning_is_not_a_refusal():
    """The validator still boots the configuration — that is the whole point."""
    s = _settings(storage_backend="local")
    assert s.storage_backend == "local" and s.production_warnings()


def test_the_warning_reaches_the_log(caplog):
    """Where the API lifespan and the worker's main send it (R7 review A1: a
    method that returns a list proves nothing about the startup log)."""
    log = logging.getLogger("test.arch002")
    with caplog.at_level(logging.WARNING, logger="test.arch002"):
        n = log_production_warnings(log, _settings(storage_backend="local"))
    assert n == 1
    assert any("STORAGE_BACKEND=local in production" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="test.arch002"):
        assert log_production_warnings(log, _settings(storage_backend="s3")) == 0
    assert not caplog.records


def test_both_process_entrypoints_call_it():
    """The API and the worker are separate processes with separate startups; a
    warning emitted by one only is a warning half the fleet never shows."""
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    for entrypoint in ("main.py", "worker.py"):
        assert "log_production_warnings(log)" in (app_dir / entrypoint).read_text(), entrypoint
