"""Deployment hygiene gates (audit 2026-09-05: OPS-002/004/005/006/008, SEC-003/004).

Each of these was a real finding in the tree, and each is the kind of drift a
hand-maintained YAML or nginx file grows back silently. The tests read the
deployment files the way an operator would apply them.

- OPS-005: the production compose overlay publishes no state service to the
  host and requires every credential (no baked-in `invoiceiq` password).
- OPS-006: the DEV stack does not hard-code `ENVIRONMENT: production` (it
  crash-looped on the boot-time safety net); the production overlay configures
  the worker too, not only the backend (the 2026-08-23 incident).
- OPS-008: every service in the single-VPS stack rotates its logs, and the
  queue SLO probe is proxied so an external monitor can reach it.
- SEC-003/004: the SPA origin sends a CSP, and the `/assets/` location — which
  declares its own add_header and therefore drops every inherited one — repeats
  the full security-header block.
- OPS-002: the CI deploy job asserts the site is up after the SSH step.
- OPS-004: a scheduled-backup script exists, is executable, and verifies its
  dump the way vps-deploy.sh does.
"""

from __future__ import annotations

import os
import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


class _Reset:
    """Compose's `!reset` tag: the attribute goes back to empty, whatever is
    written after the tag."""


class _Override:
    """Compose's `!override` tag: the attribute is REPLACED by the tagged value."""

    def __init__(self, value):
        self.value = value


class _ComposeLoader(yaml.SafeLoader):
    pass


_ComposeLoader.add_constructor("!reset", lambda loader, node: _Reset())
_ComposeLoader.add_constructor(
    "!override",
    lambda loader, node: _Override(
        loader.construct_sequence(node)
        if isinstance(node, yaml.SequenceNode)
        else loader.construct_mapping(node)
        if isinstance(node, yaml.MappingNode)
        else loader.construct_scalar(node)
    ),
)


def _load(rel: str) -> dict:
    return yaml.load((REPO / rel).read_text(), Loader=_ComposeLoader)  # noqa: S506 - custom SafeLoader


def _compose(*rels: str) -> dict:
    """Merge compose files the way `docker compose -f a -f b` does for the keys
    these tests read: mappings merge, LISTS APPEND (so a bare `ports: []` in an
    overlay is a no-op — the finding that made this loader necessary), `!reset`
    empties, `!override` replaces. Cross-checked against a real
    `docker compose config` render on 2026-09-05."""
    merged: dict = {"services": {}}
    for rel in rels:
        doc = _load(rel)
        for name, svc in (doc.get("services") or {}).items():
            target = merged["services"].setdefault(name, {})
            for key, value in (svc or {}).items():
                if isinstance(value, _Reset):
                    target[key] = [] if key in ("ports", "volumes", "depends_on") else None
                elif isinstance(value, _Override):
                    target[key] = value.value
                elif key == "environment" and isinstance(value, dict):
                    target.setdefault("environment", {}).update(value)
                elif isinstance(value, list) and isinstance(target.get(key), list):
                    target[key] = target[key] + value
                else:
                    target[key] = value
    return merged


def test_ops005_the_production_overlay_publishes_no_state_service_and_bakes_no_credential():
    stack = _compose("docker-compose.yml", "docker-compose.prod.yml")
    for svc in ("db", "minio", "backend"):
        assert stack["services"][svc].get("ports", []) == [], (
            f"{svc} is published to the host in the production overlay"
        )
    # The TLS origin publishes exactly 80 and 443 — not the base file's 8080 too.
    assert stack["services"]["frontend"]["ports"] == ["80:80", "443:443"]
    env = stack["services"]["db"]["environment"]
    assert "${POSTGRES_PASSWORD" in str(env["POSTGRES_PASSWORD"]), env["POSTGRES_PASSWORD"]
    minio = stack["services"]["minio"]["environment"]
    assert "${MINIO_ROOT_PASSWORD" in str(minio["MINIO_ROOT_PASSWORD"])
    for svc in ("backend", "worker"):
        url = stack["services"][svc]["environment"]["DATABASE_URL"]
        assert "invoiceiq:invoiceiq@" not in url, f"{svc} still carries the dev DB password"


