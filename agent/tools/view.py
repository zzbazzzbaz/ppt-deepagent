"""view：把渲染出的幻灯片页面图片交给视觉模型，返回设计质量报告。"""

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
        description="待检查图片的绝对路径列表，通常是渲染出的每页幻灯片。",
    )
    prompt: str = Field(
        min_length=1,
        description=(
            "检查任务说明：演示背景与需要重点确认的问题，"
            "如版式、溢出、重叠、可读性、视觉层级与整体一致性。"
        ),
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
        description=(
            "把幻灯片页面图片交给视觉模型检查，返回版式、溢出、重叠、"
            "可读性、视觉层级与整稿一致性报告。"
        ),
    )
    async def view(image_paths: list[str], prompt: str) -> str:
        try:
            responses = await backend.adownload_files(image_paths)
        except Exception as exc:
            raise ToolException(f"图片读取失败：{type(exc).__name__}: {exc}") from exc
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
            assert response.content is not None
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

        text = result.text
        if not text.strip():
            raise ToolException("视觉模型返回了空结果。")
        return text

    view.handle_tool_error = True
    return view
