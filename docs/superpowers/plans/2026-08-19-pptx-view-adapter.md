# PPTX `view` 工具实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 实现一个只读 `view` 工具，从当前 LangSmith Sandbox 下载图片并调用远程 `qwen3.6-flash` 返回 Markdown 视觉报告，同时把该工具注册到现有 Deep Agent。

**架构：** `create_view_tool(backend, model)` 创建绑定当前 Thread backend 和 Qwen 模型的异步 LangChain 工具。工具只验证路径、下载图片、构造标准多模态消息并返回模型文本；DeepSeek 负责决定何时调用以及如何处理报告。PPTX Skill、Sandbox 镜像和 `/workspace` 挂载不在本计划内修改。

**技术栈：** Python 3.13、LangChain 1.3.15、Deep Agents 0.7.0、LangSmith Sandbox 0.11.0、Pydantic、标准库 `unittest`

## 全局约束

- `agent/skills/pptx/` 必须保持无 diff。
- 视觉模型固定使用 `agent/model.py` 已导出的远程 `qwen_model`。
- 工具名必须是 `view`，以兼容原 PPTX Skill。
- Qwen 只返回分析文本，不获得 backend、文件写入或命令执行能力。
- `view` 只接受当前 Sandbox 内的绝对 PNG、JPG、JPEG 或 WEBP 路径。
- 任一图片读取失败、Qwen 调用失败或模型返回空文本时，整次工具调用失败。
- 不添加联网图片搜索、图片生成、自部署、Sandbox 镜像或本地目录挂载逻辑。
- 不增加本地代码执行 backend 或 LangSmith Sandbox 失败时的本地回退。
- 使用 `uv` 运行 Python 命令，使用 `ruff` 做静态检查。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `agent/tools/view.py` | 定义输入 schema、创建绑定 backend/model 的 `view` 工具、下载图片并调用 Qwen |
| `tests/__init__.py` | 标记标准库 unittest 测试包 |
| `tests/test_view.py` | 覆盖输入校验、图片编码、下载错误、模型错误和空响应 |
| `agent/agent.py` | 使用当前 Thread backend 和 `qwen_model` 创建并注册 `view` |
| `scripts/smoke_view.py` | 使用固定生成的 PNG 调用真实远程 `qwen3.6-flash`，验证多模态链路 |

### Task 1：实现 `view` 工具及单元测试

**文件：**
- 创建：`tests/__init__.py`
- 创建：`tests/test_view.py`
- 创建：`agent/tools/view.py`

**接口：**
- 消费：`BackendProtocol.adownload_files(paths: list[str]) -> list[FileDownloadResponse]`
- 消费：`BaseChatModel.ainvoke([HumanMessage(...)]) -> AIMessage`
- 产出：`ViewInput(image_paths: list[str], prompt: str)`
- 产出：`create_view_tool(backend: BackendProtocol, model: BaseChatModel) -> BaseTool`
- 产出：名为 `view` 的异步工具，调用结果为非空 `str`

- [x] **Step 1：创建测试包并编写失败测试**

创建空文件 `tests/__init__.py`，并创建 `tests/test_view.py`：

