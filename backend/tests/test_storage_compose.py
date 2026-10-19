"""Guard the actual bundled-store cutover and production credential boundaries."""

from tests.test_ops_deploy_hygiene import _compose, _load


def test_new_store_never_mounts_legacy_data_and_waits_for_read_only_inspection():
    services = _load("docker-compose.yml")["services"]
    guard = services["object-store-legacy-guard"]
    store = services["minio"]
    assert guard["build"] == "./backend"
    assert guard["command"] == ["python", "-m", "app.storage_bootstrap", "check-legacy"]
    assert guard["volumes"] == ["miniodata:/legacy-minio:ro"]
    assert store["volumes"] == ["rustfsdata:/data"]
    assert store["depends_on"]["object-store-legacy-guard"] == {
        "condition": "service_completed_successfully"
    }
    assert store["user"] == "10001:10001"
    assert store["read_only"] is True and store["cap_drop"] == ["ALL"]
    assert store["ports"] == ["127.0.0.1:9000:9000"]
    assert store["environment"]["RUSTFS_CONSOLE_ENABLE"] == "false"
    assert {"pgdata", "miniodata", "rustfsdata"} <= set(_load("docker-compose.yml")["volumes"])


def test_initializer_uses_audited_application_and_real_bucket_health_gates_api():
    services = _load("docker-compose.yml")["services"]
    init = services["minio-init"]
    assert init["build"] == "./backend" and "image" not in init
    assert init["command"] == ["python", "-m", "app.storage_bootstrap", "initialize"]
    assert "entrypoint" not in init
    assert init["depends_on"]["minio"]["condition"] == "service_healthy"
    assert services["backend"]["depends_on"]["minio-init"] == {
        "condition": "service_completed_successfully"
    }
    assert init["healthcheck"]["disable"] is True
    assert init["read_only"] is True and init["cap_drop"] == ["ALL"]
    credentials = init["environment"]
    for name in ("backend", "worker"):
        app = services[name]["environment"]
        for key in (
            "STORAGE_S3_ENDPOINT_URL",
            "STORAGE_S3_BUCKET",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        ):
            assert app[key] == credentials[key]
    assert services["minio"]["environment"]["RUSTFS_ACCESS_KEY"] == credentials["AWS_ACCESS_KEY_ID"]
    assert (
        services["minio"]["environment"]["RUSTFS_SECRET_KEY"]
        == credentials["AWS_SECRET_ACCESS_KEY"]
    )


def test_production_and_base_mode_override_reach_both_refusal_actions():
    base = _load("docker-compose.yml")["services"]
    production = _compose("docker-compose.yml", "docker-compose.prod.yml")["services"]
    for name in ("object-store-legacy-guard", "minio-init"):
        assert base[name]["environment"]["ENVIRONMENT"] == "${ENVIRONMENT:-development}"
        assert production[name]["environment"]["ENVIRONMENT"] == "production"
        assert "entrypoint" not in production[name]
    assert production["minio"].get("ports", []) == []
    # Actual server fields matter: inherited development defaults must not
    # survive merely because an obsolete MINIO_* field was overridden.
    env = production["minio"]["environment"]
    assert env["RUSTFS_ACCESS_KEY"] == "${MINIO_ROOT_USER:?set MINIO_ROOT_USER}"
    assert env["RUSTFS_SECRET_KEY"] == "${MINIO_ROOT_PASSWORD:?set MINIO_ROOT_PASSWORD}"
    assert "MINIO_ROOT_PASSWORD" not in env


def test_hostinger_keeps_independent_local_storage_without_bundled_provider():
    for files in (
        ("docker-compose.hostinger.yml",),
        ("docker-compose.hostinger.yml", "docker-compose.images.yml"),
    ):
        services = _compose(*files)["services"]
        assert not {"minio", "minio-init", "object-store-legacy-guard"} & services.keys()
        for name in ("backend", "worker"):
            assert services[name]["environment"]["STORAGE_BACKEND"] == "local"
