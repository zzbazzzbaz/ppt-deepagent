from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from deepagents.backends.langsmith import LangSmithSandbox
from langsmith.sandbox import SandboxClient, SandboxClientError, Snapshot
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.snapshot import find_ready_snapshot

_BUILD_ASSETS = (
    "Dockerfile",
    "package.json",
    "package-lock.json",
    "requirements.txt",
)
_FS_CAPACITY_BYTES = 2 * 1024**3
_VCPUS = 2
_MEM_BYTES = 8 * 1024**3
_SANDBOX_MEM_BYTES = 2 * 1024**3
_BUILD_TIMEOUT_SECONDS = 3600
_BUILD_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 30
_VERIFY_SCRIPT = """const pptxgen = require('pptxgenjs');
const pptx = new pptxgen();
const slide = pptx.addSlide();
slide.addText('Sandbox verification', { x: 1, y: 1, w: 8, h: 1 });
pptx.writeFile({ fileName: '/workspace/work/original.pptx' });
"""


@dataclass(frozen=True)
class SnapshotSyncResult:
    action: Literal["created", "updated", "skipped"]
    snapshot: Snapshot


class _SnapshotCliSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LANGSMITH_", extra="ignore"
    )

    api_key: str


def _snapshot_api_key(*, _env_file: str | None = ".env") -> str:
    return _SnapshotCliSettings(_env_file=_env_file).api_key


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def prepare_build_context(project_root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    sandbox_dir = project_root / "sandbox"
    for name in _BUILD_ASSETS:
        shutil.copy2(sandbox_dir / name, destination / name)
    skill_src = project_root / "agent" / "skills" / "pptx"
    skill_dst = destination / "agent" / "skills" / "pptx"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_src, skill_dst, ignore=shutil.ignore_patterns("*__pycache__", "*.pyc", ".DS_Store"))


def _delete_failed_snapshots(client: SandboxClient, name: str) -> None:
    for snapshot in client.list_snapshots(name_contains=name):
        if snapshot.name != name or snapshot.status != "failed":
            continue
        client.delete_snapshot(snapshot.id)
        print(f"Deleted failed snapshot {snapshot.name} ({snapshot.id})")


def _find_snapshot(client: SandboxClient, name: str) -> Snapshot | None:
    matches = [
        snapshot
        for snapshot in client.list_snapshots(name_contains=name)
        if snapshot.name == name
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Expected at most one snapshot named {name!r}, found {len(matches)}"
        )
    return matches[0] if matches else None


def build_snapshot(client: SandboxClient, name: str, project_root: Path) -> Snapshot:
    existing = [
        snapshot
        for snapshot in client.list_snapshots(name_contains=name)
        if snapshot.name == name
    ]
    for snapshot in existing:
        if snapshot.status != "failed":
            raise RuntimeError(f"Snapshot already exists: {name!r}")
        client.delete_snapshot(snapshot.id)
        print(f"Deleted failed snapshot {snapshot.name} ({snapshot.id})")

    with tempfile.TemporaryDirectory(prefix="pptx-snapshot-") as temp:
        context = Path(temp)
        prepare_build_context(project_root, context)
        last_error: SandboxClientError | None = None
        for attempt in range(1, _BUILD_ATTEMPTS + 1):
            try:
                return client.create_snapshot_from_dockerfile(
                    name,
                    dockerfile="Dockerfile",
                    context=context,
                    fs_capacity_bytes=_FS_CAPACITY_BYTES,
                    vcpus=_VCPUS,
                    mem_bytes=_MEM_BYTES,
                    timeout=_BUILD_TIMEOUT_SECONDS,
                    on_build_log=lambda line: print(line, end=""),
                )
            except SandboxClientError as exc:
                last_error = exc
                print(f"Build attempt {attempt} failed: {exc}")
                _delete_failed_snapshots(client, name)
                if attempt < _BUILD_ATTEMPTS:
                    time.sleep(_RETRY_DELAY_SECONDS)
        raise RuntimeError(
            f"Snapshot build failed after {_BUILD_ATTEMPTS} attempts"
        ) from last_error


def _require_success(output: object, label: str) -> None:
    exit_code = getattr(output, "exit_code", None)
    if exit_code != 0:
        result = getattr(output, "output", "")
        raise RuntimeError(f"{label} failed: {result}")


def _run_commands(backend: LangSmithSandbox, commands: Iterable[tuple[str, str]]) -> None:
    for label, command in commands:
        _require_success(backend.execute(command), label)


def verify_snapshot(client: SandboxClient, name: str) -> None:
    snapshot = find_ready_snapshot(client, name)
    sandbox_name = f"{name}-verify-{uuid4().hex[:8]}"
    sandbox = client.create_sandbox(
        snapshot_id=snapshot.id,
        name=sandbox_name,
        wait_for_ready=True,
        mem_bytes=_SANDBOX_MEM_BYTES,
    )
    backend = LangSmithSandbox(sandbox=sandbox)
    try:
        responses = backend.upload_files(
            [("/workspace/work/verify.js", _VERIFY_SCRIPT.encode("utf-8"))]
        )
        if len(responses) != 1 or responses[0].error is not None:
            error = responses[0].error if responses else "missing upload response"
            raise RuntimeError(f"Failed to upload verification script: {error}")

        _run_commands(
            backend,
            (
                (
                    "runtime dependencies",
                    "node --version && python --version && soffice --version && pdftoppm -v && gcc --version",
                ),
                (
                    "package imports",
                    "node -e \"require('pptxgenjs'); require('react'); require('react-dom'); require('react-icons'); require('sharp')\" && python -c \"import defusedxml, lxml; from PIL import Image; from markitdown import MarkItDown\"",
                ),
                ("create PPTX", "node /workspace/work/verify.js"),
                (
                    "extract PPTX text",
                    "markitdown /workspace/work/original.pptx > /workspace/work/original.md",
                ),
                (
                    "OOXML round-trip",
                    "unzip -q /workspace/work/original.pptx -d /workspace/work/unpacked && python -c \"import lxml.etree as ET; path = '/workspace/work/unpacked/ppt/slides/slide1.xml'; tree = ET.parse(path); tree.write(path)\" && cd /workspace/work/unpacked && zip -qr /workspace/work/edited.pptx .",
                ),
                (
                    "convert PPTX to PDF",
                    "soffice --headless --convert-to pdf --outdir /workspace/work /workspace/work/edited.pptx",
                ),
                (
                    "render PDF pages",
                    "pdftoppm -jpeg -r 150 /workspace/work/edited.pdf /workspace/work/slide",
                ),
                (
                    "verify artifacts",
                    "test -s /workspace/work/original.md && test -s /workspace/work/slide-1.jpg",
                ),
            ),
        )
    finally:
        client.delete_sandbox(sandbox.name)


