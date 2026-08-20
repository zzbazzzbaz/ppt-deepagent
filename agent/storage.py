from __future__ import annotations

from typing import Protocol

import boto3
from botocore.config import Config

from agent.settings import MinioSettings, minio_settings


class S3Client(Protocol):
    """Structural type for the boto3 S3 client used by storage tools."""

    def get_paginator(self, operation_name: str) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> object: ...

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> object: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...


def create_s3_client(settings: MinioSettings | None = None) -> S3Client:
    """Create a boto3 S3 client bound to the configured MinIO endpoint."""
    resolved = settings if settings is not None else minio_settings
    return boto3.client(
        "s3",
        endpoint_url=resolved.endpoint_url,
        aws_access_key_id=resolved.access_key,
        aws_secret_access_key=resolved.secret_key,
        region_name=resolved.region,
        config=Config(
            s3={"addressing_style": "path" if resolved.path_style else "virtual"},
        ),
    )


def list_object_keys(client: S3Client, bucket: str, prefix: str) -> list[str]:
    """List all object keys under ``prefix`` (the prefix itself is excluded)."""
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"] != prefix:
                keys.append(obj["Key"])
    return sorted(keys)


def get_object_body(client: S3Client, bucket: str, key: str) -> bytes:
    """Download and return the full body of a single object."""
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def put_object_body(client: S3Client, bucket: str, key: str, body: bytes) -> None:
    """Upload ``body`` to ``key``, overwriting any existing object."""
    client.put_object(Bucket=bucket, Key=key, Body=body)


def delete_object(client: S3Client, bucket: str, key: str) -> None:
    """Delete a single object, ignoring missing keys."""
    client.delete_object(Bucket=bucket, Key=key)
