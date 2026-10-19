"""Boot contracts missed by a successful image pull or an OS-package scan.

The vendor entrypoint generates two files before starting; its default port is
5432. Both facts must agree with the hardened optional Kubernetes deployment.
The source-version check also covers a binary Trivy does not identify as an OS
package: the vendor's 1.26.0 image reports clean despite its upstream SCRAM CVEs.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _resources():
    documents = list(yaml.safe_load_all((ROOT / "deploy/k8s/25-pgbouncer.yaml").read_text()))
    return {doc["kind"]: doc for doc in documents}


def _pod():
    return _resources()["Deployment"]["spec"]["template"]["spec"]


def test_pooler_port_matches_configuration_container_service_and_liveness():
    resources = _resources()
    port = int(resources["ConfigMap"]["data"]["LISTEN_PORT"])
    container = _pod()["containers"][0]
    service = resources["Service"]["spec"]["ports"][0]
    assert port == 6432  # documented application DATABASE_URL contract
    assert container["ports"][0]["containerPort"] == port
    assert service["port"] == service["targetPort"] == port
    assert container["livenessProbe"]["tcpSocket"]["port"] == port
    assert container["startupProbe"]["exec"]["command"] == [
        "pg_isready",
        "-h",
        "127.0.0.1",
        "-p",
        str(port),
    ]


def test_vendor_generated_configuration_has_a_bounded_nonpersistent_writable_mount():
    pod = _pod()
    container = pod["containers"][0]
    mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
    volume = next(v for v in pod["volumes"] if v["name"] == mounts["/etc/pgbouncer"]["name"])
    assert volume["emptyDir"] == {"medium": "Memory", "sizeLimit": "1Mi"}
    assert not mounts["/etc/pgbouncer"].get("readOnly", False)
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    context = pod["securityContext"]
    assert context["runAsNonRoot"] is True
    assert context["runAsUser"] == context["runAsGroup"] == context["fsGroup"] == 70
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_readiness_checks_authenticated_backend_sql_instead_of_only_a_listener():
    probe = _pod()["containers"][0]["readinessProbe"]
    command = probe["exec"]["command"]
    assert command[:2] == ["/bin/sh", "-ec"]
    script = command[2]
    for required in (
        'PGPASSWORD="$DB_PASSWORD"',
        "PGCONNECT_TIMEOUT=3",
        "psql -X --no-password",
        "--port=6432",
        '--username="$DB_USER"',
        '--dbname="$DB_NAME"',
        "--command='SELECT 1'",
    ):
        assert required in script
    assert probe["timeoutSeconds"] > 3
    assert "tcpSocket" not in probe


def test_pooling_and_scram_are_preserved_with_credentials_only_from_secret():
    resources = _resources()
    config = resources["ConfigMap"]["data"]
    assert config["POOL_MODE"] == "transaction"
    assert config["AUTH_TYPE"] == "scram-sha-256"
    container = _pod()["containers"][0]
    assert {env["name"]: env["valueFrom"]["secretKeyRef"] for env in container["env"]} == {
        name: {"name": "invoiceiq-secrets", "key": name} for name in ("DB_USER", "DB_PASSWORD")
    }


def test_pooler_is_a_release_artifact_with_verified_patched_upstream_source():
    assert _pod()["containers"][0]["image"] == "ghcr.io/OWNER/REPO/pgbouncer:VERSION"
    dockerfile = (ROOT / "deploy/pgbouncer/Dockerfile").read_text()
    stages = [line.split()[1] for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert (
        stages
        == [
            "edoburu/pgbouncer:v1.26.0-p0@sha256:"
            "b17551c776ef7e5769ef80b956d20f85e2fd25dd8912d31d58f782aad495b711"
        ]
        * 2
    )
    assert "https://www.pgbouncer.org/downloads/files/1.26.1/pgbouncer-1.26.1.tar.gz" in dockerfile
    assert "00886a2994b4844cd37110970df777772cfc42a74c400522c82fba509a3f42d9" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "install -m 0755 /src/pgbouncer-1.26.1/pgbouncer /usr/bin/pgbouncer" in dockerfile
    assert "COPY --from=build /src/pgbouncer-1.26.1/pgbouncer /usr/bin/pgbouncer" in dockerfile
    assert "/usr/share/licenses/pgbouncer/COPYRIGHT" in dockerfile
    assert "/usr/share/licenses/pgbouncer/libusual-COPYRIGHT" in dockerfile
    assert "USER postgres" in dockerfile
