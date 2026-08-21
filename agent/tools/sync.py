"""sync：在工作环境与云端之间同步文件（用户素材 / 工作产物）。"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Literal

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.tools import tool
from langchain_core.tools import BaseTool, ToolException
from pydantic import BaseModel, ConfigDict, Field

from agent.infra import (
    S3Client,
    create_s3_client,
    get_object_body,
    list_object_keys,
    put_object_body,
)
from agent.settings import minio_settings
from agent.tools._workspace import REMOTE_WORK, list_work_files, reject_work_symlinks

_REMOTE_INPUT = PurePosixPath("/workspace/input")


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    direction: Literal["download", "upload"] = Field(
        description=(
            "同步方向：download 把用户上传的素材拉取到 /workspace/input/，"
            "在开始制作前调用；upload 把工作产物（生成脚本、渲染图、检查报告等）"
            "从 /workspace/work/ 回传到云端存档，在发布后调用。同名文件直接覆盖。"
        ),
    )


def _safe_relative_path(suffix: str) -> PurePosixPath:
    path = PurePosixPath(suffix)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ToolException(f"不安全的对象存储路径：{suffix}")
    return path


async def _download_from_minio(
    backend: SandboxBackendProtocol,
    client: S3Client,
    bucket: str,
    prefix: str,
) -> list[PurePosixPath]:
    try:
        keys = await asyncio.to_thread(list_object_keys, client, bucket, prefix)
    except Exception as exc:
        raise ToolException(f"列出云端对象失败：{type(exc).__name__}: {exc}") from exc

    files: list[tuple[str, bytes]] = []
    relative_paths: list[PurePosixPath] = []
    for key in keys:
        if key.endswith("/"):
            continue
        relative_path = _safe_relative_path(key[len(prefix) :])
        try:
            body = await asyncio.to_thread(get_object_body, client, bucket, key)
        except Exception as exc:
            raise ToolException(
                f"读取云端对象失败：{key} ({type(exc).__name__}: {exc})"
            ) from exc
        relative_paths.append(relative_path)
        files.append((str(_REMOTE_INPUT / relative_path), body))

    if files:
        responses = await backend.aupload_files(files)
        failures = [
            f"{response.path}: {response.error}"
            for response in responses
            if response.error is not None
        ]
        if failures:
            raise ToolException("写入工作环境失败：" + "; ".join(failures))
    return relative_paths


async def _upload_to_minio(
    backend: SandboxBackendProtocol,
    client: S3Client,
    bucket: str,
    prefix: str,
) -> list[PurePosixPath]:
    await reject_work_symlinks(backend)
    relative_paths = await list_work_files(backend)
    if not relative_paths:
        return []

    remote_paths = [str(REMOTE_WORK / path) for path in relative_paths]
    responses = await backend.adownload_files(remote_paths)
    failures = [
        f"{response.path}: {response.error or 'empty_content'}"
        for response in responses
        if response.error is not None or response.content is None
    ]
    if failures:
        raise ToolException("读取工作环境文件失败：" + "; ".join(failures))

    for relative_path, response in zip(relative_paths, responses, strict=True):
        assert response.content is not None
        key = prefix + relative_path.as_posix()
        try:
            await asyncio.to_thread(
                put_object_body, client, bucket, key, response.content
            )
        except Exception as exc:
            raise ToolException(
                f"上传到云端失败：{key} ({type(exc).__name__}: {exc})"
            ) from exc
    return relative_paths


def create_sync_tool(
    backend: SandboxBackendProtocol,
    thread_id: str,
    *,
    s3_client: S3Client | None = None,
    bucket: str | None = None,
) -> BaseTool:
    client = s3_client if s3_client is not None else create_s3_client()
    resolved_bucket = bucket if bucket is not None else minio_settings.bucket
    input_prefix = f"threads/{thread_id}/input/"
    work_prefix = f"threads/{thread_id}/work/"

    @tool(
        "sync",
        args_schema=SyncInput,
        description=(
            "在工作环境与云端之间同步文件，同名文件直接覆盖。"
            "开始时用 download 拉取用户素材；结束时用 upload 回传工作产物，"
            "便于后续会话继续。"
        ),
    )
    async def sync(direction: Literal["download", "upload"]) -> str:
        if direction == "download":
            synced = await _download_from_minio(
                backend, client, resolved_bucket, input_prefix
            )
            source = "用户素材"
            target = f"{_REMOTE_INPUT}/"
        else:
            synced = await _upload_to_minio(
                backend, client, resolved_bucket, work_prefix
            )
            source = "工作产物"
            target = "云端存档"

        lines = [f"已同步 {len(synced)} 个文件（{source} -> {target}，同名覆盖）："]
        lines.extend(f"- {path.as_posix()}" for path in synced)
        return "\n".join(lines)

    sync.handle_tool_error = True
    return sync
