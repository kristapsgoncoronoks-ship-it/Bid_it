"""Source controls complement real NGINX saturation/streaming qualification."""

import re
from pathlib import Path

import pytest

from tests.test_upload_ingress import SINGLE

ROOT = Path(__file__).resolve().parents[2]
ORIGINS = [ROOT / "frontend" / name for name in ("nginx.conf", "nginx.prod.conf")]


@pytest.mark.parametrize("origin", ORIGINS, ids=lambda p: p.name)
def test_origin_admission_covers_upload_routes_without_blocking_other_actions(origin):
    text = origin.read_text()
    block = re.search(r'map "\$request_method:\$uri" \$upload_admission_key \{(.*?)\n}', text, re.S)
    assert block, "origin has no shared upload admission map"
    patterns = re.findall(r"^\s+~(\S+)\s+uploads;", block[1], re.M)

    def admitted(method, path):
        return any(re.fullmatch(pattern, method + ":" + path) for pattern in patterns)

    paths = [p for p, _ in SINGLE] + [
        "/invoices/upload/batch",
        "/email/inbound",
        "/email/inbound/mailgun",
    ]
    for version in ("v1", "v2"):
        for path in paths:
            for suffix in ("", "/"):
                assert admitted("POST", "/api/" + version + path + suffix), path
                assert not admitted("GET", "/api/" + version + path + suffix), path
    for path in (
        "/health",
        "/health/ready",
        "/api/v1/issued",
        "/api/v1/auth/login",
        "/api/v1/invoices/upload-commands/synthetic",
        "/api/v1/issued/commands",
    ):
        assert not admitted("GET", path)
        assert not admitted("POST", path)
    assert "limit_conn_zone $upload_admission_key zone=upload_admission:64k;" in text
    api = text.split("location /api/ {", 1)[1].split("\n    }", 1)[0]
    for directive in (
        "limit_conn upload_admission 2;",
        "limit_conn_status 503;",
        "proxy_http_version 1.1;",
        "proxy_request_buffering off;",
        "proxy_intercept_errors off;",
        "client_body_timeout 60s;",
    ):
        assert directive in api
    for name, status in (("upload_busy", 503),):
        response = text.split(f"location @{name} {{", 1)[1].split("\n    }", 1)[0]
        assert f"return {status} " in response and f'"code":"{name}"' in response
        assert "no-store" in response
        if status == 503:
            assert "Retry-After 1 always" in response
        if origin.name == "nginx.prod.conf":
            for header in (
                "X-Content-Type-Options",
                "X-Frame-Options",
                "Referrer-Policy",
                "Strict-Transport-Security",
                "Content-Security-Policy",
            ):
                assert f"add_header {header} " in response


def test_origin_upload_admission_maps_are_identical():
    blocks = [
        re.search(
            r'map "\$request_method:\$uri" \$upload_admission_key \{(.*?)\n}', p.read_text(), re.S
        )
        for p in ORIGINS
    ]
    assert all(blocks)
    assert blocks[0][1] == blocks[1][1]


def test_kubernetes_ingress_streams_bodies_to_the_api_deadline():
    import yaml

    ingress = yaml.safe_load((ROOT / "deploy/k8s/50-ingress.yaml").read_text())
    annotations = ingress["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/proxy-request-buffering"] == "off"
    assert annotations["nginx.ingress.kubernetes.io/proxy-http-version"] == "1.1"
    # This manifest routes /api directly to backend: frontend's shared limit is
    # not cluster admission. Controller-specific shared quotas need native proof.
