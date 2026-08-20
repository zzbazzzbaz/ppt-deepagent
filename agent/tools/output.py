from __future__ import annotations

import shlex
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import uuid4

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.tools import tool
from langchain_core.tools import BaseTool, ToolException

from agent.settings import minio_settings

_REMOTE_WORK = PurePosixPath("/workspace/work")
_REMOTE_OUTPUT = PurePosixPath("/workspace/output")


async def _collect_pptx_files(
    backend: SandboxBackendProtocol,
) -> list[PurePosixPath]:
    result = await backend.aglob("**/*", str(_REMOTE_WORK))
    if result.error is not None:
        raise ToolException(f"Failed to list remote work directory: {result.error}")
    if result.truncated:
        raise ToolException("Remote work directory listing was truncated.")

    pptx_files: list[PurePosixPath] = []
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

        if relative_path.suffix.lower() != ".pptx":
            continue
        folded_path = relative_path.as_posix().casefold()
        if folded_path in folded_paths:
            raise ToolException(f"Conflicting remote work path: {remote_path}")
        folded_paths.add(folded_path)
        pptx_files.append(relative_path)

    return sorted(pptx_files)


async def _reject_remote_symlinks(backend: SandboxBackendProtocol) -> None:
    result = await backend.aexecute("find /workspace/work -type l -print -quit")
    if result.exit_code != 0:
        raise ToolException(f"Failed to inspect remote work directory: {result.output}")
    if result.output.strip():
        raise ToolException(f"Remote work directory contains a symbolic link: {result.output}")


async def _run_remote_command(
    backend: SandboxBackendProtocol, command: str, label: str
) -> None:
    result = await backend.aexecute(command)
    if result.exit_code != 0:
        raise ToolException(f"{label} failed: {result.output}")


async def _validate_pptx_files(
    backend: SandboxBackendProtocol,
    pptx_paths: list[str],
) -> None:
    for remote_path in pptx_paths:
        await _run_remote_command(
            backend,
            "python /skills/pptx/scripts/office/validate.py " + shlex.quote(remote_path),
            f"PPTX validation for {remote_path}",
        )


def _output_id(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return f"{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"


def _public_url(
    public_base_url: str,
    thread_id: str,
    output_id: str,
    relative_path: PurePosixPath,
) -> str:
    object_key = "/".join(
        (
            "threads",
            thread_id,
            "output",
            output_id,
            *(quote(part) for part in relative_path.parts),
        )
    )
    return f"{public_base_url.rstrip('/')}/{object_key}"


async def _publish_pptx_files(
    backend: SandboxBackendProtocol,
    output_id: str,
    pptx_files: list[PurePosixPath],
) -> None:
    def destination_for(relative_path: PurePosixPath) -> str:
        return str(_REMOTE_OUTPUT / output_id / relative_path)

    try:
        for relative_path in pptx_files:
            source = str(_REMOTE_WORK.joinpath(relative_path))
            destination = destination_for(relative_path)
            await _run_remote_command(
                backend,
                "mkdir -p -- " + shlex.quote(str(PurePosixPath(destination).parent)),
                f"Creating output directory for {relative_path}",
            )
            await _run_remote_command(
                backend,
                "cp -- " + shlex.quote(source) + " " + shlex.quote(destination),
                f"Publishing {relative_path}",
            )
        for relative_path in pptx_files:
            source = str(_REMOTE_WORK.joinpath(relative_path))
            destination = destination_for(relative_path)
            await _run_remote_command(
                backend,
                "cmp -s -- "
                + shlex.quote(source)
                + " "
                + shlex.quote(destination)
                + " && test -s -- "
                + shlex.quote(destination),
                f"Verifying published {relative_path}",
            )
    except ToolException:
        await backend.aexecute(
            "rm -rf -- " + shlex.quote(str(_REMOTE_OUTPUT / output_id))
        )
        raise


def create_save_output_tool(
    backend: SandboxBackendProtocol,
    thread_id: str,
    *,
    now: Callable[[], datetime] = datetime.now,
    public_base_url: str | None = None,
) -> BaseTool:
    base_url = (
        public_base_url
        if public_base_url is not None
        else minio_settings.public_base_url
    )

    @tool(
        "save_output",
        description=(
            "校验 /workspace/work/ 中全部 PPTX，发布到 /workspace/output/<output-id>/，"
            "并返回每个文件的公网下载链接。"
        ),
    )
    async def save_output() -> str:
        pptx_files = await _collect_pptx_files(backend)
        if not pptx_files:
            raise ToolException("Remote work directory does not contain a PPTX file.")

        await _reject_remote_symlinks(backend)
        await _validate_pptx_files(
            backend, [str(_REMOTE_WORK.joinpath(path)) for path in pptx_files]
        )

        output_id = _output_id(now())
        await _publish_pptx_files(backend, output_id, pptx_files)

        urls = [
            _public_url(base_url, thread_id, output_id, relative_path)
            for relative_path in pptx_files
        ]
        lines = [f"已发布 {len(pptx_files)} 个 PPTX 到 /workspace/output/{output_id}/："]
        lines.extend(f"- {url}" for url in urls)
        return "\n".join(lines)

    save_output.handle_tool_error = True
    return save_output
