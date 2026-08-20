from __future__ import annotations

import re
import shlex
from datetime import datetime
from types import SimpleNamespace
from typing import cast

from blockbuster import blockbuster_ctx
from deepagents.backends.protocol import GlobResult, SandboxBackendProtocol
from langchain_core.messages import ToolMessage

from agent.tools import output as output_module
from agent.tools.output import create_save_output_tool

THREAD_ID = "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"


class FakeBackend:
    def __init__(
        self, matches: list[dict[str, object]], *, truncated: bool = False
    ) -> None:
        self.matches = matches
        self.truncated = truncated
        self.commands: list[str] = []
        self.command_responses: list[SimpleNamespace] = []

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(error=None, matches=self.matches, truncated=self.truncated)

    async def aexecute(self, command: str) -> SimpleNamespace:
        self.commands.append(command)
        assert self.command_responses, f"Unexpected remote command: {command}"
        return self.command_responses.pop(0)


def _ok() -> SimpleNamespace:
    return SimpleNamespace(output="", exit_code=0, truncated=False)


async def test_rejects_work_directory_without_pptx() -> None:
    """Catches publishing source files while silently dropping the presentation."""
    backend = FakeBackend(
        [{"path": "/workspace/work/source.js", "is_dir": False, "size": 6}]
    )
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), THREAD_ID)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "PPTX" in result.text
    assert backend.commands == []


async def test_rejects_truncated_work_listing() -> None:
    """Catches publishing from an incomplete work directory listing."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}],
        truncated=True,
    )
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), THREAD_ID)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "truncated" in result.text
    assert backend.commands == []


async def test_rejects_conflicting_case_folded_pptx_paths() -> None:
    """Catches ambiguous output keys differing only by letter case."""
    backend = FakeBackend(
        [
            {"path": "/workspace/work/Deck.pptx", "is_dir": False, "size": 1},
            {"path": "/workspace/work/deck.pptx", "is_dir": False, "size": 2},
        ]
    )
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), THREAD_ID)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "Conflicting" in result.text
    assert backend.commands == []


async def test_validation_failure_publishes_nothing() -> None:
    """Catches publishing a PPTX that failed deterministic validation."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [
        _ok(),
        SimpleNamespace(output="invalid", exit_code=1, truncated=False),
    ]
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), THREAD_ID)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "PPTX validation" in result.text
    assert "invalid" in result.text
    assert not any(" cp -- " in command for command in backend.commands)


async def test_rejects_work_directory_with_symlink() -> None:
    """Catches following a symbolic link out of the mounted work directory."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [
        SimpleNamespace(output="/workspace/work/evil", exit_code=0, truncated=False),
    ]
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), THREAD_ID)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "symbolic link" in result.text
    assert not any(" cp -- " in command for command in backend.commands)


async def test_publishes_all_pptx_and_returns_public_urls() -> None:
    """Catches missing files in the published output or unshareable links."""
    backend = FakeBackend(
        [
            {"path": "source.js", "is_dir": False, "size": 6},
            {"path": "final/deck.pptx", "is_dir": False, "size": 10},
            {"path": "archive/old deck.pptx", "is_dir": False, "size": 12},
        ]
    )
    backend.command_responses = [_ok() for _ in range(9)]
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend),
        THREAD_ID,
        now=lambda: datetime(2026, 8, 19, 12, 34, 56),
    )

    with blockbuster_ctx(scanned_modules=[output_module]):
        result = await tool.ainvoke({})

    match = re.search(r"/workspace/output/(20260819T123456Z-[0-9a-f]{6})", result)
    assert match is not None
    output_id = match.group(1)
    def cp(source: str, destination: str) -> str:
        return "cp -- " + shlex.quote(source) + " " + shlex.quote(destination)

    assert cp(
        "/workspace/work/final/deck.pptx",
        f"/workspace/output/{output_id}/final/deck.pptx",
    ) in backend.commands
    assert cp(
        "/workspace/work/archive/old deck.pptx",
        f"/workspace/output/{output_id}/archive/old deck.pptx",
    ) in backend.commands
    assert (
        "cmp -s -- "
        + shlex.quote("/workspace/work/final/deck.pptx")
        + " "
        + shlex.quote(f"/workspace/output/{output_id}/final/deck.pptx")
        + " && test -s -- "
        + shlex.quote(f"/workspace/output/{output_id}/final/deck.pptx")
    ) in backend.commands
    url_base = f"https://test-minio.example.com/test-bucket/threads/{THREAD_ID}/output/{output_id}"
    assert f"{url_base}/final/deck.pptx" in result
    assert f"{url_base}/archive/old%20deck.pptx" in result
    assert f"已发布 2 个 PPTX 到 /workspace/output/{output_id}/：" in result


async def test_publish_failure_cleans_up_output_directory() -> None:
    """Catches a broken publish leaving a partial output directory behind."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [
        _ok(),
        _ok(),
        _ok(),
        _ok(),
        SimpleNamespace(output="differs", exit_code=1, truncated=False),
        _ok(),
    ]
    tool = create_save_output_tool(cast(SandboxBackendProtocol, backend), THREAD_ID)

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "Verifying published" in result.text
    cleanup = [command for command in backend.commands if "rm -rf -- " in command]
    assert len(cleanup) == 1
    assert re.match(r"rm -rf -- /workspace/output/\S+$", cleanup[0]) is not None


async def test_publishes_explicit_public_base_url() -> None:
    """Catches URLs built from the configured public endpoint instead of S3 internals."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [_ok() for _ in range(5)]
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend),
        THREAD_ID,
        public_base_url="https://cdn.example.com/",
    )

    result = await tool.ainvoke({})

    assert "https://cdn.example.com/threads/" in result
