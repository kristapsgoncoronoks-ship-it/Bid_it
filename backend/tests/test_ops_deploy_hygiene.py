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


def test_sec_jwt_001_every_compose_file_forwards_the_signing_key_variables():
    """The compose files pass explicit `environment:` maps (no env_file), so a
    variable that is not forwarded is silently ignored. The R3 review found the
    documented JWT rotation would have been inert on the documented deploy."""
    for name in ("docker-compose.yml", "docker-compose.prod.yml", "docker-compose.hostinger.yml"):
        doc = _load(name)
        for svc_name, svc in doc["services"].items():
            env = svc.get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) for e in env)
            if "SECRET_KEY" not in env:
                continue  # not an app service
            for var in ("JWT_SIGNING_KEY", "JWT_SIGNING_KEY_FALLBACKS"):
                assert var in env, f"{name}: service {svc_name} does not forward {var}"
            assert "[]" in str(env["JWT_SIGNING_KEY_FALLBACKS"]), (
                f"{name}: {svc_name}: an unset fallback list must default to [] (empty string is a parse error)"
            )


# --- worker liveness probe (reference R2 review → ops group) ----------------

_PROBE = ["CMD", "python", "-m", "app.worker_probe"]


def test_every_compose_worker_probes_its_own_loop_instead_of_an_http_port():
    """The worker shares the backend image, whose HEALTHCHECK curls a port the
    worker never serves. Each stack replaces it with the loop heartbeat probe;
    the production overlay inherits the base file's (merge keeps it)."""
    base = _load("docker-compose.yml")["services"]["worker"]["healthcheck"]
    assert base["test"] == _PROBE, base
    assert "disable" not in base
    host = _load("docker-compose.hostinger.yml")["services"]["worker"]["healthcheck"]
    assert host["test"] == _PROBE, host
    assert "disable" not in host
    prod = _compose("docker-compose.yml", "docker-compose.prod.yml")["services"]["worker"]
    assert prod["healthcheck"]["test"] == _PROBE
    for hc in (base, host):
        # Slower than a tick, faster than the probe's own staleness limit (180 s)
        # times the retries — so `unhealthy` means the loop, not a slow disk.
        assert hc["interval"] == "60s" and hc["retries"] == 3