```python
from __future__ import annotations

import base64
import unittest
from typing import cast
from unittest.mock import AsyncMock, Mock

from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException
from pydantic import ValidationError

from agent.tools.view import ViewInput, create_view_tool


class FakeBackend:
    def __init__(self, responses: list[FileDownloadResponse]) -> None:
        self.responses = responses
        self.requested_paths: list[str] | None = None

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self.requested_paths = paths
        return self.responses


def make_model(response: AIMessage | None = None) -> Mock:
    model = Mock(spec=BaseChatModel)
    model.ainvoke = AsyncMock(return_value=response or AIMessage(content="视觉报告"))
    return model


class ViewInputTests(unittest.TestCase):
    def test_rejects_relative_path(self) -> None:
        with self.assertRaises(ValidationError):
            ViewInput(image_paths=["slide-1.jpg"], prompt="检查页面")

    def test_rejects_unsupported_extension(self) -> None:
        with self.assertRaises(ValidationError):
            ViewInput(image_paths=["/workspace/slide-1.pdf"], prompt="检查页面")


class ViewToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_images_and_calls_model_with_multimodal_blocks(self) -> None:
        backend = FakeBackend(
            [
                FileDownloadResponse(path="/workspace/a.png", content=b"png"),
                FileDownloadResponse(path="/workspace/b.jpg", content=b"jpg"),
            ]
        )
        model = make_model()
        view = create_view_tool(
            cast(BackendProtocol, backend),
            cast(BaseChatModel, model),
        )

        result = await view.ainvoke(
            {
                "image_paths": ["/workspace/a.png", "/workspace/b.jpg"],
                "prompt": "检查布局",
            }
        )

        self.assertEqual(result, "视觉报告")
        self.assertEqual(
            backend.requested_paths,
            ["/workspace/a.png", "/workspace/b.jpg"],
        )
        messages = model.ainvoke.await_args.args[0]
        blocks = messages[0].content
        self.assertEqual(blocks[0], {"type": "text", "text": "检查布局"})
        self.assertEqual(
            blocks[1],
            {
                "type": "image",
                "base64": base64.b64encode(b"png").decode("ascii"),
                "mime_type": "image/png",
            },
        )
        self.assertEqual(blocks[2]["mime_type"], "image/jpeg")

    async def test_fails_when_any_download_fails(self) -> None:
        backend = FakeBackend(
            [
                FileDownloadResponse(path="/workspace/a.png", content=b"png"),
                FileDownloadResponse(
                    path="/workspace/missing.png",
                    error="file_not_found",
                ),
            ]
        )
        model = make_model()
        view = create_view_tool(
            cast(BackendProtocol, backend),
            cast(BaseChatModel, model),
        )

        with self.assertRaises(ToolException):
            await view.ainvoke(
                {
                    "image_paths": [
                        "/workspace/a.png",
                        "/workspace/missing.png",
                    ],
                    "prompt": "检查布局",
                }
            )

        model.ainvoke.assert_not_awaited()

    async def test_wraps_model_failure_as_tool_error(self) -> None:
        backend = FakeBackend(
            [FileDownloadResponse(path="/workspace/a.png", content=b"png")]
        )
        model = make_model()
        model.ainvoke.side_effect = TimeoutError("timeout")
        view = create_view_tool(
            cast(BackendProtocol, backend),
            cast(BaseChatModel, model),
        )

        with self.assertRaisesRegex(ToolException, "视觉模型调用失败"):
            await view.ainvoke(
                {
                    "image_paths": ["/workspace/a.png"],
                    "prompt": "检查布局",
                }
            )

    async def test_rejects_empty_model_response(self) -> None:
        backend = FakeBackend(
            [FileDownloadResponse(path="/workspace/a.png", content=b"png")]
        )
        model = make_model(AIMessage(content="  "))
        view = create_view_tool(
            cast(BackendProtocol, backend),
            cast(BaseChatModel, model),
        )

        with self.assertRaisesRegex(ToolException, "视觉模型返回了空结果"):
            await view.ainvoke(
                {
                    "image_paths": ["/workspace/a.png"],
                    "prompt": "检查布局",
                }
            )
```

- [x] **Step 2：运行测试并确认因模块不存在而失败**

运行：

```bash
uv run python -m unittest tests.test_view -v
```

预期：失败，错误包含 `No module named 'agent.tools.view'`。

- [x] **Step 3：实现最小 `view` 工具**

创建 `agent/tools/view.py`：

