"""Sync agent/skills/ to the MinIO ``skills/`` prefix.

Local credentials come from ``MINIO_ACCESS_KEY`` and ``MINIO_SECRET_KEY``
(local environment or ``.env`` only; never commit them). Endpoint, bucket,
region, and path-style settings reuse the ``MINIO_*`` variables documented
in ``.env.example``.

Usage:
    uv run python scripts/sync_skills_to_minio.py [--delete] [--dry-run]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config
from pydantic_settings import BaseSettings, SettingsConfigDict

SKILLS_PREFIX = "skills"
_EXCLUDED_NAMES = frozenset({"__pycache__", ".DS_Store", ".gitkeep"})
_ANCHOR_KEYS = (
    "skills/pptx/SKILL.md",
    "skills/pptx/LICENSE.txt",
    "skills/pptx/scripts/office/validate.py",
)
_ANCHOR_PREFIXES = ("skills/pptx/scripts/office/schemas/",)


class _SyncCliSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MINIO_", extra="ignore"
    )

    endpoint_url: str
    bucket: str
    region: str = "us-east-1"
    path_style: bool = True
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class SyncResult:
    uploaded: tuple[str, ...]
    deleted: tuple[str, ...]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def collect_local_skills(skills_root: Path) -> dict[str, Path]:
    """Map MinIO object keys to local files under agent/skills/."""
    if not skills_root.is_dir():
        raise RuntimeError(f"Skills directory does not exist: {skills_root}")

    files: dict[str, Path] = {}
    for path in sorted(skills_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(skills_root)
        if any(part in _EXCLUDED_NAMES for part in relative.parts):
            continue
        files[f"{SKILLS_PREFIX}/{relative.as_posix()}"] = path
    if not files:
        raise RuntimeError(f"No skill files found under {skills_root}")
    return files


def list_remote_keys(client: object, bucket: str, prefix: str) -> set[str]:
    keys: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def sync_skills(
    client: object,
    bucket: str,
    local_files: dict[str, Path],
    *,
    delete: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    uploaded: list[str] = []
    for key, path in sorted(local_files.items()):
        if dry_run:
            print(f"would upload {path} -> s3://{bucket}/{key}")
        else:
            client.put_object(Bucket=bucket, Key=key, Body=path.read_bytes())
            print(f"uploaded s3://{bucket}/{key}")
        uploaded.append(key)

    deleted: list[str] = []
    if delete:
        stale = sorted(list_remote_keys(client, bucket, SKILLS_PREFIX) - set(local_files))
        for key in stale:
            if dry_run:
                print(f"would delete s3://{bucket}/{key}")
            else:
                client.delete_object(Bucket=bucket, Key=key)
                print(f"deleted s3://{bucket}/{key}")
            deleted.append(key)
    return SyncResult(tuple(uploaded), tuple(deleted))


def verify_skills_sync(client: object, bucket: str, expected_keys: set[str]) -> None:
    remote_keys = list_remote_keys(client, bucket, SKILLS_PREFIX)
    missing = sorted(expected_keys - remote_keys)
    extra = sorted(remote_keys - expected_keys)
    if missing or extra:
        raise RuntimeError(
            f"Skills sync verification failed: missing={missing}, extra={extra}"
        )
    for anchor in _ANCHOR_KEYS:
        if anchor not in expected_keys:
            raise RuntimeError(f"Local skills tree is missing anchor file: {anchor}")
    for prefix in _ANCHOR_PREFIXES:
        if not any(key.startswith(prefix) for key in expected_keys):
            raise RuntimeError(f"Local skills tree is empty under prefix: {prefix}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync local agent/skills/ to the MinIO skills/ prefix."
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete remote skills/ objects that no longer exist locally.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without uploading or deleting.",
    )
    args = parser.parse_args()

    settings = _SyncCliSettings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name=settings.region,
        config=Config(
            s3={"addressing_style": "path" if settings.path_style else "virtual"}
        ),
    )

    local_files = collect_local_skills(_project_root() / "agent" / "skills")
    result = sync_skills(
        client,
        settings.bucket,
        local_files,
        delete=args.delete,
        dry_run=args.dry_run,
    )
    print(f"Summary: uploaded={len(result.uploaded)}, deleted={len(result.deleted)}")

    if not args.dry_run:
        verify_skills_sync(client, settings.bucket, set(local_files))
        print("Skills sync verification passed.")


if __name__ == "__main__":
    main()