def create_snapshot_from_image(
    client: SandboxClient,
    name: str,
    docker_image: str,
    *,
    registry_id: str | None = None,
) -> Snapshot:
    existing = [
        snapshot
        for snapshot in client.list_snapshots(name_contains=name)
        if snapshot.name == name
    ]
    for snapshot in existing:
        if snapshot.status != "failed":
            raise RuntimeError(f"Snapshot already exists: {name!r}")
        client.delete_snapshot(snapshot.id)
        print(f"Deleted failed snapshot {snapshot.name} ({snapshot.id})")

    return client.create_snapshot(
        name,
        docker_image=docker_image,
        fs_capacity_bytes=_FS_CAPACITY_BYTES,
        timeout=_BUILD_TIMEOUT_SECONDS,
        registry_id=registry_id,
    )


def sync_snapshot_from_image(
    client: SandboxClient,
    latest_name: str,
    candidate_name: str,
    docker_image: str,
    image_digest: str,
) -> SnapshotSyncResult:
    existing = _find_snapshot(client, latest_name)
    if (
        existing is not None
        and existing.status == "ready"
        and existing.image_digest == image_digest
    ):
        return SnapshotSyncResult("skipped", existing)

    if _find_snapshot(client, candidate_name) is not None:
        raise RuntimeError(f"Candidate snapshot already exists: {candidate_name!r}")

    candidate = create_snapshot_from_image(client, candidate_name, docker_image)
    try:
        verify_snapshot(client, candidate_name)
    except Exception:
        client.delete_snapshot(candidate.id)
        raise

    old_image = existing.docker_image if existing is not None else None
    if existing is not None:
        client.delete_snapshot(existing.id)

    try:
        latest = create_snapshot_from_image(client, latest_name, docker_image)
        verify_snapshot(client, latest_name)
    except Exception as release_error:
        failed_latest = _find_snapshot(client, latest_name)
        if failed_latest is not None:
            client.delete_snapshot(failed_latest.id)
        if old_image is None:
            release_error.add_note(f"Validated candidate retained: {candidate.name}")
            raise
        try:
            create_snapshot_from_image(client, latest_name, old_image)
            verify_snapshot(client, latest_name)
        except Exception as rollback_error:
            raise RuntimeError(
                f"Snapshot release failed: {release_error}; "
                f"rollback failed: {rollback_error}; "
                f"candidate retained: {candidate.name}"
            ) from rollback_error
        client.delete_snapshot(candidate.id)
        raise
    client.delete_snapshot(candidate.id)
    return SnapshotSyncResult(
        "updated" if existing is not None else "created", latest
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build, verify, or synchronize the PPTX Sandbox snapshot."
    )
    parser.add_argument(
        "command", choices=("build", "verify", "from-image", "sync-image")
    )
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--docker-image", help="Docker image reference (required for from-image)"
    )
    parser.add_argument(
        "--registry-id", help="Optional private registry ID for from-image"
    )
    parser.add_argument(
        "--candidate-name", help="Candidate snapshot name (required for sync-image)"
    )
    parser.add_argument(
        "--image-digest", help="Immutable image digest (required for sync-image)"
    )
    args = parser.parse_args()

    client = SandboxClient(api_key=_snapshot_api_key())
    try:
        if args.command == "build":
            snapshot = build_snapshot(client, args.name, _project_root())
            print(f"Snapshot created: {snapshot.name} ({snapshot.id}) [{snapshot.status}]")
        elif args.command == "from-image":
            if not args.docker_image:
                parser.error("--docker-image is required for from-image")
            snapshot = create_snapshot_from_image(
                client,
                args.name,
                args.docker_image,
                registry_id=args.registry_id,
            )
            print(
                f"Snapshot created from image: {snapshot.name} ({snapshot.id}) "
                f"[{snapshot.status}] image={snapshot.docker_image} digest={snapshot.image_digest}"
            )
        elif args.command == "sync-image":
            if not args.docker_image:
                parser.error("--docker-image is required for sync-image")
            if not args.candidate_name:
                parser.error("--candidate-name is required for sync-image")
            if not args.image_digest:
                parser.error("--image-digest is required for sync-image")
            result = sync_snapshot_from_image(
                client,
                latest_name=args.name,
                candidate_name=args.candidate_name,
                docker_image=args.docker_image,
                image_digest=args.image_digest,
            )
            print(
                f"Snapshot sync {result.action}: {result.snapshot.name} "
                f"({result.snapshot.id}) [{result.snapshot.status}] "
                f"image={result.snapshot.docker_image} "
                f"digest={result.snapshot.image_digest}"
            )
        else:
            verify_snapshot(client, args.name)
            print(f"Snapshot verification passed: {args.name}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
