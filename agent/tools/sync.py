from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Literal

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.tools import tool
from langchain_core.tools import BaseTool, ToolException
from pydantic import BaseModel, ConfigDict, Field

from agent.settings import minio_settings
from agent.storage import (
    S3Client,
    create_s3_client,
    get_object_body,
    list_object_keys,
    put_object_body,
)

_REMOTE_WORK = PurePosixPath("/workspace/work")
_REMOTE_INPUT = PurePosixPath("/workspace/input")


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    direction: Literal["download", "upload"] = Field(
        description=(
            "同步方向：download 把 MinIO threads/<thread_id>/input 同步到 Sandbox "
            "/workspace/input；upload 把 Sandbox /workspace/work 同步到 MinIO "
            "threads/<thread_id>/work。同名文件直接覆盖。"
        ),
    )


def _safe_relative_path(suffix: str) -> PurePosixPath:
    path = PurePosixPath(suffix)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ToolException(f"不安全的对象存储路径：{suffix}")
    return path


async def _collect_work_files(
    backend: SandboxBackendProtocol,
) -> list[PurePosixPath]:
    result = await backend.aglob("**/*", str(_REMOTE_WORK))
    if result.error is not None:
        raise ToolException(f"列出 Sandbox 工作目录失败：{result.error}")
    if result.truncated:
        raise ToolException("Sandbox 工作目录列表被截断。")

    files: list[PurePosixPath] = []
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
            raise ToolException(f"路径超出工作目录：{remote_path}") from exc
        if not relative_path.parts or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            raise ToolException(f"无效的工作目录路径：{remote_path}")
        files.append(relative_path)

    return sorted(files)


async def _reject_remote_symlinks(backend: SandboxBackendProtocol) -> None:
    result = await backend.aexecute("find /workspace/work -type l -print -quit")
    if result.exit_code != 0:
        raise ToolException(f"检查 Sandbox 工作目录失败：{result.output}")
    if result.output.strip():
        raise ToolException(f"Sandbox 工作目录包含符号链接：{result.output}")


async def _download_from_minio(
    backend: SandboxBackendProtocol,
    client: S3Client,
    bucket: str,
    prefix: str,
) -> list[PurePosixPath]:
    try:
        keys = await asyncio.to_thread(list_object_keys, client, bucket, prefix)
    except Exception as exc:
        raise ToolException(
            f"列出 MinIO 对象失败：{type(exc).__name__}: {exc}"
        ) from exc

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
                f"读取 MinIO 对象失败：{key} ({type(exc).__name__}: {exc})"
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
            raise ToolException("写入 Sandbox 失败：" + "; ".join(failures))
    return relative_paths


async def _upload_to_minio(
    backend: SandboxBackendProtocol,
    client: S3Client,
    bucket: str,
    prefix: str,
) -> list[PurePosixPath]:
    await _reject_remote_symlinks(backend)
    relative_paths = await _collect_work_files(backend)
    if not relative_paths:
        return []

    remote_paths = [str(_REMOTE_WORK / path) for path in relative_paths]
    responses = await backend.adownload_files(remote_paths)
    failures = [
        f"{response.path}: {response.error or 'empty_content'}"
        for response in responses
        if response.error is not None or response.content is None
    ]
    if failures:
        raise ToolException("读取 Sandbox 文件失败：" + "; ".join(failures))

    for relative_path, response in zip(relative_paths, responses, strict=True):
        assert response.content is not None
        key = prefix + relative_path.as_posix()
        try:
            await asyncio.to_thread(
                put_object_body, client, bucket, key, response.content
            )
        except Exception as exc:
            raise ToolException(
                f"上传到 MinIO 失败：{key} ({type(exc).__name__}: {exc})"
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
            "同步 MinIO 与 Sandbox 之间的文件，同名文件直接覆盖："
            "direction=download 时把 MinIO threads/<thread_id>/input/ 同步到 "
            "/workspace/input/；direction=upload 时把 /workspace/work/ 同步到 "
            "MinIO threads/<thread_id>/work/。"
        ),
    )
    async def sync(direction: Literal["download", "upload"]) -> str:
        if direction == "download":
            synced = await _download_from_minio(backend, client, resolved_bucket, input_prefix)
            source = f"MinIO {input_prefix}"
            target = f"{_REMOTE_INPUT}/"
        else:
            synced = await _upload_to_minio(backend, client, resolved_bucket, work_prefix)
            source = f"{_REMOTE_WORK}/"
            target = f"MinIO {work_prefix}"

        lines = [f"已同步 {len(synced)} 个文件（{source} -> {target}，同名覆盖）："]
        lines.extend(f"- {path.as_posix()}" for path in synced)
        return "\n".join(lines)

    sync.handle_tool_error = True
    return sync
