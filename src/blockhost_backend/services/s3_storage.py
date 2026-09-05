"""Thin wrapper around boto3 for S3 world-backup operations.

Used by both the Agent (upload/download zips) and the Control Plane
(checking key existence, generating presigned URLs if needed later).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _env(key: str, fallback: str = "") -> str:
    """Read from env — works on both the agent and the control-plane."""
    return os.environ.get(key, fallback)


@lru_cache(maxsize=1)
def get_s3_client():
    """Return a reusable, thread-safe boto3 S3 client."""
    return boto3.client(
        "s3",
        aws_access_key_id=_env("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_env("AWS_SECRET_ACCESS_KEY"),
        region_name=_env("AWS_REGION", "ap-southeast-2"),
        config=BotoConfig(
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def is_s3_configured() -> bool:
    """True when bucket + credentials are present enough to attempt S3 ops."""
    return bool(
        _env("S3_BACKUP_BUCKET_NAME")
        and _env("AWS_ACCESS_KEY_ID")
        and _env("AWS_SECRET_ACCESS_KEY")
    )


def require_s3_configured() -> None:
    """Raise if migration/backup S3 is not configured."""
    missing = [
        name
        for name, val in (
            ("S3_BACKUP_BUCKET_NAME", _env("S3_BACKUP_BUCKET_NAME")),
            ("AWS_ACCESS_KEY_ID", _env("AWS_ACCESS_KEY_ID")),
            ("AWS_SECRET_ACCESS_KEY", _env("AWS_SECRET_ACCESS_KEY")),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "S3 is not configured for migrations. Set: " + ", ".join(missing)
        )


def get_bucket_name() -> str:
    name = _env("S3_BACKUP_BUCKET_NAME")
    if not name:
        raise RuntimeError("S3_BACKUP_BUCKET_NAME is not configured")
    return name


def s3_key_for_server(server_id: str) -> str:
    """Canonical S3 object key for a server's migration snapshot."""
    return f"migrations/{server_id}/world.zip"


def upload_file_to_s3(local_path: str, s3_key: str) -> None:
    """Upload a local file to S3."""
    require_s3_configured()
    client = get_s3_client()
    bucket = get_bucket_name()
    logger.info("Uploading %s → s3://%s/%s", local_path, bucket, s3_key)
    client.upload_file(local_path, bucket, s3_key)
    logger.info("Upload complete: s3://%s/%s", bucket, s3_key)


def download_file_from_s3(s3_key: str, local_path: str) -> None:
    """Download an S3 object to a local file."""
    require_s3_configured()
    client = get_s3_client()
    bucket = get_bucket_name()
    logger.info("Downloading s3://%s/%s → %s", bucket, s3_key, local_path)
    client.download_file(bucket, s3_key, local_path)
    logger.info("Download complete: %s", local_path)


def s3_key_exists(s3_key: str) -> bool:
    """Check whether an S3 key exists (HEAD request)."""
    require_s3_configured()
    client = get_s3_client()
    bucket = get_bucket_name()
    try:
        client.head_object(Bucket=bucket, Key=s3_key)
        return True
    except ClientError:
        return False


def delete_s3_object(s3_key: str) -> bool:
    """Delete an S3 object. Returns True if delete was attempted successfully."""
    if not is_s3_configured():
        logger.warning("Skipping S3 delete for %s — S3 not configured", s3_key)
        return False
    client = get_s3_client()
    bucket = get_bucket_name()
    try:
        client.delete_object(Bucket=bucket, Key=s3_key)
        logger.info("Deleted s3://%s/%s", bucket, s3_key)
        return True
    except ClientError as e:
        logger.warning("Failed to delete s3://%s/%s: %s", bucket, s3_key, e)
        return False


def delete_migration_snapshot(server_id: str) -> bool:
    """Remove the migration zip for a server (best-effort)."""
    return delete_s3_object(s3_key_for_server(server_id))
