from __future__ import annotations

import asyncio
import shlex
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from deepagents.backends.protocol import FileDownloadResponse, SandboxBackendProtocol
from langchain.tools import tool
from langchain_core.tools import BaseTool, ToolException

from agent.workspace import ThreadWorkspace

_REMOTE_WORK = PurePosixPath("/workspace/work")


async def _collect_remote_files(
    backend: SandboxBackendProtocol,
) -> list[tuple[str, Path]]:
    result = await backend.aglob("**/*", str(_REMOTE_WORK))
    if result.error is not None:
        raise ToolException(f"Failed to list remote work directory: {result.error}")
    if result.truncated:
        raise ToolException("Remote work directory listing was truncated.")

    files: list[tuple[str, Path]] = []
    relative_paths: set[Path] = set()
    folded_paths: set[str] = set()
    for entry in result.matches or []:
        if entry.get("is_dir", False):
            continue
        listed_path = PurePosixPath(entry["path"])
        remote_path = (
            listed_path
            if listed_path.is_absolute()
            else _REMOTE_WORK.joinpath(listed_path)
        )
        try:
            relative_path = remote_path.relative_to(_REMOTE_WORK)
        except ValueError as exc:
            raise ToolException(f"Remote path is outside work directory: {remote_path}") from exc
        if not relative_path.parts or any(part in {"", ".", ".."} for part in relative_path.parts):
            raise ToolException(f"Invalid remote work path: {remote_path}")

        local_relative_path = Path(*relative_path.parts)
        folded_path = local_relative_path.as_posix().casefold()
        if local_relative_path in relative_paths or folded_path in folded_paths:
            raise ToolException(f"Conflicting remote work path: {remote_path}")
        relative_paths.add(local_relative_path)
        folded_paths.add(folded_path)
        files.append((str(remote_path), local_relative_path))

    return sorted(files)


async def _reject_remote_symlinks(backend: SandboxBackendProtocol) -> None:
    result = await backend.aexecute("find /workspace/work -type l -print -quit")
    if result.exit_code != 0:
        raise ToolException(f"Failed to inspect remote work directory: {result.output}")
    if result.output.strip():
        raise ToolException(f"Remote work directory contains a symbolic link: {result.output}")


async def _validate_pptx_files(
    backend: SandboxBackendProtocol,
    pptx_paths: list[str],
) -> None:
    for remote_path in pptx_paths:
        result = await backend.aexecute(
            "python /skills/pptx/scripts/office/validate.py "
            + shlex.quote(remote_path)
        )
        if result.exit_code != 0:
            raise ToolException(
                f"PPTX validation failed for {remote_path}: {result.output}"
            )


def _validate_downloads(
    requested_paths: list[str],
    responses: list[FileDownloadResponse],
) -> None:
    if len(requested_paths) != len(responses):
        raise ToolException(
            "Remote work download response count does not match the request."
        )
    response_paths = [response.path for response in responses]
    if response_paths != requested_paths:
        raise ToolException("Remote work download response paths do not match the request.")
    failures = [
        f"{response.path}: {response.error or 'empty_content'}"
        for response in responses
        if response.error is not None or response.content is None
    ]
    if failures:
        raise ToolException("Failed to download remote work files: " + "; ".join(failures))


def _next_output_path(output_root: Path, now: datetime) -> Path:
    stem = now.strftime("%Y%m%d-%H%M%S")
    candidate = output_root / stem
    suffix = 1
    while candidate.exists():
        candidate = output_root / f"{stem}-{suffix:02d}"
        suffix += 1
    return candidate


def _prepare_staging_tree(
    workspace: ThreadWorkspace,
    files: list[tuple[str, Path]],
    responses: list[FileDownloadResponse],
    output_path: Path,
) -> tuple[Path, Path, Path]:
    workspace.root.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".save-output-", dir=workspace.root))
    staged_work = staging_root / "work"
    staged_output = staging_root / "output"
    staged_work.mkdir()
    staged_output.mkdir()
    try:
        for (_, relative_path), response in zip(files, responses, strict=True):
            destination = staged_work / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            assert response.content is not None
            destination.write_bytes(response.content)

        for _, relative_path in files:
            if relative_path.suffix.lower() != ".pptx":
                continue
            source = staged_work / relative_path
            destination = staged_output / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    return staging_root, staged_work, staged_output


def _commit_staging_tree(
    workspace: ThreadWorkspace,
    staging_root: Path,
    staged_work: Path,
    staged_output: Path,
    output_path: Path,
) -> None:
    workspace.output.mkdir(parents=True, exist_ok=True)
    backup_work = workspace.root / f".work-backup-{uuid4().hex}"
    had_existing_work = workspace.work.exists()
    committed_work = False
    try:
        if had_existing_work:
            workspace.work.rename(backup_work)
        staged_work.rename(workspace.work)
        committed_work = True
        staged_output.rename(output_path)
    except Exception:
        if output_path.exists():
            shutil.rmtree(output_path, ignore_errors=True)
        if committed_work and workspace.work.exists():
            shutil.rmtree(workspace.work, ignore_errors=True)
        if had_existing_work and backup_work.exists():
            backup_work.rename(workspace.work)
        raise
    else:
        if backup_work.exists():
            shutil.rmtree(backup_work)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _save_downloads_locally(
    workspace: ThreadWorkspace,
    files: list[tuple[str, Path]],
    responses: list[FileDownloadResponse],
    timestamp: datetime,
) -> Path:
    output_path = _next_output_path(workspace.output, timestamp)
    staging_root, staged_work, staged_output = _prepare_staging_tree(
        workspace,
        files,
        responses,
        output_path,
    )
    _commit_staging_tree(
        workspace,
        staging_root,
        staged_work,
        staged_output,
        output_path,
    )
    return output_path


def create_save_output_tool(
    backend: SandboxBackendProtocol,
    workspace: ThreadWorkspace,
    now: Callable[[], datetime] = datetime.now,
) -> BaseTool:
    @tool(
        "save_output",
        description=(
            "校验并保存当前 Sandbox 的 /workspace/work/。"
            "会下载全部工作文件，并将所有 PPTX 复制到本机时间戳输出目录。"
        ),
    )
    async def save_output() -> str:
        files = await _collect_remote_files(backend)
        if not files:
            raise ToolException("Remote work directory is empty.")

        pptx_paths = [
            remote_path
            for remote_path, relative_path in files
            if relative_path.suffix.lower() == ".pptx"
        ]
        if not pptx_paths:
            raise ToolException("Remote work directory does not contain a PPTX file.")

        await _reject_remote_symlinks(backend)
        await _validate_pptx_files(backend, pptx_paths)

        remote_paths = [remote_path for remote_path, _ in files]
        responses = await backend.adownload_files(remote_paths)
        _validate_downloads(remote_paths, responses)

        try:
            output_path = await asyncio.to_thread(
                _save_downloads_locally,
                workspace,
                files,
                responses,
                now(),
            )
        except Exception as exc:
            raise ToolException(f"Failed to save local output: {exc}") from exc

        saved_pptx = [
            str(output_path / relative_path)
            for _, relative_path in files
            if relative_path.suffix.lower() == ".pptx"
        ]
        return (
            f"Saved remote work to {workspace.work} and PPTX files to {output_path}: "
            + ", ".join(saved_pptx)
        )

    save_output.handle_tool_error = True
    return save_output