def test_ops006_the_dev_stack_does_not_hardcode_production_and_the_overlay_configures_the_worker():
    base = _load("docker-compose.yml")
    for svc in ("backend", "worker"):
        env = base["services"][svc]["environment"]["ENVIRONMENT"]
        assert env != "production", f"the dev stack hard-codes production for {svc}"
    prod = _load("docker-compose.prod.yml")
    worker = prod["services"]["worker"]["environment"]
    assert worker["ENVIRONMENT"] == "production"
    assert "INBOUND_EMAIL_SECRET" in worker and "SECRET_KEY" in worker


def test_ops008_every_single_vps_service_rotates_its_logs():
    doc = _load("docker-compose.hostinger.yml")
    missing = [
        name
        for name, svc in doc["services"].items()
        if not (svc.get("logging") or {}).get("options", {}).get("max-size")
    ]
    assert missing == [], f"services without log rotation: {missing}"


def _nginx() -> str:
    return (REPO / "frontend" / "nginx.prod.conf").read_text()


def test_ops008_the_queue_slo_probe_is_proxied():
    assert re.search(
        r"location = /health/queue \{\s*proxy_pass http://backend:8000/health/queue;", _nginx()
    )


def test_sec003_the_spa_origin_sends_a_csp_without_inline_scripts():
    conf = _nginx()
    csp_lines = [
        ln
        for ln in conf.splitlines()
        if "Content-Security-Policy" in ln and not ln.lstrip().startswith("#")
    ]
    assert csp_lines, "no Content-Security-Policy header in nginx.prod.conf"
    for ln in csp_lines:
        assert (
            "script-src 'self'" in ln
            and "'unsafe-inline'" not in ln.split("script-src")[1].split(";")[0]
        )
        assert "frame-ancestors 'none'" in ln
        assert "object-src 'none'" in ln


def test_sec004_the_assets_location_repeats_every_security_header():
    conf = _nginx()
    server_headers = set()
    assets_headers = set()
    depth = 0
    in_assets = False
    for ln in conf.splitlines():
        s = ln.strip()
        if s.startswith("location /assets/"):
            in_assets = True
        if s.startswith("add_header ") and not s.startswith("#"):
            name = s.split()[1]
            if name == "Cache-Control":
                continue
            (assets_headers if in_assets else server_headers).add(name)
        depth += s.count("{") - s.count("}")
        if in_assets and s.endswith("}") and "{" not in s:
            in_assets = False
    assert server_headers >= {
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Strict-Transport-Security",
        "Content-Security-Policy",
    }, server_headers
    assert assets_headers == server_headers, (
        f"/assets/ drops inherited headers; missing: {sorted(server_headers - assets_headers)}"
    )


def test_ops002_the_deploy_job_asserts_the_site_is_up_after_the_ssh_step():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    deploy = ci.split("\n  deploy:\n", 1)[1]
    assert "Assert the deployed site is up" in deploy
    assert "DEPLOY_HEALTH_URL" in deploy
    assert deploy.index("Deploy over SSH") < deploy.index("Assert the deployed site is up")
    assert "ServerAliveInterval" in deploy


def test_ops004_a_scheduled_backup_script_exists_and_verifies_its_dump():
    script = REPO / "scripts" / "backup.sh"
    assert script.exists()
    assert os.access(script, os.X_OK), "scripts/backup.sh is not executable"
    text = script.read_text()
    assert "PostgreSQL database dump complete" in text  # the verified-dump check
    assert "invoiceiq_storagedata" in text  # the document-bytes volume
    assert "crontab" in text  # the install line is documented in the script itself
    docs = (REPO / "docs" / "DEPLOY-HOSTINGER.md").read_text()
    assert "scripts/backup.sh" in docs
