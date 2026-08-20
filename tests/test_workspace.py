from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from blockbuster import blockbuster_ctx

from agent import workspace as workspace_module

THREAD_ID = "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"


@pytest.fixture
def backend() -> SimpleNamespace:
    return SimpleNamespace(
        adelete=AsyncMock(),
        aexecute=AsyncMock(
            return_value=SimpleNamespace(output="", exit_code=0, truncated=False)
        ),
        aupload_files=AsyncMock(),
    )


def test_thread_workspace_creates_uuid_scoped_paths(tmp_path: Path) -> None:
    """Catches using the process cwd or an unvalidated thread path for files."""
    workspace_root = tmp_path / "workspace"
    with patch.object(workspace_module, "WORKSPACE_ROOT", workspace_root):
        workspace = workspace_module.thread_workspace(THREAD_ID)

    assert workspace.root == workspace_root / THREAD_ID
    assert workspace.input == workspace_root / THREAD_ID / "input"
    assert workspace.work == workspace_root / THREAD_ID / "work"
    assert workspace.output == workspace_root / THREAD_ID / "output"


async def test_initialization_uploads_input_and_work_without_remote_delete(
    tmp_path: Path,
    backend: SimpleNamespace,
) -> None:
    """Catches losing resumable work or omitting either local synchronization root."""
    workspace_root = tmp_path / "workspace"
    with patch.object(workspace_module, "WORKSPACE_ROOT", workspace_root):
        workspace = workspace_module.thread_workspace(THREAD_ID)
    (workspace.input / "nested").mkdir(parents=True)
    workspace.work.mkdir(parents=True)
    (workspace.input / "nested" / "brief.txt").write_bytes(b"brief")
    (workspace.work / "source.js").write_bytes(b"source")
    backend.aupload_files.return_value = [
        SimpleNamespace(path="/workspace/input/nested/brief.txt", error=None),
        SimpleNamespace(path="/workspace/work/source.js", error=None),
    ]

    with blockbuster_ctx(scanned_modules=[workspace_module]):
        await workspace_module.initialize_thread_workspace(backend, workspace)

    uploads = dict(backend.aupload_files.await_args.args[0])
    assert uploads["/workspace/input/nested/brief.txt"] == b"brief"
    assert uploads["/workspace/work/source.js"] == b"source"
    backend.adelete.assert_not_awaited()
