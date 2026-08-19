from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.smoke_pptx_e2e import _tool_calls, _validate_editable_pptx


def write_pptx(path: Path, *, editable: bool) -> None:
    shape = (
        "<p:sp><p:txBody><a:p><a:r><a:t>可编辑演示</a:t></a:r></a:p></p:txBody></p:sp>"
        if editable
        else "<p:pic><p:nvPicPr/></p:pic>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
        for index in range(1, 4):
            archive.writestr(
                f"ppt/slides/slide{index}.xml",
                f"<p:sld>{shape}</p:sld>",
            )


def test_collects_view_tool_calls_from_agent_messages() -> None:
    """Catches E2E reporting a visual pass without inspecting actual tool calls."""
    messages = [
        {
            "tool_calls": [
                {"name": "view", "args": {}},
                {"name": "save_output", "args": {}},
            ]
        },
        {"tool_calls": [{"name": "view", "args": {}}]},
    ]

    calls = _tool_calls(messages, "view")

    assert len(calls) == 2


def test_rejects_flattened_three_slide_deck(tmp_path: Path) -> None:
    """Catches an E2E success that saved only full-slide raster images."""
    path = tmp_path / "deck.pptx"
    write_pptx(path, editable=False)

    with pytest.raises(RuntimeError, match="editable"):
        _validate_editable_pptx(path, required_text="可编辑演示")
