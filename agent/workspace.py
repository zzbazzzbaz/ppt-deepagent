from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from deepagents.backends.protocol import FileUploadResponse, SandboxBackendProtocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"


@dataclass(frozen=True)
class ThreadWorkspace:
    root: Path
    input: Path
    work: Path
    output: Path


def thread_workspace(thread_id: str) -> ThreadWorkspace:
    normalized_thread_id = str(UUID(thread_id))
    root = WORKSPACE_ROOT / normalized_thread_id
    return ThreadWorkspace(
        root=root,
        input=root / "input",
        work=root / "work",
        output=root / "output",
    )


def collect_uploads(local_root: Path, remote_root: str) -> list[tuple[str, bytes]]:
    if not local_root.exists():
        return []

    remote_base = PurePosixPath(remote_root)
    uploads: list[tuple[str, bytes]] = []
    for path in sorted(local_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative_path = path.relative_to(local_root)
        remote_path = remote_base.joinpath(*relative_path.parts)
        uploads.append((str(remote_path), path.read_bytes()))
    return uploads


def _validate_upload_responses(
    uploads: list[tuple[str, bytes]], responses: list[FileUploadResponse]
) -> None:
    if len(responses) != len(uploads):
        raise RuntimeError(
            f"Upload response count mismatch: expected {len(uploads)}, got {len(responses)}"
        )

    expected_paths = [path for path, _ in uploads]
    response_paths = [response.path for response in responses]
    if response_paths != expected_paths:
        raise RuntimeError(
            f"Upload response paths mismatch: expected {expected_paths}, got {response_paths}"
        )

    failures = [
        f"{response.path}: {response.error}"
        for response in responses
        if response.error is not None
    ]
    if failures:
        raise RuntimeError("Workspace upload failed: " + "; ".join(failures))


def _prepare_local_uploads(workspace: ThreadWorkspace) -> list[tuple[str, bytes]]:
    for directory in (workspace.input, workspace.work, workspace.output):
        directory.mkdir(parents=True, exist_ok=True)

    uploads = collect_uploads(workspace.input, "/workspace/input")
    uploads.extend(collect_uploads(workspace.work, "/workspace/work"))
    return uploads


async def initialize_thread_workspace(
    backend: SandboxBackendProtocol,
    workspace: ThreadWorkspace,
) -> None:
    uploads = await asyncio.to_thread(_prepare_local_uploads, workspace)

    mkdir_result = await backend.aexecute(
        "mkdir -p -- /workspace/input /workspace/work"
    )
    if mkdir_result.exit_code != 0:
        raise RuntimeError(
            f"Failed to initialize remote workspace: {mkdir_result.output}"
        )

    if not uploads:
        return

    responses = await backend.aupload_files(uploads)
    _validate_upload_responses(uploads, responses)