```python
from __future__ import annotations

import base64
from pathlib import PurePosixPath

from deepagents.backends.protocol import BackendProtocol
from langchain.tools import tool
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, field_validator

_IMAGE_MIME_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class ViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    image_paths: list[str] = Field(
        min_length=1,
        description="当前 Sandbox 中待查看图片的绝对路径列表。",
    )
    prompt: str = Field(
        min_length=1,
        description="视觉分析任务、演示背景和需要重点回答的问题。",
    )

    @field_validator("image_paths")
    @classmethod
    def validate_image_paths(cls, image_paths: list[str]) -> list[str]:
        for image_path in image_paths:
            path = PurePosixPath(image_path)
            if not path.is_absolute():
                raise ValueError(f"图片路径必须是绝对路径：{image_path}")
            if path.suffix.lower() not in _IMAGE_MIME_TYPES:
                raise ValueError(f"不支持的图片格式：{image_path}")
        return image_paths


def create_view_tool(
    backend: BackendProtocol,
    model: BaseChatModel,
) -> BaseTool:
    @tool(
        "view",
        args_schema=ViewInput,
        description="读取 Sandbox 中的图片并返回视觉设计或质量检查报告。",
    )
    async def view(image_paths: list[str], prompt: str) -> str:
        responses = await backend.adownload_files(image_paths)
        if len(responses) != len(image_paths):
            raise ToolException("图片下载结果数量与请求数量不一致。")

        failures = [
            f"{response.path}: {response.error or 'empty_content'}"
            for response in responses
            if response.error is not None or response.content is None
        ]
        if failures:
            raise ToolException("图片读取失败：" + "; ".join(failures))

        blocks: list[dict[str, str]] = [{"type": "text", "text": prompt}]
        for image_path, response in zip(image_paths, responses, strict=True):
            mime_type = _IMAGE_MIME_TYPES[PurePosixPath(image_path).suffix.lower()]
            blocks.append(
                {
                    "type": "image",
                    "base64": base64.b64encode(response.content).decode("ascii"),
                    "mime_type": mime_type,
                }
            )

        try:
            result = await model.ainvoke([HumanMessage(content=blocks)])
        except Exception as exc:
            raise ToolException(
                f"视觉模型调用失败：{type(exc).__name__}: {exc}"
            ) from exc

        text = result.text.strip()
        if not text:
            raise ToolException("视觉模型返回了空结果。")
        return text

    return view
```

- [x] **Step 4：运行单元测试并确认通过**

运行：

```bash
uv run python -m unittest tests.test_view -v
```

预期：全部测试显示 `ok`，最终输出 `OK`。

- [x] **Step 5：运行静态检查**

运行：

```bash
uv run ruff check agent/tools/view.py tests/test_view.py
```

预期：输出 `All checks passed!`。

- [x] **Step 6：获得用户提交授权，并纳入最终统一提交**

```bash
git add agent/tools/view.py tests/__init__.py tests/test_view.py
git commit -m "feat: 新增 PPTX 视觉查看工具"
```

### Task 2：把 `view` 注册到主 Agent

**文件：**
- 修改：`agent/agent.py:9-38`

**接口：**
- 消费：Task 1 的 `create_view_tool(backend, qwen_model) -> BaseTool`
- 消费：`agent.model.qwen_model`
- 产出：主 Agent 工具集合 `[submit_outline, view_tool]`

- [x] **Step 1：修改模型和工具导入**

将 `agent/agent.py` 的模型导入改为：

```python
from agent.model import deepseek_model, qwen_model
```

增加工具导入：

```python
from agent.tools.view import create_view_tool
```

- [x] **Step 2：为当前 Thread 创建并注册工具**

在取得 `backend` 后创建工具：

```python
    view_tool = create_view_tool(backend, qwen_model)
```

把 `create_deep_agent` 的工具列表改为：

```python
        tools=[submit_outline, view_tool],
```

本任务不得增加 `skills=["/skills/"]`。PPTX Skill 只有在后续 Sandbox 镜像包含 `/skills/pptx/` 后才能加载。

- [x] **Step 3：运行局部静态检查**

运行：

```bash
uv run ruff check agent/agent.py agent/tools/view.py
```

预期：输出 `All checks passed!`。

- [x] **Step 4：验证 LangGraph 配置和图导出**

运行：

```bash
uv run langgraph validate
```

