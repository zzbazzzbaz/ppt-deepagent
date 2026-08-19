from __future__ import annotations

import base64
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from agent.tools.view import ViewInput, create_view_tool


class FakeBackend:
    def __init__(self, responses: list[FileDownloadResponse]) -> None:
        self.responses = responses
        self.requested_paths: list[str] | None = None

    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        self.requested_paths = paths
        return self.responses


class FailingBackend:
    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        raise RuntimeError("sandbox unavailable")


def make_model(response: AIMessage | None = None) -> Mock:
    mock = Mock(spec=BaseChatModel)
    mock.ainvoke = AsyncMock(return_value=response or AIMessage(content="视觉报告"))
    return mock


def test_rejects_empty_image_paths() -> None:
    with pytest.raises(ValidationError):
        ViewInput(image_paths=[], prompt="检查页面")


def test_rejects_relative_path() -> None:
    with pytest.raises(ValidationError):
        ViewInput(image_paths=["slide-1.jpg"], prompt="检查页面")


def test_rejects_unsupported_extension() -> None:
    with pytest.raises(ValidationError):
        ViewInput(image_paths=["/workspace/slide-1.pdf"], prompt="检查页面")


def test_accepts_all_supported_extensions_case_insensitively() -> None:
    value = ViewInput(
        image_paths=[
            "/workspace/a.PNG",
            "/workspace/b.jpg",
            "/workspace/c.JPEG",
            "/workspace/d.webp",
        ],
        prompt="检查页面",
    )

    assert len(value.image_paths) == 4


async def test_downloads_images_and_sends_multimodal_message() -> None:
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

    assert result == "视觉报告"
    assert backend.requested_paths == ["/workspace/a.png", "/workspace/b.jpg"]
    messages = model.ainvoke.await_args.args[0]
    blocks = messages[0].content
    assert blocks[0] == {"type": "text", "text": "检查布局"}
    assert blocks[1] == {
        "type": "image",
        "base64": base64.b64encode(b"png").decode("ascii"),
        "mime_type": "image/png",
    }
    assert blocks[2] == {
        "type": "image",
        "base64": base64.b64encode(b"jpg").decode("ascii"),
        "mime_type": "image/jpeg",
    }


async def test_returns_model_markdown_unchanged() -> None:
    backend = FakeBackend(
        [FileDownloadResponse(path="/workspace/a.png", content=b"png")]
    )
    model = make_model(AIMessage(content="\n# 视觉报告\n"))
    view = create_view_tool(
        cast(BackendProtocol, backend),
        cast(BaseChatModel, model),
    )

    result = await view.ainvoke(
        {
            "image_paths": ["/workspace/a.png"],
            "prompt": "检查布局",
        }
    )

    assert result == "\n# 视觉报告\n"


async def test_returns_error_tool_message_when_any_download_fails() -> None:
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

    result = await view.ainvoke(
        {
            "type": "tool_call",
            "id": "view-call-1",
            "name": "view",
            "args": {
                "image_paths": [
                    "/workspace/a.png",
                    "/workspace/missing.png",
                ],
                "prompt": "检查布局",
            },
        }
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "图片读取失败" in result.text
    model.ainvoke.assert_not_awaited()


async def test_returns_tool_error_when_backend_raises() -> None:
    model = make_model()
    view = create_view_tool(
        cast(BackendProtocol, FailingBackend()),
        cast(BaseChatModel, model),
    )

    result = await view.ainvoke(
        {
            "image_paths": ["/workspace/a.png"],
            "prompt": "检查布局",
        }
    )

    assert "图片读取失败" in result
    model.ainvoke.assert_not_awaited()


async def test_returns_tool_error_when_download_count_mismatches() -> None:
    backend = FakeBackend([])
    model = make_model()
    view = create_view_tool(
        cast(BackendProtocol, backend),
        cast(BaseChatModel, model),
    )

    result = await view.ainvoke(
        {
            "image_paths": ["/workspace/a.png"],
            "prompt": "检查布局",
        }
    )

    assert "图片下载结果数量与请求数量不一致" in result
    model.ainvoke.assert_not_awaited()


async def test_wraps_model_failure_as_tool_error() -> None:
    backend = FakeBackend(
        [FileDownloadResponse(path="/workspace/a.png", content=b"png")]
    )
    model = make_model()
    model.ainvoke.side_effect = TimeoutError("timeout")
    view = create_view_tool(
        cast(BackendProtocol, backend),
        cast(BaseChatModel, model),
    )

    result = await view.ainvoke(
        {
            "image_paths": ["/workspace/a.png"],
            "prompt": "检查布局",
        }
    )

    assert "视觉模型调用失败" in result


async def test_rejects_empty_model_response() -> None:
    backend = FakeBackend(
        [FileDownloadResponse(path="/workspace/a.png", content=b"png")]
    )
    model = make_model(AIMessage(content="  "))
    view = create_view_tool(
        cast(BackendProtocol, backend),
        cast(BaseChatModel, model),
    )

    result = await view.ainvoke(
        {
            "image_paths": ["/workspace/a.png"],
            "prompt": "检查布局",
        }
    )

    assert "视觉模型返回了空结果" in result
