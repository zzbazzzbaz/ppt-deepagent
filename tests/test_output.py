from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from deepagents.backends.protocol import (
    FileDownloadResponse,
    GlobResult,
    SandboxBackendProtocol,
)
from langchain_core.messages import ToolMessage

from agent.tools.output import create_save_output_tool
from agent.workspace import ThreadWorkspace


class FakeBackend:
    def __init__(self, matches: list[dict[str, object]]) -> None:
        self.matches = matches
        self.downloaded_paths: list[str] | None = None
        self.commands: list[str] = []
        self.download_responses: list[FileDownloadResponse] = []
        self.command_responses: list[SimpleNamespace] = []

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(error=None, matches=self.matches, truncated=False)

    async def aexecute(self, command: str) -> SimpleNamespace:
        self.commands.append(command)
        return self.command_responses.pop(0)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self.downloaded_paths = paths
        return self.download_responses


@pytest.fixture
def workspace(tmp_path: Path) -> ThreadWorkspace:
    root = tmp_path / "thread"
    ws = ThreadWorkspace(
        root=root,
        input=root / "input",
        work=root / "work",
        output=root / "output",
    )
    ws.work.mkdir(parents=True)
    return ws


async def test_rejects_work_directory_without_pptx(
    workspace: ThreadWorkspace,
) -> None:
    """Catches publishing source files while silently dropping the presentation."""
    backend = FakeBackend(
        [{"path": "/workspace/work/source.js", "is_dir": False, "size": 6}]
    )
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), workspace)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "PPTX" in result.text
    assert backend.downloaded_paths is None


async def test_validation_failure_keeps_existing_local_work(
    workspace: ThreadWorkspace,
) -> None:
    """Catches a failed remote validation replacing a previously saved workspace."""
    (workspace.work / "existing.txt").write_text("keep")
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [
        SimpleNamespace(output="", exit_code=0, truncated=False),
        SimpleNamespace(output="invalid", exit_code=1, truncated=False),
    ]
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), workspace)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-2", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "validation failed" in result.text
    assert (workspace.work / "existing.txt").read_text() == "keep"
    assert backend.downloaded_paths is None


async def test_saves_complete_work_tree_and_preserves_nested_pptx(
    workspace: ThreadWorkspace,
) -> None:
    """Catches saving only a deck while discarding source and nested presentation files."""
    backend = FakeBackend(
        [
            {"path": "/workspace/work/source.js", "is_dir": False, "size": 6},
            {
                "path": "/workspace/work/final/deck.pptx",
                "is_dir": False,
                "size": 10,
            },
            {
                "path": "/workspace/work/archive/deck.pptx",
                "is_dir": False,
                "size": 12,
            },
        ]
    )
    backend.command_responses = [
        SimpleNamespace(output="", exit_code=0, truncated=False),
        SimpleNamespace(output="", exit_code=0, truncated=False),
        SimpleNamespace(output="", exit_code=0, truncated=False),
    ]
    backend.download_responses = [
        FileDownloadResponse(
            path="/workspace/work/archive/deck.pptx", content=b"archive-pptx"
        ),
        FileDownloadResponse(
            path="/workspace/work/final/deck.pptx", content=b"final-pptx"
        ),
        FileDownloadResponse(path="/workspace/work/source.js", content=b"source"),
    ]
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend),
        workspace,
        now=lambda: datetime(2026, 8, 19, 12, 34, 56),
    )

    result = await tool.ainvoke({})

    assert "20260819-123456" in result
    assert (workspace.work / "source.js").read_bytes() == b"source"
    output = workspace.output / "20260819-123456"
    assert (output / "final/deck.pptx").read_bytes() == b"final-pptx"
    assert (output / "archive/deck.pptx").read_bytes() == b"archive-pptx"
