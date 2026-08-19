from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from scripts.sandbox_snapshot import (
    build_snapshot,
    create_snapshot_from_image,
    prepare_build_context,
    verify_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_copies_only_snapshot_assets_and_complete_pptx_skill(
    tmp_path: Path,
) -> None:
    """Catches accidentally sending credentials or the whole repository to LangSmith."""
    prepare_build_context(PROJECT_ROOT, tmp_path)

    assert {path.name for path in tmp_path.iterdir()} == {
        "Dockerfile",
        "package.json",
        "package-lock.json",
        "requirements.txt",
        "pptx",
    }
    expected = sorted(
        path.relative_to(PROJECT_ROOT / "agent" / "skills" / "pptx")
        for path in (PROJECT_ROOT / "agent" / "skills" / "pptx").rglob("*")
        if path.is_file()
    )
    actual = sorted(
        path.relative_to(tmp_path / "pptx")
        for path in (tmp_path / "pptx").rglob("*")
        if path.is_file()
    )
    assert actual == expected
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "workspace").exists()


def test_build_refuses_to_overwrite_existing_snapshot() -> None:
    """Catches a build command replacing a known-good snapshot by accident."""
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(name="ppt-v1", status="ready")
    ]

    with pytest.raises(RuntimeError, match="already exists"):
        build_snapshot(client, "ppt-v1", PROJECT_ROOT)

    client.create_snapshot_from_dockerfile.assert_not_called()


def test_build_deletes_failed_snapshot_with_same_name_before_rebuilding() -> None:
    """Catches a permanently blocked rebuild after a LangSmith-side snapshot failure."""
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(id="failed-id", name="ppt-v1", status="failed")
    ]
    client.create_snapshot_from_dockerfile.return_value = SimpleNamespace(
        id="new-id", name="ppt-v1", status="ready"
    )

    snapshot = build_snapshot(client, "ppt-v1", PROJECT_ROOT)

    client.delete_snapshot.assert_called_once_with("failed-id")
    assert snapshot.id == "new-id"


def test_build_retries_transient_upload_failure() -> None:
    """Catches one-off LangSmith dataplane errors aborting an otherwise valid build."""
    from langsmith.sandbox import SandboxClientError

    client = Mock()
    client.list_snapshots.return_value = []
    client.create_snapshot_from_dockerfile.side_effect = [
        SandboxClientError("file is required"),
        SimpleNamespace(id="snapshot-id", name="ppt-v1", status="ready"),
    ]

    with patch("scripts.sandbox_snapshot.time.sleep") as sleep:
        snapshot = build_snapshot(client, "ppt-v1", PROJECT_ROOT)

    sleep.assert_called()
    assert snapshot.id == "snapshot-id"
    assert client.create_snapshot_from_dockerfile.call_count == 2


def test_build_reserves_capacity_for_langsmith_builder_snapshot() -> None:
    """Catches a builder disk too small for BuildKit export of the heavy PPTX image."""
    client = Mock()
    client.list_snapshots.return_value = []
    client.create_snapshot_from_dockerfile.return_value = SimpleNamespace(
        id="snapshot-id", name="ppt-v2", status="ready"
    )

    build_snapshot(client, "ppt-v2", PROJECT_ROOT)

    assert (
        client.create_snapshot_from_dockerfile.call_args.kwargs["fs_capacity_bytes"]
        == 32 * 1024**3
    )


def test_verify_deletes_temporary_sandbox_when_dependency_check_fails() -> None:
    """Catches leaked LangSmith Sandboxes after a failed verification command."""
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(id="snapshot-id", name="ppt-v1", status="ready")
    ]
    client.create_sandbox.return_value = SimpleNamespace(
        name="ppt-v1-verify", status="ready", snapshot_id="snapshot-id"
    )
    failed_result = SimpleNamespace(
        output="missing dependency", exit_code=1, truncated=False
    )

    with patch("scripts.sandbox_snapshot.LangSmithSandbox") as backend_type:
        backend_type.return_value.upload_files.return_value = [
            SimpleNamespace(path="/workspace/work/verify.js", error=None)
        ]
        backend_type.return_value.execute.return_value = failed_result
        with pytest.raises(RuntimeError, match="missing dependency"):
            verify_snapshot(client, "ppt-v1")

    client.delete_sandbox.assert_called_once_with(
        client.create_sandbox.return_value.name
    )


def test_create_from_image_refuses_existing_snapshot() -> None:
    """Catches a from-image command replacing a known-good snapshot by accident."""
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(name="ppt-v1", status="ready")
    ]

    with pytest.raises(RuntimeError, match="already exists"):
        create_snapshot_from_image(
            client, "ppt-v1", "ghcr.io/zzbazzzbaz/ppt-deepagent:v1"
        )

    client.create_snapshot.assert_not_called()


def test_create_from_image_deletes_failed_snapshot_before_recreating() -> None:
    """Catches a permanently blocked recreate after a LangSmith-side snapshot failure."""
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(id="failed-id", name="ppt-v1", status="failed")
    ]
    client.create_snapshot.return_value = SimpleNamespace(
        id="new-id", name="ppt-v1", status="ready", image_digest="sha256:abc"
    )

    snapshot = create_snapshot_from_image(
        client, "ppt-v1", "ghcr.io/zzbazzzbaz/ppt-deepagent:v1"
    )

    client.delete_snapshot.assert_called_once_with("failed-id")
    assert snapshot.id == "new-id"
    assert (
        client.create_snapshot.call_args.kwargs["docker_image"]
        == "ghcr.io/zzbazzzbaz/ppt-deepagent:v1"
    )
    assert client.create_snapshot.call_args.kwargs["fs_capacity_bytes"] == 32 * 1024**3


def test_create_from_image_passes_registry_id_when_provided() -> None:
    """Catches a registry_id that is silently dropped for private GHCR images."""
    client = Mock()
    client.list_snapshots.return_value = []
    client.create_snapshot.return_value = SimpleNamespace(
        id="new-id", name="ppt-v1", status="ready", image_digest=None
    )

    create_snapshot_from_image(
        client,
        "ppt-v1",
        "ghcr.io/zzbazzzbaz/ppt-deepagent:v1",
        registry_id="registry-1",
    )

    assert client.create_snapshot.call_args.kwargs["registry_id"] == "registry-1"
