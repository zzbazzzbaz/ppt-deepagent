from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import cast

from blockbuster import blockbuster_ctx
from deepagents.backends.protocol import GlobResult, SandboxBackendProtocol
from langchain_core.messages import ToolMessage

from agent.tools import output as output_module
from agent.tools.output import create_save_output_tool

THREAD_ID = "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"
TIMESTAMP = "20260819T123456Z"


class FakeBackend:
    def __init__(
        self,
        matches: list[dict[str, object]],
        *,
        truncated: bool = False,
        downloads: dict[str, bytes] | None = None,
    ) -> None:
        self.matches = matches
        self.truncated = truncated
        self.downloads = downloads or {}
        self.commands: list[str] = []
        self.command_responses: list[SimpleNamespace] = []

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(error=None, matches=self.matches, truncated=self.truncated)

    async def aexecute(self, command: str) -> SimpleNamespace:
        self.commands.append(command)
        assert self.command_responses, f"Unexpected remote command: {command}"
        return self.command_responses.pop(0)

    async def adownload_files(self, paths: list[str]) -> list[SimpleNamespace]:
        responses = []
        for path in paths:
            content = self.downloads.get(path)
            if content is None:
                responses.append(
                    SimpleNamespace(path=path, content=None, error="file_not_found")
                )
            else:
                responses.append(
                    SimpleNamespace(path=path, content=content, error=None)
                )
        return responses


class FakeS3Client:
    def __init__(self, *, fail_on_key: str | None = None) -> None:
        self.fail_on_key = fail_on_key
        self.put_calls: list[dict[str, object]] = []
        self.deleted_keys: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        if self.fail_on_key is not None and Key == self.fail_on_key:
            raise RuntimeError(f"put_object failed for {Key}")
        self.put_calls.append({"Bucket": Bucket, "Key": Key, "Body": Body})

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.deleted_keys.append(Key)


def _ok() -> SimpleNamespace:
    return SimpleNamespace(output="", exit_code=0, truncated=False)


async def test_rejects_work_directory_without_pptx() -> None:
    """Catches publishing source files while silently dropping the presentation."""
    backend = FakeBackend(
        [{"path": "/workspace/work/source.js", "is_dir": False, "size": 6}]
    )
    s3_client = FakeS3Client()
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "PPTX" in result.text
    assert backend.commands == []
    assert s3_client.put_calls == []


async def test_rejects_truncated_work_listing() -> None:
    """Catches publishing from an incomplete work directory listing."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}],
        truncated=True,
    )
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=FakeS3Client()
    )

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
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=FakeS3Client()
    )

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "Conflicting" in result.text
    assert backend.commands == []


async def test_validation_failure_uploads_nothing() -> None:
    """Catches publishing a PPTX that failed deterministic validation."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [
        _ok(),
        SimpleNamespace(output="invalid", exit_code=1, truncated=False),
    ]
    s3_client = FakeS3Client()
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "PPTX validation" in result.text
    assert "invalid" in result.text
    assert s3_client.put_calls == []


async def test_rejects_work_directory_with_symlink() -> None:
    """Catches following a symbolic link out of the sandbox work directory."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [
        SimpleNamespace(output="/workspace/work/evil", exit_code=0, truncated=False),
    ]
    s3_client = FakeS3Client()
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "symbolic link" in result.text
    assert s3_client.put_calls == []


async def test_rejects_failed_pptx_download() -> None:
    """Catches uploading a PPTX whose sandbox download failed."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [_ok(), _ok()]
    s3_client = FakeS3Client()
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "读取 PPTX 文件失败" in result.text
    assert s3_client.put_calls == []


async def test_rejects_empty_pptx_content() -> None:
    """Catches publishing a zero-byte PPTX."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 0}]
    )
    backend.command_responses = [_ok(), _ok()]
    backend.downloads = {"/workspace/work/final.pptx": b""}
    s3_client = FakeS3Client()
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "为空" in result.text
    assert s3_client.put_calls == []


async def test_uploads_all_pptx_and_returns_public_urls() -> None:
    """Catches missing files in the upload or unshareable links."""
    backend = FakeBackend(
        [
            {"path": "source.js", "is_dir": False, "size": 6},
            {"path": "final/deck.pptx", "is_dir": False, "size": 10},
            {"path": "archive/old deck.pptx", "is_dir": False, "size": 12},
        ]
    )
    backend.command_responses = [_ok() for _ in range(3)]
    backend.downloads = {
        "/workspace/work/final/deck.pptx": b"deck-bytes",
        "/workspace/work/archive/old deck.pptx": b"old-deck-bytes",
    }
    s3_client = FakeS3Client()
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend),
        THREAD_ID,
        s3_client=s3_client,
        now=lambda: datetime(2026, 8, 19, 12, 34, 56),
    )

    with blockbuster_ctx(scanned_modules=[output_module]):
        result = await tool.ainvoke({})

    assert s3_client.put_calls == [
        {
            "Bucket": "test-bucket",
            "Key": f"threads/{THREAD_ID}/{TIMESTAMP}/archive/old deck.pptx",
            "Body": b"old-deck-bytes",
        },
        {
            "Bucket": "test-bucket",
            "Key": f"threads/{THREAD_ID}/{TIMESTAMP}/final/deck.pptx",
            "Body": b"deck-bytes",
        },
    ]
    url_base = (
        f"https://test-minio.example.com/test-bucket/threads/{THREAD_ID}/{TIMESTAMP}"
    )
    assert f"{url_base}/final/deck.pptx" in result
    assert f"{url_base}/archive/old%20deck.pptx" in result
    assert f"已上传 2 个 PPTX 到 threads/{THREAD_ID}/{TIMESTAMP}/：" in result


async def test_upload_failure_cleans_up_uploaded_objects() -> None:
    """Catches a broken upload leaving partial public objects behind."""
    backend = FakeBackend(
        [
            {"path": "a.pptx", "is_dir": False, "size": 1},
            {"path": "b.pptx", "is_dir": False, "size": 2},
        ]
    )
    backend.command_responses = [_ok() for _ in range(3)]
    backend.downloads = {
        "/workspace/work/a.pptx": b"a",
        "/workspace/work/b.pptx": b"b",
    }
    s3_client = FakeS3Client(fail_on_key=f"threads/{THREAD_ID}/{TIMESTAMP}/b.pptx")
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend),
        THREAD_ID,
        s3_client=s3_client,
        now=lambda: datetime(2026, 8, 19, 12, 34, 56),
    )

    result = await tool.ainvoke(
        {"type": "tool_call", "id": "save-1", "name": "save_output", "args": {}}
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "上传 PPTX 到 MinIO 失败" in result.text
    assert s3_client.deleted_keys == [f"threads/{THREAD_ID}/{TIMESTAMP}/a.pptx"]


async def test_uploads_explicit_public_base_url() -> None:
    """Catches URLs built from the configured public endpoint instead of S3 internals."""
    backend = FakeBackend(
        [{"path": "/workspace/work/final.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [_ok() for _ in range(2)]
    backend.downloads = {"/workspace/work/final.pptx": b"deck"}
    tool = create_save_output_tool(
        cast(SandboxBackendProtocol, backend),
        THREAD_ID,
        s3_client=FakeS3Client(),
        now=lambda: datetime(2026, 8, 19, 12, 34, 56),
        public_base_url="https://cdn.example.com/",
    )

    result = await tool.ainvoke({})

    assert (
        f"https://cdn.example.com/threads/{THREAD_ID}/{TIMESTAMP}/final.pptx" in result
    )
