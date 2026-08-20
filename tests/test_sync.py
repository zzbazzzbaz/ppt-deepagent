from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from blockbuster import blockbuster_ctx
from deepagents.backends.protocol import GlobResult, SandboxBackendProtocol
from langchain_core.messages import ToolMessage

from agent.tools import sync as sync_module
from agent.tools.sync import create_sync_tool

THREAD_ID = "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"


class FakeBackend:
    def __init__(
        self,
        *,
        matches: list[dict[str, object]] | None = None,
        truncated: bool = False,
        downloads: dict[str, bytes] | None = None,
    ) -> None:
        self.matches = matches or []
        self.truncated = truncated
        self.downloads = downloads or {}
        self.commands: list[str] = []
        self.command_responses: list[SimpleNamespace] = []
        self.uploads: list[list[tuple[str, bytes]]] = []

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

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[SimpleNamespace]:
        self.uploads.append(files)
        return [
            SimpleNamespace(path=path, content=content, error=None)
            for path, content in files
        ]


class FakePaginator:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages

    def paginate(self, *, Bucket: str, Prefix: str):
        return iter(self.pages)


class FakeS3Client:
    def __init__(
        self,
        *,
        pages: list[dict[str, object]] | None = None,
        objects: dict[str, bytes] | None = None,
    ) -> None:
        self.pages = pages or []
        self.objects = objects or {}
        self.put_calls: list[dict[str, object]] = []

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return FakePaginator(self.pages)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        body = SimpleNamespace(read=lambda: self.objects[Key])
        return {"Body": body}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.put_calls.append({"Bucket": Bucket, "Key": Key, "Body": Body})


def _ok() -> SimpleNamespace:
    return SimpleNamespace(output="", exit_code=0, truncated=False)


async def _invoke(tool, direction: str) -> str:
    return await tool.ainvoke({"direction": direction})


async def _invoke_tool_call(tool, direction: str) -> ToolMessage:
    return await tool.ainvoke(
        {
            "type": "tool_call",
            "id": "sync-1",
            "name": "sync",
            "args": {"direction": direction},
        }
    )


async def test_download_syncs_minio_input_to_sandbox() -> None:
    """Catches input files missing from the sandbox before generation starts."""
    backend = FakeBackend()
    s3_client = FakeS3Client(
        pages=[
            {
                "Contents": [
                    {"Key": f"threads/{THREAD_ID}/input/brief.md"},
                    {"Key": f"threads/{THREAD_ID}/input/assets/logo.png"},
                    {"Key": f"threads/{THREAD_ID}/input/assets/"},
                ]
            }
        ],
        objects={
            f"threads/{THREAD_ID}/input/brief.md": b"brief",
            f"threads/{THREAD_ID}/input/assets/logo.png": b"logo",
        },
    )
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    with blockbuster_ctx(scanned_modules=[sync_module]):
        result = await _invoke(tool, "download")

    assert backend.uploads == [
        [
            ("/workspace/input/assets/logo.png", b"logo"),
            ("/workspace/input/brief.md", b"brief"),
        ]
    ]
    assert "已同步 2 个文件" in result
    assert f"MinIO threads/{THREAD_ID}/input/ -> /workspace/input/" in result
    assert "- brief.md" in result
    assert "- assets/logo.png" in result


async def test_download_with_empty_prefix_syncs_nothing() -> None:
    backend = FakeBackend()
    s3_client = FakeS3Client(pages=[{}])
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke(tool, "download")

    assert backend.uploads == []
    assert "已同步 0 个文件" in result


async def test_download_rejects_unsafe_object_key() -> None:
    """Catches an object key escaping the sandbox input directory."""
    backend = FakeBackend()
    s3_client = FakeS3Client(
        pages=[
            {"Contents": [{"Key": f"threads/{THREAD_ID}/input/../evil.md"}]}
        ],
        objects={f"threads/{THREAD_ID}/input/../evil.md": b"evil"},
    )
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke_tool_call(tool, "download")

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "不安全的对象存储路径" in result.text
    assert backend.uploads == []


