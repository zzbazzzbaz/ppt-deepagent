"""submit_outline：提交完整逐页大纲，触发人工审批中断。"""

from __future__ import annotations

from typing import Annotated

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SlideOutline(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    index: int = Field(
        ge=1,
        description="页面编号，从 1 开始连续递增。",
    )
    title: str = Field(
        min_length=1,
        description="本页短标题，概括该页要传达的核心信息。",
    )
    key_points: list[
        Annotated[str, Field(min_length=1, description="一条独立的页面要点。")]
    ] = Field(
        min_length=2,
        max_length=5,
        description="本页 2-5 条原子化要点：事实、观点或结论，每条只表达一个要点。",
    )
    markdown: str = Field(
        min_length=1,
        description=(
            "本页内容的 Markdown 草稿，可使用段落、列表、引用或表格，"
            "作为后续制作的素材；只写内容本身，不包含代码、格式指令或占位文字。"
        ),
    )


class OutlineSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(
        min_length=1,
        description="演示文稿标题。",
    )
    markdown: str = Field(
        min_length=1,
        description=(
            "整套大纲的 Markdown 摘要，概括总体叙事、页面顺序和每页要点，"
            "供人工快速审阅；只描述内容与结构。"
        ),
    )
    slides: list[SlideOutline] = Field(
        min_length=1,
        description="按播放顺序排列的逐页大纲，至少包含一页。",
    )

    @model_validator(mode="after")
    def require_sequential_slide_indices(self) -> OutlineSubmission:
        expected = list(range(1, len(self.slides) + 1))
        actual = [slide.index for slide in self.slides]
        if actual != expected:
            raise ValueError(f"页面编号必须从 1 开始连续递增，当前收到：{actual}")
        return self


@tool(
    args_schema=OutlineSubmission,
    description=(
        "提交完整的逐页大纲供人工审批。调用后流程会暂停，等待人工批准"
        "（approve）、修改（edit）或驳回（reject）；只有批准后才可开始生成演示文稿。"
    ),
)
def submit_outline(
    title: str,
    markdown: str,
    slides: list[SlideOutline],
) -> str:
    """返回审批通过信号，供系统提示词判断进入生成阶段。"""
    return "大纲已批准，可以开始生成演示文稿。"
