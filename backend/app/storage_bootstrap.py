"""One-shot development object-store checks; never migrate or delete legacy data.

The legacy check inspects the original MinIO volume mounted read-only at
``/legacy-minio``. Bucket initialization uses the application's existing boto3
credentials. Production provider selection and cutover remain separate decisions.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

LEGACY_PATH = Path("/legacy-minio")


class BootstrapError(Exception):
    """An actionable refusal without private storage-provider diagnostics."""


def check_legacy(path: Path) -> None:
    """Refuse every existing entry or inspection failure on the legacy mount."""
    try:
        # Hidden MinIO metadata and unknown entries are state too. A missing or
        # inaccessible mount must never be interpreted as an empty volume.
        with os.scandir(path) as entries:
            nonempty = next(entries, None) is not None
    except Exception:  # noqa: BLE001 — inspection failures must not expose mount diagnostics
        raise BootstrapError(
            "Cannot inspect the required read-only legacy MinIO volume. "
            "Restore its mount/access and retry; no object-store cutover occurred."
        ) from None
    if nonempty:
        raise BootstrapError(
            "Legacy MinIO data or metadata exists. Automatic store replacement is refused. "
            "Keep miniodata unchanged. Migrate through the S3 API into a separate new "
            "volume, verify all required keys, hashes and metadata, and obtain the "
            "owner-reviewed cutover configuration. Do not delete the legacy volume "
            "or mount it as RustFS /data."
        )


def initialize_bucket(endpoint: str, bucket: str, region: str = "us-east-1") -> None:
    """Verify an existing bucket or create it only after authenticated absence."""
    if not endpoint or not bucket:
        raise BootstrapError("An explicit S3 endpoint and bucket are required.")
    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"mode": "standard", "max_attempts": 2},
            ),
        )
    except Exception:  # noqa: BLE001 — credential/configuration errors can include secrets
        raise BootstrapError(
            "S3 client could not be configured. Check credentials, endpoint and region, "
            "then retry. Existing objects were not changed."
        ) from None
    try:
        _prepare_bucket(client, bucket, region)
    except BaseException:
        # Release transport resources without replacing the primary refusal or
        # interruption with a secondary cleanup failure.
        try:
            client.close()
        except BaseException:
            pass
        raise
    else:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            raise BootstrapError("S3 client cleanup failed. Check storage and retry.") from None


def _prepare_bucket(client, bucket: str, region: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        # AccessDenied, provider errors and network failures must not authorize
        # creation or be reported as an already prepared bucket.
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            raise BootstrapError(
                "S3 bucket could not be verified. Check credentials, endpoint and "
                "storage health, then retry. Existing objects were not changed."
            ) from None
    except Exception:  # noqa: BLE001 — do not print private provider diagnostics
        raise BootstrapError(
            "S3 storage is unavailable. Check its health and retry. "
            "Existing objects were not changed."
        ) from None
    try:
        options = (
            {}
            if region == "us-east-1"
            else {"CreateBucketConfiguration": {"LocationConstraint": region}}
        )
        client.create_bucket(Bucket=bucket, **options)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
            raise BootstrapError("S3 bucket creation failed. Check storage and retry.") from None
        # A concurrent initializer may have created the bucket. Verify access
        # again; a foreign bucket or a failed recheck must not report readiness.
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:  # noqa: BLE001
            raise BootstrapError("S3 bucket could not be verified after creation. Retry.") from None
    except Exception:  # noqa: BLE001
        raise BootstrapError("S3 bucket creation failed. Check storage and retry.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check-legacy", "initialize"))
    args = parser.parse_args(argv)
    try:
        if os.environ.get("ENVIRONMENT", "").strip().lower() == "production":
            raise BootstrapError(
                "The bundled RustFS candidate is development-only. Use an approved "
                "provider and cutover configuration after review."
            )
        if args.action == "check-legacy":
            check_legacy(LEGACY_PATH)
        else:
            initialize_bucket(
                os.environ.get("STORAGE_S3_ENDPOINT_URL", ""),
                os.environ.get("STORAGE_S3_BUCKET", ""),
                os.environ.get("STORAGE_S3_REGION", "us-east-1"),
            )
    except BootstrapError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    sys.stdout.write(
        "Legacy store is empty.\n" if args.action == "check-legacy" else "S3 bucket ready.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