async def test_download_reports_failed_sandbox_write() -> None:
    backend = FakeBackend()

    async def failing_upload(files):
        backend.uploads.append(files)
        return [
            SimpleNamespace(path=path, content=content, error="permission_denied")
            for path, content in files
        ]

    backend.aupload_files = failing_upload  # type: ignore[method-assign]
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": f"threads/{THREAD_ID}/input/brief.md"}]}],
        objects={f"threads/{THREAD_ID}/input/brief.md": b"brief"},
    )
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke_tool_call(tool, "download")

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "写入 Sandbox 失败" in result.text


async def test_upload_syncs_sandbox_work_to_minio() -> None:
    """Catches work files never reaching MinIO for cross-session resume."""
    backend = FakeBackend(
        matches=[
            {"path": "deck.pptx", "is_dir": False, "size": 10},
            {"path": "notes/topic.md", "is_dir": False, "size": 4},
            {"path": "notes", "is_dir": True, "size": 0},
        ],
        downloads={
            "/workspace/work/deck.pptx": b"deck",
            "/workspace/work/notes/topic.md": b"note",
        },
    )
    backend.command_responses = [_ok()]
    s3_client = FakeS3Client()
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    with blockbuster_ctx(scanned_modules=[sync_module]):
        result = await _invoke(tool, "upload")

    assert s3_client.put_calls == [
        {
            "Bucket": "test-bucket",
            "Key": f"threads/{THREAD_ID}/work/deck.pptx",
            "Body": b"deck",
        },
        {
            "Bucket": "test-bucket",
            "Key": f"threads/{THREAD_ID}/work/notes/topic.md",
            "Body": b"note",
        },
    ]
    assert "已同步 2 个文件" in result
    assert f"/workspace/work/ -> MinIO threads/{THREAD_ID}/work/" in result


async def test_upload_with_empty_work_directory_syncs_nothing() -> None:
    backend = FakeBackend(matches=[])
    backend.command_responses = [_ok()]
    s3_client = FakeS3Client()
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke(tool, "upload")

    assert s3_client.put_calls == []
    assert "已同步 0 个文件" in result


async def test_upload_rejects_work_directory_with_symlink() -> None:
    """Catches following a symbolic link out of the sandbox work directory."""
    backend = FakeBackend(
        matches=[{"path": "deck.pptx", "is_dir": False, "size": 10}]
    )
    backend.command_responses = [
        SimpleNamespace(output="/workspace/work/evil", exit_code=0, truncated=False)
    ]
    s3_client = FakeS3Client()
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke_tool_call(tool, "upload")

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "符号链接" in result.text
    assert s3_client.put_calls == []


async def test_upload_rejects_truncated_work_listing() -> None:
    backend = FakeBackend(
        matches=[{"path": "deck.pptx", "is_dir": False, "size": 10}],
        truncated=True,
    )
    backend.command_responses = [_ok()]
    s3_client = FakeS3Client()
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke_tool_call(tool, "upload")

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "截断" in result.text
    assert s3_client.put_calls == []


async def test_upload_rejects_failed_sandbox_download() -> None:
    backend = FakeBackend(
        matches=[{"path": "deck.pptx", "is_dir": False, "size": 10}],
        downloads={},
    )
    backend.command_responses = [_ok()]
    s3_client = FakeS3Client()
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke_tool_call(tool, "upload")

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "读取 Sandbox 文件失败" in result.text
    assert s3_client.put_calls == []


async def test_sync_uses_injected_bucket() -> None:
    backend = FakeBackend()
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": f"threads/{THREAD_ID}/input/brief.md"}]}],
        objects={f"threads/{THREAD_ID}/input/brief.md": b"brief"},
    )
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend),
        THREAD_ID,
        s3_client=s3_client,
        bucket="custom-bucket",
    )

    result = await _invoke(tool, "download")

    assert "已同步 1 个文件" in result


async def test_download_failure_reports_object_error() -> None:
    """Catches a missing object body silently producing an empty sandbox file."""
    backend = FakeBackend()
    s3_client = FakeS3Client(
        pages=[{"Contents": [{"Key": f"threads/{THREAD_ID}/input/brief.md"}]}],
        objects={},
    )
    tool = create_sync_tool(
        cast(SandboxBackendProtocol, backend), THREAD_ID, s3_client=s3_client
    )

    result = await _invoke_tool_call(tool, "download")

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert backend.uploads == []