预期：验证成功，不出现模型导入、工具 schema 或图工厂错误。

- [x] **Step 5：获得用户提交授权，并纳入最终统一提交**

```bash
git add agent/agent.py
git commit -m "feat: 注册 Qwen 视觉查看工具"
```

### Task 3：增加真实 Qwen 多模态 Smoke

**文件：**
- 创建：`scripts/smoke_view.py`

**接口：**
- 消费：Task 1 的 `create_view_tool(backend, qwen_model) -> BaseTool`
- 消费：`deepagents.backends.protocol.FileDownloadResponse`
- 消费：`agent.model.qwen_model`
- 产出：命令 `uv run python -m scripts.smoke_view`

- [x] **Step 1：创建不依赖 Pillow 的固定 PNG 生成器和 fake backend**

创建 `scripts/smoke_view.py`：

```python
from __future__ import annotations

import asyncio
import binascii
import struct
import zlib

from deepagents.backends.protocol import FileDownloadResponse

from agent.model import qwen_model
from agent.tools.view import create_view_tool

_IMAGE_PATH = "/workspace/smoke-slide.png"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", checksum)
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


async def main() -> None:
    view = create_view_tool(SmokeBackend(), qwen_model)  # type: ignore[arg-type]
    result = await view.ainvoke(
        {
            "image_paths": [_IMAGE_PATH],
            "prompt": "用中文简要描述这张演示页面的颜色和两栏布局。",
        }
    )
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError("qwen3.6-flash 未返回非空视觉报告")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
```

- [x] **Step 2：运行 smoke 静态检查**

运行：

```bash
uv run ruff check scripts/smoke_view.py
```

预期：输出 `All checks passed!`。如果 `type: ignore` 触发未启用的规则，不增加新的 Ruff ignore。

- [x] **Step 3：调用真实远程 Qwen API**

运行：

```bash
uv run python -m scripts.smoke_view
```

预期：输出一段非空中文文本，并提及深蓝、浅蓝/珊瑚色或左右两栏中的至少一种可见特征。该命令访问国内阿里云端点，不配置 Clash 代理。

- [x] **Step 4：运行完整本地验证**

运行：

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run langgraph validate
git diff --check
git diff -- agent/skills/pptx
```

预期：

- unittest 全部通过；
- Ruff 输出 `All checks passed!`；
- LangGraph 验证成功；
- `git diff --check` 无输出；
- `git diff -- agent/skills/pptx` 无输出。

- [x] **Step 5：获得用户提交授权，并纳入最终统一提交**

```bash
git add scripts/smoke_view.py
git commit -m "test: 增加 Qwen 视觉链路 smoke"
```

## 代码审查修正

- [x] 为 `view` 启用 `handle_tool_error`，使下载、模型和空响应错误以错误 `ToolMessage` 返回给 Agent，而不是终止整个 Run。
- [x] 捕获 backend 下载阶段的异常并转换为 `ToolException`。
- [x] 只用 `.strip()` 判断空响应，正常 Markdown 按模型原文返回。
- [x] 将 smoke 提示改为不泄露测试图片布局，并校验颜色与并列布局特征。
- [x] 使用 `langsmith.tracing_context()` 显式启用 tracing，通过 `client.runs.query()` 确认唯一 smoke run 已写入 LangSmith。
- [x] 增加 backend 异常、下载数量不一致、全部支持格式、Markdown 原样返回和真实模型措辞变体测试。

## 计划自查

- 规范覆盖：工具名、只读边界、绝对图片路径、四种格式、整批失败、Qwen 错误、空响应、纯文本返回、真实 smoke 和 Skill 无 diff 均有对应任务。
- 类型一致：所有任务统一使用 `create_view_tool(backend: BackendProtocol, model: BaseChatModel) -> BaseTool`。
- 范围一致：没有加载 Skill、构建镜像、实现挂载、增加图片来源或设计自部署。
- 提交约束：计划保留逐任务提交点，但只有获得用户明确授权后才执行提交命令。
