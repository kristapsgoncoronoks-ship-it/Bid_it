"""Legacy data must block replacement; storage failures must not report readiness."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app import storage_bootstrap

BACKEND = Path(__file__).resolve().parents[1]
PRIVATE_DETAIL = "private-provider-endpoint-key-and-secret"


@pytest.fixture(autouse=True)
def _development_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")


def _failure(code: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": PRIVATE_DETAIL},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "HeadBucket",
    )


def _client(monkeypatch) -> Mock:
    client = Mock()
    monkeypatch.setattr(storage_bootstrap.boto3, "client", Mock(return_value=client))
    return client


def test_empty_legacy_directory(tmp_path):
    storage_bootstrap.check_legacy(tmp_path)


@pytest.mark.parametrize("name", [".minio.sys", ".hidden-object", "known-object", "unknown-marker"])
def test_legacy_state_is_never_ignored_or_changed(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    (path / "original").write_bytes(b"legacy bytes remain unchanged")
    with pytest.raises(storage_bootstrap.BootstrapError, match="replacement is refused"):
        storage_bootstrap.check_legacy(tmp_path)
    assert (path / "original").read_bytes() == b"legacy bytes remain unchanged"
    assert list(tmp_path.iterdir()) == [path]


def test_missing_mount_fails_closed(tmp_path):
    with pytest.raises(storage_bootstrap.BootstrapError, match="Cannot inspect"):
        storage_bootstrap.check_legacy(tmp_path / "not-mounted")


def test_non_directory_mount_fails_closed(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a volume")
    with pytest.raises(storage_bootstrap.BootstrapError, match="Cannot inspect"):
        storage_bootstrap.check_legacy(path)


@pytest.mark.parametrize("error", [PermissionError, OSError, ValueError])
def test_inspection_failure_fails_closed_without_private_details(tmp_path, monkeypatch, error):
    def deny(*args):
        raise error(PRIVATE_DETAIL)

    monkeypatch.setattr(storage_bootstrap.os, "scandir", deny)
    with pytest.raises(storage_bootstrap.BootstrapError, match="Cannot inspect") as caught:
        storage_bootstrap.check_legacy(tmp_path)
    assert PRIVATE_DETAIL not in str(caught.value)


def test_iteration_failure_closes_directory_handle_and_refuses(tmp_path, monkeypatch):
    closed = []

    class FailedScan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def __next__(self):
            raise OSError(PRIVATE_DETAIL)

    monkeypatch.setattr(storage_bootstrap.os, "scandir", lambda path: FailedScan())
    with pytest.raises(storage_bootstrap.BootstrapError, match="Cannot inspect") as caught:
        storage_bootstrap.check_legacy(tmp_path)
    assert PRIVATE_DETAIL not in str(caught.value)
    assert closed == [True]


def test_existing_bucket_is_verified_and_never_recreated(monkeypatch):
    client = _client(monkeypatch)
    storage_bootstrap.initialize_bucket("http://s3:9000", "documents")
    client.head_bucket.assert_called_once_with(Bucket="documents")
    client.create_bucket.assert_not_called()
    client.close.assert_called_once_with()


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_only_404_authorizes_creation(monkeypatch, status):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = _failure(PRIVATE_DETAIL, status)
    with pytest.raises(storage_bootstrap.BootstrapError, match="could not be verified") as caught:
        storage_bootstrap.initialize_bucket("http://s3:9000", "documents")
    assert PRIVATE_DETAIL not in str(caught.value)
    client.create_bucket.assert_not_called()
    client.close.assert_called_once_with()


def test_network_failure_never_creates_bucket(monkeypatch):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = EndpointConnectionError(endpoint_url=PRIVATE_DETAIL)
    with pytest.raises(storage_bootstrap.BootstrapError, match="unavailable") as caught:
        storage_bootstrap.initialize_bucket("http://s3:9000", "documents")
    assert PRIVATE_DETAIL not in str(caught.value)
    client.create_bucket.assert_not_called()


@pytest.mark.parametrize("region", ["us-east-1", "eu-north-1"])
def test_creation_only_after_authenticated_absence(monkeypatch, region):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = _failure("404", 404)
    storage_bootstrap.initialize_bucket("http://s3:9000", "documents", region)
    expected = {"Bucket": "documents"}
    if region != "us-east-1":
        expected["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket.assert_called_once_with(**expected)


def test_concurrent_owned_bucket_is_verified_again(monkeypatch):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = [_failure("404", 404), None]
    client.create_bucket.side_effect = _failure("BucketAlreadyOwnedByYou", 409)
    storage_bootstrap.initialize_bucket("http://s3:9000", "documents")
    assert client.head_bucket.call_count == 2


def test_concurrent_foreign_bucket_is_refused(monkeypatch):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = _failure("404", 404)
    client.create_bucket.side_effect = _failure("BucketAlreadyExists", 409)
    with pytest.raises(storage_bootstrap.BootstrapError, match="creation failed"):
        storage_bootstrap.initialize_bucket("http://s3:9000", "documents")


def test_no_false_success_when_recheck_fails(monkeypatch):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = [_failure("404", 404), _failure("AccessDenied", 403)]
    client.create_bucket.side_effect = _failure("BucketAlreadyOwnedByYou", 409)
    with pytest.raises(
        storage_bootstrap.BootstrapError, match="could not be verified after creation"
    ):
        storage_bootstrap.initialize_bucket("http://s3:9000", "documents")


@pytest.mark.parametrize("endpoint,bucket", [("", "documents"), ("http://s3:9000", "")])
def test_empty_configuration_never_connects(monkeypatch, endpoint, bucket):
    _client(monkeypatch)
    with pytest.raises(storage_bootstrap.BootstrapError, match="explicit"):
        storage_bootstrap.initialize_bucket(endpoint, bucket)
    storage_bootstrap.boto3.client.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [ValueError(PRIVATE_DETAIL), EndpointConnectionError(endpoint_url=PRIVATE_DETAIL)],
)
def test_client_construction_failure_is_sanitized(monkeypatch, failure):
    monkeypatch.setattr(storage_bootstrap.boto3, "client", Mock(side_effect=failure))
    with pytest.raises(storage_bootstrap.BootstrapError, match="could not be configured") as caught:
        storage_bootstrap.initialize_bucket("http://s3:9000", "documents")
    assert PRIVATE_DETAIL not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("phase", ["construct", "head", "create", "recheck"])
def test_cli_refuses_provider_failures_without_raw_output(monkeypatch, capsys, phase):
    monkeypatch.setenv("STORAGE_S3_ENDPOINT_URL", "http://" + PRIVATE_DETAIL)
    monkeypatch.setenv("STORAGE_S3_BUCKET", "documents")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", PRIVATE_DETAIL)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", PRIVATE_DETAIL)
    client = _client(monkeypatch)
    if phase == "construct":
        storage_bootstrap.boto3.client.side_effect = ValueError(PRIVATE_DETAIL)
    elif phase == "head":
        client.head_bucket.side_effect = _failure("AccessDenied", 403)
    elif phase == "create":
        client.head_bucket.side_effect = _failure("404", 404)
        client.create_bucket.side_effect = ConnectionError(PRIVATE_DETAIL)
    else:
        client.head_bucket.side_effect = [_failure("404", 404), ConnectionError(PRIVATE_DETAIL)]
        client.create_bucket.side_effect = _failure("BucketAlreadyOwnedByYou", 409)
    assert storage_bootstrap.main(["initialize"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err
    assert PRIVATE_DETAIL not in output.err
    assert "Traceback" not in output.err
    assert "ready" not in output.err


def test_legacy_cli_does_not_leak_path_or_publish_ready(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(storage_bootstrap, "LEGACY_PATH", tmp_path / PRIVATE_DETAIL)
    assert storage_bootstrap.main(["check-legacy"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert PRIVATE_DETAIL not in output.err
    assert "Cannot inspect" in output.err


def test_cli_success_reports_readiness_only_after_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(storage_bootstrap, "LEGACY_PATH", tmp_path)
    assert storage_bootstrap.main(["check-legacy"]) == 0
    output = capsys.readouterr()
    assert output.out == "Legacy store is empty.\n" and output.err == ""
    monkeypatch.setenv("STORAGE_S3_ENDPOINT_URL", "http://s3:9000")
    monkeypatch.setenv("STORAGE_S3_BUCKET", "documents")
    _client(monkeypatch)
    assert storage_bootstrap.main(["initialize"]) == 0
    output = capsys.readouterr()
    assert output.out == "S3 bucket ready.\n" and output.err == ""


def test_module_cli_sanitizes_invalid_endpoint_configuration():
    env = {
        **os.environ,
        "ENVIRONMENT": "development",
        "STORAGE_S3_ENDPOINT_URL": "invalid-" + PRIVATE_DETAIL,
        "STORAGE_S3_BUCKET": "documents",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_ACCESS_KEY_ID": PRIVATE_DETAIL,
        "AWS_SECRET_ACCESS_KEY": PRIVATE_DETAIL,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "app.storage_bootstrap", "initialize"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "could not be configured" in proc.stderr
    assert PRIVATE_DETAIL not in proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("action", ["check-legacy", "initialize"])
@pytest.mark.parametrize("environment", ["production", " Production "])
def test_production_cli_refuses_before_any_storage_access(monkeypatch, capsys, action, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    check = Mock()
    initialize = Mock()
    monkeypatch.setattr(storage_bootstrap, "check_legacy", check)
    monkeypatch.setattr(storage_bootstrap, "initialize_bucket", initialize)
    assert storage_bootstrap.main([action]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "development-only" in output.err
    assert "approved provider and cutover configuration" in output.err
    check.assert_not_called()
    initialize.assert_not_called()


def test_successful_bucket_verification_still_refuses_cleanup_failure(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("STORAGE_S3_ENDPOINT_URL", "http://s3:9000")
    monkeypatch.setenv("STORAGE_S3_BUCKET", "documents")
    client = _client(monkeypatch)
    client.close.side_effect = RuntimeError(PRIVATE_DETAIL)
    assert storage_bootstrap.main(["initialize"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "cleanup failed" in output.err
    assert PRIVATE_DETAIL not in output.err


@pytest.mark.parametrize("cleanup_error", [RuntimeError(PRIVATE_DETAIL), KeyboardInterrupt()])
def test_cleanup_failure_preserves_primary_refusal(monkeypatch, cleanup_error):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = _failure("AccessDenied", 403)
    client.close.side_effect = cleanup_error
    with pytest.raises(storage_bootstrap.BootstrapError, match="could not be verified"):
        storage_bootstrap.initialize_bucket("http://s3:9000", "documents")
    client.close.assert_called_once_with()


def test_interruption_closes_client_without_swallowing_it(monkeypatch):
    client = _client(monkeypatch)
    client.head_bucket.side_effect = KeyboardInterrupt()
    client.close.side_effect = RuntimeError(PRIVATE_DETAIL)
    with pytest.raises(KeyboardInterrupt):
        storage_bootstrap.initialize_bucket("http://s3:9000", "documents")
    client.close.assert_called_once_with()
