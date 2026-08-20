from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from scripts.smoke_pptx_e2e import (
    _tool_calls,
    _validate_editable_pptx,
    _wait_for_trace_runs,
)


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


async def test_wait_for_trace_runs_selects_post_approval_trace() -> None:
    smoke_id = "pptx-e2e-test"
    outline_root = SimpleNamespace(
        trace_id="outline-trace",
        start_time=datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
        extra={"metadata": {"smoke_id": smoke_id}},
    )
    generation_root = SimpleNamespace(
        trace_id="generation-trace",
        start_time=datetime(2026, 8, 20, 0, 1, tzinfo=UTC),
        extra={"metadata": {"smoke_id": smoke_id}},
    )
    generation_runs = [
        SimpleNamespace(name="view"),
        SimpleNamespace(name="save_output"),
    ]

    class FakeRuns:
        def __init__(self) -> None:
            self.queries: list[dict[str, object]] = []

        async def query(self, **kwargs):
            self.queries.append(kwargs)
            if kwargs.get("trace_id") == "generation-trace":
                for run in generation_runs:
                    yield run
                return
            if kwargs.get("trace_id") == "outline-trace":
                return
            for run in (outline_root, generation_root):
                yield run

    fake_runs = FakeRuns()
    client = SimpleNamespace(
        aread_project=AsyncMock(return_value=SimpleNamespace(id="project-id")),
        runs=fake_runs,
    )

    with patch("scripts.smoke_pptx_e2e.asyncio.sleep", new=AsyncMock()):
        trace_id, runs = await _wait_for_trace_runs(
            client,
            "ppt-deepagent",
            smoke_id,
            datetime(2026, 8, 19, tzinfo=UTC),
        )

    assert trace_id == "generation-trace"
    assert runs == generation_runs
    generation_query = next(
        query for query in fake_runs.queries if query.get("trace_id") == trace_id
    )
    assert generation_query["selects"] == ["NAME", "EXTRA"]


def test_rejects_flattened_three_slide_deck(tmp_path: Path) -> None:
    """Catches an E2E success that saved only full-slide raster images."""
    path = tmp_path / "deck.pptx"
    write_pptx(path, editable=False)

    with pytest.raises(RuntimeError, match="editable"):
        _validate_editable_pptx(path, required_text="可编辑演示")
