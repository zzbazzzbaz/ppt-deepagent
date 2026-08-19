from __future__ import annotations

import pytest
from langchain_core.messages import ToolMessage

from scripts.smoke_view import _assert_visual_report, _require_successful_tool_result


def test_accepts_report_that_identifies_image_features() -> None:
    _assert_visual_report("顶部是深蓝色横幅，下方左侧浅蓝、右侧珊瑚红，形成左右双栏。")


def test_accepts_parallel_layout_wording_from_real_model() -> None:
    _assert_visual_report("顶部深蓝横条，下层并列两个矩形，左侧淡蓝，右侧粉红。")


def test_rejects_generic_nonempty_report() -> None:
    with pytest.raises(RuntimeError, match="未识别测试图片的关键视觉特征"):
        _assert_visual_report("这是一张设计简洁的演示页面。")


def test_rejects_report_without_distinct_dark_blue_area() -> None:
    with pytest.raises(RuntimeError, match="深蓝/藏青/深色"):
        _assert_visual_report("页面使用浅蓝色、粉红色和左右并列布局。")


def test_extracts_text_from_successful_tool_message() -> None:
    result = ToolMessage(
        content="视觉报告",
        tool_call_id="view-smoke",
        status="success",
    )

    assert _require_successful_tool_result(result) == "视觉报告"


def test_preserves_error_from_failed_tool_message() -> None:
    result = ToolMessage(
        content="视觉模型调用失败：timeout",
        tool_call_id="view-smoke",
        status="error",
    )

    with pytest.raises(RuntimeError, match="视觉模型调用失败：timeout"):
        _require_successful_tool_result(result)
