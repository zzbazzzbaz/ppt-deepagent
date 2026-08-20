from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import quote

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.tools import tool
from langchain_core.tools import BaseTool, ToolException

from agent.settings import minio_settings
from agent.storage import S3Client, create_s3_client, delete_object, put_object_body

_REMOTE_WORK = PurePosixPath("/workspace/work")


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


def _timestamp(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _object_key(
    thread_id: str,
    timestamp: str,
    relative_path: PurePosixPath,
) -> str:
    return "/".join(("threads", thread_id, timestamp, *relative_path.parts))


def _public_url(
    public_base_url: str,
    thread_id: str,
    timestamp: str,
    relative_path: PurePosixPath,
) -> str:
    quoted_key = "/".join(
        ("threads", thread_id, timestamp, *(quote(part) for part in relative_path.parts))
    )
    return f"{public_base_url.rstrip('/')}/{quoted_key}"


async def _download_work_files(
    backend: SandboxBackendProtocol,
    pptx_files: list[PurePosixPath],
) -> dict[PurePosixPath, bytes]:
    remote_paths = [str(_REMOTE_WORK.joinpath(path)) for path in pptx_files]
    responses = await backend.adownload_files(remote_paths)
    failures = [
        f"{response.path}: {response.error or 'empty_content'}"
        for response in responses
        if response.error is not None or response.content is None
    ]
    if failures:
        raise ToolException("读取 PPTX 文件失败：" + "; ".join(failures))

    contents: dict[PurePosixPath, bytes] = {}
    for relative_path, response in zip(pptx_files, responses, strict=True):
        assert response.content is not None
        if not response.content:
            raise ToolException(f"PPTX 文件为空：{relative_path.as_posix()}")
        contents[relative_path] = response.content
    return contents


async def _upload_pptx_files(
    client: S3Client,
    bucket: str,
    thread_id: str,
    timestamp: str,
    contents: dict[PurePosixPath, bytes],
) -> None:
    uploaded_keys: list[str] = []
    try:
        for relative_path, content in sorted(contents.items()):
            key = _object_key(thread_id, timestamp, relative_path)
            await asyncio.to_thread(put_object_body, client, bucket, key, content)
            uploaded_keys.append(key)
    except Exception as exc:
        for key in uploaded_keys:
            try:
                await asyncio.to_thread(delete_object, client, bucket, key)
            except Exception:
                pass
        raise ToolException(
            f"上传 PPTX 到 MinIO 失败：{type(exc).__name__}: {exc}"
        ) from exc


def create_save_output_tool(
    backend: SandboxBackendProtocol,
    thread_id: str,
    *,
    s3_client: S3Client | None = None,
    bucket: str | None = None,
    now: Callable[[], datetime] = datetime.now,
    public_base_url: str | None = None,
) -> BaseTool:
    client = s3_client if s3_client is not None else create_s3_client()
    resolved_bucket = bucket if bucket is not None else minio_settings.bucket
    base_url = (
        public_base_url
        if public_base_url is not None
        else minio_settings.public_base_url
    )

    @tool(
        "save_output",
        description=(
            "校验 /workspace/work/ 中全部 PPTX，上传到 MinIO threads/<thread_id>/<时间戳>/ 目录，"
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
        contents = await _download_work_files(backend, pptx_files)

        timestamp = _timestamp(now())
        await _upload_pptx_files(
            client, resolved_bucket, thread_id, timestamp, contents
        )

        urls = [
            _public_url(base_url, thread_id, timestamp, relative_path)
            for relative_path in sorted(contents)
        ]
        lines = [
            f"已上传 {len(urls)} 个 PPTX 到 threads/{thread_id}/{timestamp}/："
        ]
        lines.extend(f"- {url}" for url in urls)
        return "\n".join(lines)

    save_output.handle_tool_error = True
    return save_output
