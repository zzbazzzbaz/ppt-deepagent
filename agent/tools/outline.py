from __future__ import annotations

from typing import Annotated

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SlideOutline(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    index: int = Field(
        ge=1,
        description="页面编号",
    )
    title: str = Field(
        min_length=1,
        description="当前页面的标题，应表达该页的核心信息。",
    )
    key_points: list[
        Annotated[str, Field(min_length=1, description="一条独立的页面要点。")]
    ] = Field(
        min_length=2,
        max_length=5,
        description="当前页面的 2-5 条原子化事实、观点或结论，每条只表达一个要点。",
    )
    markdown: str = Field(
        min_length=1,
        description=(
            "当前页面的 Markdown 内容草稿，可使用段落、列表、引用或表格；"
            "不要包含 HTML、PPTX XML、布局代码或纯演讲稿填充内容。"
        ),
    )


class OutlineSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(
        min_length=1,
        description="标题",
    )
    markdown: str = Field(
        min_length=1,
        description=(
            "整套大纲的人类可读 Markdown 视图，应概括总体叙事、页面顺序和每页摘要，"
            "不包含 PPTX 实现细节。"
        ),
    )
    slides: list[SlideOutline] = Field(
        min_length=1,
        description="按页面顺序排列的结构化大纲，至少包含一页。",
    )

    @model_validator(mode="after")
    def require_sequential_slide_indices(self) -> OutlineSubmission:
        expected = list(range(1, len(self.slides) + 1))
        actual = [slide.index for slide in self.slides]
        if actual != expected:
            raise ValueError(f"页面编号必须从 1 开始连续递增，当前收到：{actual}")
        return self


@tool(args_schema=OutlineSubmission, description="提交完整的逐页演示大纲，等待人工审批")
def submit_outline(
    title: str,
    markdown: str,
    slides: list[SlideOutline],
) -> str:
    return "大纲已批准，可以开始生成演示文稿。"
