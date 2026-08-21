"""save_output：校验全部 PPTX 后发布，并返回每个文件的下载链接。"""

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

from agent.infra import (
    S3Client,
    create_s3_client,
    delete_object,
    put_object_body,
)
from agent.settings import minio_settings
from agent.tools._workspace import REMOTE_WORK, list_work_files, reject_work_symlinks


async def _validate_pptx_files(
    backend: SandboxBackendProtocol,
    pptx_paths: list[str],
) -> None:
    for remote_path in pptx_paths:
        result = await backend.aexecute(
            "python /skills/pptx/scripts/office/validate.py " + shlex.quote(remote_path)
        )
        if result.exit_code != 0:
            raise ToolException(f"PPTX 校验失败（{remote_path}）：{result.output}")


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
        (
            "threads",
            thread_id,
            timestamp,
            *(quote(part) for part in relative_path.parts),
        )
    )
    return f"{public_base_url.rstrip('/')}/{quoted_key}"


async def _download_work_files(
    backend: SandboxBackendProtocol,
    pptx_files: list[PurePosixPath],
) -> dict[PurePosixPath, bytes]:
    remote_paths = [str(REMOTE_WORK.joinpath(path)) for path in pptx_files]
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
        raise ToolException(f"发布 PPTX 失败：{type(exc).__name__}: {exc}") from exc


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
            "完成最终检查并发布演示文稿：校验工作目录中的全部 PPTX 文件，"
            "通过后发布，并返回每个文件的下载链接。"
        ),
    )
    async def save_output() -> str:
        pptx_files = await list_work_files(backend, only_suffixes={".pptx"})
        if not pptx_files:
            raise ToolException("工作目录中没有任何 PPTX 文件。")

        await reject_work_symlinks(backend)
        await _validate_pptx_files(
            backend, [str(REMOTE_WORK.joinpath(path)) for path in pptx_files]
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
        lines = [f"已发布 {len(urls)} 个 PPTX，下载链接如下："]
        lines.extend(f"- {url}" for url in urls)
        return "\n".join(lines)

    save_output.handle_tool_error = True
    return save_output
