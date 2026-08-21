from __future__ import annotations

import asyncio
import binascii
import struct
import zlib
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import langsmith as ls
from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse
from langchain_core.messages import ToolMessage
from langchain_core.tracers.langchain import wait_for_all_tracers

from agent.infra import qwen_model
from agent.settings import langsmith_settings
from agent.tools.view import create_view_tool

_IMAGE_PATH = "/workspace/smoke-slide.png"
_VISUAL_FEATURES = (
    ("深蓝", "藏青", "深色"),
    ("浅蓝", "淡蓝"),
    ("珊瑚", "粉", "红"),
    ("左右", "双栏", "两栏", "并排", "并列"),
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)
    )


def _make_slide_png(width: int = 640, height: int = 360) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            if 48 <= x < 592 and 48 <= y < 112:
                color = (30, 39, 97)
            elif 48 <= x < 300 and 144 <= y < 312:
                color = (202, 220, 252)
            elif 332 <= x < 592 and 144 <= y < 312:
                color = (249, 97, 103)
            else:
                color = (255, 255, 255)
            rows.extend(color)

    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        signature
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _png_chunk(b"IEND", b"")
    )


class SmokeBackend:
    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        if paths != [_IMAGE_PATH]:
            return [
                FileDownloadResponse(path=path, error="file_not_found")
                for path in paths
            ]
        return [FileDownloadResponse(path=_IMAGE_PATH, content=_make_slide_png())]


def _assert_visual_report(report: str) -> None:
    missing = [
        "/".join(alternatives)
        for alternatives in _VISUAL_FEATURES
        if not any(keyword in report for keyword in alternatives)
    ]
    if missing:
        raise RuntimeError(
            "qwen3.6-flash 未识别测试图片的关键视觉特征：" + ", ".join(missing)
        )


def _require_successful_tool_result(result: object) -> str:
    if not isinstance(result, ToolMessage):
        raise RuntimeError("view smoke 未返回 ToolMessage")
    if result.status == "error":
        raise RuntimeError(f"view smoke 调用失败：{result.text}")
    if not result.text.strip():
        raise RuntimeError("qwen3.6-flash 未返回非空视觉报告")
    return result.text


async def _wait_for_trace(
    client: ls.Client,
    run_name: str,
    started_at: datetime,
) -> str:
    project = await client.aread_project(project_name=langsmith_settings.project)
    for _ in range(10):
        runs = client.runs.query(
            project_ids=[str(project.id)],
            filter=f'eq(name, "{run_name}")',
            min_start_time=started_at,
            page_size=10,
        )
        async for run in runs:
            return str(run.id)
        await asyncio.sleep(1)
    raise RuntimeError(f"LangSmith 中未找到 smoke trace：{run_name}")


async def main() -> None:
    if not langsmith_settings.tracing:
        raise RuntimeError("LANGSMITH_TRACING 必须为 true")

    backend = cast(BackendProtocol, SmokeBackend())
    view = create_view_tool(backend, qwen_model)
    client = ls.Client(api_key=langsmith_settings.api_key)
    run_name = f"pptx-view-smoke-{uuid4()}"
    started_at = datetime.now(UTC)

    try:
        with ls.tracing_context(
            client=client,
            project_name=langsmith_settings.project,
            enabled=True,
            tags=["smoke", "pptx-view"],
        ):
            tool_result = await view.ainvoke(
                {
                    "type": "tool_call",
                    "id": "view-smoke",
                    "name": "view",
                    "args": {
                        "image_paths": [_IMAGE_PATH],
                        "prompt": (
                            "用中文简要描述这张演示页面的主要颜色和版式结构。"
                            "只根据图片回答，不要推测图片外的信息。"
                        ),
                    },
                },
                {"run_name": run_name},
            )
    finally:
        wait_for_all_tracers()

    result = _require_successful_tool_result(tool_result)
    _assert_visual_report(result)
    trace_id = await _wait_for_trace(client, run_name, started_at)
    print(result)
    print(f"LangSmith trace: {trace_id}")


if __name__ == "__main__":
    asyncio.run(main())