def test_every_k8s_worker_deployment_probes_its_loop_and_has_a_writable_tmp():
    """35-worker.yaml (the all-kinds pool) AND both lanes in 36-worker-lanes.yaml,
    which operators apply INSTEAD of it (R6 review D1: the OCR lane is the one
    most likely to wedge and had no probe)."""
    seen = []
    for path in sorted((REPO / "deploy" / "k8s").glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not doc or doc.get("kind") != "Deployment":
                continue
            if doc["metadata"].get("labels", {}).get("component") != "worker":
                continue
            seen.append(doc["metadata"]["name"])
            container = doc["spec"]["template"]["spec"]["containers"][0]
            probe = container.get("livenessProbe")
            assert probe, f"{path.name}: {doc['metadata']['name']} has no livenessProbe"
            assert probe["exec"]["command"] == ["python", "-m", "app.worker_probe"]
            assert probe["periodSeconds"] == 60 and probe["failureThreshold"] == 5
            # Once the file is older than 180 s the probe fails on every run, so
            # the product governs how long after a real hang the restart lands;
            # it must not be shorter than the limit or the two disagree on what
            # "wedged" means.
            assert probe["periodSeconds"] * probe["failureThreshold"] > 180
            # The root filesystem is read-only; the heartbeat lives on /tmp.
            assert container["securityContext"]["readOnlyRootFilesystem"] is True
            assert any(m["mountPath"] == "/tmp" for m in container["volumeMounts"])  # noqa: S108
            assert "readinessProbe" not in container  # nothing routes traffic to a worker
    assert sorted(seen) == ["worker", "worker-extract", "worker-general"], seen


# --- P2 batch 7: images from CI, one-shot migrations, the deploy environment --


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _extends_resolved(overlay: str) -> dict:
    """`extends` as Compose does it (verified with `docker compose config` on
    Compose 5.1.1): the referenced service's WHOLE definition is copied from the
    referenced FILE — depends_on and healthcheck included — and the extending
    service's own keys deep-merge on top (mappings merge, scalars and lists
    replace)."""
    doc = _load(overlay)
    for name, svc in (doc.get("services") or {}).items():
        ext = (svc or {}).get("extends")
        if ext:
            parent = _load(ext["file"])["services"][ext["service"]]
            own = {k: v for k, v in svc.items() if k != "extends"}
            doc["services"][name] = _deep_merge(parent, own)
    return doc


def _compose_stack(*rels: str) -> dict:
    """`docker compose -f a -f b -f c` for the overlays here: `extends` resolves
    per FILE first, then the files merge left to right — mappings deep-merge,
    `!reset` removes the key, everything else replaces."""
    merged: dict = {"services": {}}
    for rel in rels:
        for name, svc in (_extends_resolved(rel).get("services") or {}).items():
            target = merged["services"].setdefault(name, {})
            for key, value in (svc or {}).items():
                if isinstance(value, _Reset):
                    target[key] = None
                elif isinstance(value, dict) and isinstance(target.get(key), dict):
                    target[key] = _deep_merge(target[key], value)
                else:
                    target[key] = value
    return merged


def test_ops003_every_main_push_publishes_an_immutable_image_and_the_overlay_consumes_it():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
    on = wf[True] if True in wf else wf["on"]  # PyYAML reads the bare `on:` key as True
    assert "main" in on["push"]["branches"]
    assert on["push"]["tags"] == ["v*.*.*"]
    # Two pushes to main in quick succession must not race on the moving tags.
    assert wf["concurrency"] == {
        "group": "release-images-${{ github.ref }}",
        "cancel-in-progress": False,
    }
    (meta,) = [s for s in wf["jobs"]["images"]["steps"] if s.get("id") == "meta"]
    tags = meta["with"]["tags"]
    assert "type=sha" in tags and "type=ref,event=branch" in tags
    assert wf["permissions"] == {"contents": "read", "packages": "write"}

    stack = _compose_stack("docker-compose.hostinger.yml", "docker-compose.images.yml")
    for svc, image in (("backend", "backend"), ("worker", "backend"), ("frontend", "frontend")):
        s = stack["services"][svc]
        assert s.get("build") is None, f"{svc} still builds on the box"
        # IMAGE_TAG is REQUIRED (`:?`): a default of `main` would snap a rollback
        # back to the moving tag on the next plain invocation.
        assert s["image"].startswith("ghcr.io/${GHCR_REPOSITORY:?"), s["image"]
        assert f"/{image}:${{IMAGE_TAG:?" in s["image"], s["image"]
        # Everything else is inherited: volumes, healthcheck, command, env.
        assert s.get("healthcheck") and s.get("restart") == "unless-stopped"
    for svc in ("backend", "worker"):
        assert stack["services"][svc]["environment"]["STORAGE_BACKEND"] == "local"
        assert stack["services"][svc]["volumes"] == ["storagedata:/app/var/storage"]
    assert "alembic upgrade head" in " ".join(stack["services"]["backend"]["command"])

    # The documented three-file order: the one-shot migration must not compile
    # either — `extends` copied `build:` from the BASE FILE, so the images
    # overlay has to remove it explicitly.
    three = _compose_stack(
        "docker-compose.hostinger.yml",
        "docker-compose.hostinger.migrate.yml",
        "docker-compose.images.yml",
    )
    for svc in ("backend", "worker", "frontend", "migrate"):
        assert three["services"][svc].get("build") is None, f"{svc} still builds on the box"
    migrate = three["services"]["migrate"]
    assert "/backend:${IMAGE_TAG:?" in migrate["image"]
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert three["services"]["backend"]["depends_on"]["migrate"] == {
        "condition": "service_completed_successfully"
    }
    assert "alembic" not in " ".join(three["services"]["backend"]["command"])


def test_ops007_the_migrate_overlay_runs_migrations_once_and_gates_the_api_on_them():
    doc = _extends_resolved("docker-compose.hostinger.migrate.yml")
    migrate = doc["services"]["migrate"]
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert migrate["restart"] == "no"
    # The inherited healthcheck deep-merges with the overlay's; `disable` wins.
    assert migrate["healthcheck"]["disable"] is True
    # The production validator runs inside alembic's env.py too: the migrate
    # service must carry the backend's whole environment (extends gives it),
    # and the same volume (a migration may touch stored documents' metadata).
    base = _load("docker-compose.hostinger.yml")["services"]["backend"]
    assert migrate["environment"] == base["environment"]
    assert migrate["volumes"] == base["volumes"] == ["storagedata:/app/var/storage"]
    assert migrate["depends_on"] == {"db": {"condition": "service_healthy"}}
    backend = doc["services"]["backend"]
    assert backend["depends_on"]["migrate"] == {"condition": "service_completed_successfully"}
    assert "alembic" not in " ".join(backend["command"]), (
        "the API must not migrate on boot in this shape"
    )
    assert "exec uvicorn app.main:app" in " ".join(backend["command"])
    # Merged with the base file, the worker still waits for a HEALTHY backend,
    # which now implies the migration completed; the one-shot keeps its volume.
    stack = _compose_stack("docker-compose.hostinger.yml", "docker-compose.hostinger.migrate.yml")
    assert stack["services"]["worker"]["depends_on"] == {
        "backend": {"condition": "service_healthy"}
    }
    assert stack["services"]["migrate"]["volumes"] == ["storagedata:/app/var/storage"]
    assert stack["services"]["backend"]["depends_on"] == {
        "db": {"condition": "service_healthy"},
        "migrate": {"condition": "service_completed_successfully"},
    }


def test_ops013_the_deploy_job_runs_in_the_production_environment():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    deploy = wf["jobs"]["deploy"]
    assert deploy["environment"] == {"name": "production"}
    assert deploy["concurrency"]["group"] == "deploy-production"


def test_arch002_the_single_vps_stack_acknowledges_its_shared_local_volume():
    """The live stack runs `local` storage on purpose — one volume mounted by
    both processes. The acknowledgement is what keeps the ARCH-002 startup
    warning quiet there; a stack that dropped the shared mount would have to
    drop the acknowledgement too, and the warning would return."""
    doc = _load("docker-compose.hostinger.yml")
    for svc in ("backend", "worker"):
        env = doc["services"][svc]["environment"]
        assert env["STORAGE_BACKEND"] == "local"
        assert env["STORAGE_LOCAL_SHARED"] == "true"
        assert "storagedata:/app/var/storage" in doc["services"][svc]["volumes"]
    # The overlays inherit it (they never restate the environment).
    for overlay in ("docker-compose.images.yml", "docker-compose.hostinger.migrate.yml"):
        for svc in _load(overlay)["services"].values():
            assert "environment" not in svc
