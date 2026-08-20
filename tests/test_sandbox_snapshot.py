from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from langsmith.sandbox import SandboxClientError

from scripts.sandbox_snapshot import (
    SnapshotSyncResult,
    _find_snapshot,
    _snapshot_api_key,
    build_snapshot,
    create_snapshot_from_image,
    prepare_build_context,
    sync_snapshot_from_image,
    verify_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_pinned_node_22_runtime() -> None:
    dockerfile = (PROJECT_ROOT / "sandbox" / "Dockerfile").read_text()

    assert (
        "FROM node:22-bookworm-slim@sha256:"
        "d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 "
        "AS node-runtime"
    ) in dockerfile
    assert "COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node" in dockerfile
    assert "COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules" in dockerfile
    assert "\n    nodejs \\" not in dockerfile
    assert "\n    npm \\" not in dockerfile
    assert "npm ci --omit=dev --ignore-scripts" in dockerfile
    assert "COPY agent/skills/pptx /skills/pptx" in dockerfile
    assert "ln -s /opt/pptx/node_modules /node_modules" in dockerfile


def test_package_and_lock_pin_node_22_compatible_sharp() -> None:
    package = json.loads((PROJECT_ROOT / "sandbox" / "package.json").read_text())
    lock = json.loads((PROJECT_ROOT / "sandbox" / "package-lock.json").read_text())

    assert package["dependencies"]["sharp"] == "0.35.3"
    assert lock["packages"][""]["dependencies"]["sharp"] == "0.35.3"
    assert lock["packages"]["node_modules/sharp"]["version"] == "0.35.3"


def test_copies_only_snapshot_assets_with_pptx_skill_without_secrets(
    tmp_path: Path,
) -> None:
    """Catches sending credentials, the whole repository, or secrets to LangSmith."""
    prepare_build_context(PROJECT_ROOT, tmp_path)

    assert {path.name for path in tmp_path.iterdir()} == {
        "Dockerfile",
        "package.json",
        "package-lock.json",
        "requirements.txt",
        "agent",
    }
    skill_dir = tmp_path / "agent" / "skills" / "pptx"
    assert skill_dir.is_dir()
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "scripts" / "office" / "validate.py").exists()
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "workspace").exists()
    assert not any(skill_dir.rglob("*__pycache__*"))


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
        == 2 * 1024**3
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

    client.create_sandbox.assert_called_once_with(
        snapshot_id="snapshot-id",
        name=client.create_sandbox.call_args.kwargs["name"],
        wait_for_ready=True,
        mem_bytes=2 * 1024**3,
    )
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
    assert client.create_snapshot.call_args.kwargs["fs_capacity_bytes"] == 2 * 1024**3


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


def test_find_snapshot_requires_at_most_one_exact_name() -> None:
    """Catches ambiguous latest lookups silently picking the wrong snapshot."""
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(id="one", name="latest", status="ready"),
        SimpleNamespace(id="two", name="latest", status="ready"),
    ]

    with pytest.raises(RuntimeError, match="Expected at most one"):
        _find_snapshot(client, "latest")


def test_snapshot_api_key_reads_only_langsmith_configuration() -> None:
    """Catches the CLI pulling unrelated model secrets into CI."""
    with patch.dict(os.environ, {"LANGSMITH_API_KEY": "ci-key"}, clear=True):
        assert _snapshot_api_key(_env_file=None) == "ci-key"


def test_sync_skips_when_latest_digest_matches() -> None:
    """Catches redundant candidate builds for unchanged images."""
    existing = SimpleNamespace(
        id="latest-id",
        name="ppt-deepagent-sandbox-latest",
        status="ready",
        image_digest="sha256:new",
        docker_image="ghcr.io/zzbazzzbaz/ppt-deepagent:old",
    )
    client = Mock()
    client.list_snapshots.return_value = [existing]

    result = sync_snapshot_from_image(
        client,
        latest_name=existing.name,
        candidate_name="candidate",
        docker_image="ghcr.io/zzbazzzbaz/ppt-deepagent:ppt-deepagent-sandbox-20260820-010203",
        image_digest="sha256:new",
    )

    assert result == SnapshotSyncResult(action="skipped", snapshot=existing)
    client.create_snapshot.assert_not_called()
    client.delete_snapshot.assert_not_called()


def test_sync_creates_latest_when_missing() -> None:
    """Catches the first pipeline run failing instead of bootstrapping latest."""
    image = "ghcr.io/zzbazzzbaz/ppt-deepagent:ppt-deepagent-sandbox-20260820-010203"
    client = Mock()
    client.list_snapshots.return_value = []
    client.create_snapshot.side_effect = [
        SimpleNamespace(
            id="candidate-id",
            name="ppt-deepagent-sandbox-candidate-20260820-010203",
            status="ready",
            image_digest="sha256:new",
            docker_image=image,
        ),
        SimpleNamespace(
            id="latest-id",
            name="ppt-deepagent-sandbox-latest",
            status="ready",
            image_digest="sha256:new",
            docker_image=image,
        ),
    ]

    with patch("scripts.sandbox_snapshot.verify_snapshot") as verify:
        manager = Mock()
        manager.attach_mock(verify, "verify")
        manager.attach_mock(client.create_snapshot, "create_snapshot")
        manager.attach_mock(client.delete_snapshot, "delete_snapshot")
        result = sync_snapshot_from_image(
            client,
            latest_name="ppt-deepagent-sandbox-latest",
            candidate_name="ppt-deepagent-sandbox-candidate-20260820-010203",
            docker_image=image,
            image_digest="sha256:new",
        )

    assert result.action == "created"
    assert result.snapshot.id == "latest-id"
    assert result.snapshot.docker_image == image
    latest_call = client.create_snapshot.call_args_list[-1]
    assert latest_call.args[0] == "ppt-deepagent-sandbox-latest"
    assert latest_call.kwargs["docker_image"] == image
    assert [call[0] for call in manager.mock_calls] == [
        "create_snapshot",
        "verify",
        "create_snapshot",
        "verify",
        "delete_snapshot",
    ]
    client.delete_snapshot.assert_called_once_with("candidate-id")


_LATEST_NAME = "ppt-deepagent-sandbox-latest"
_OLD_IMAGE = "ghcr.io/zzbazzzbaz/ppt-deepagent:ppt-deepagent-sandbox-20260819-010203"
_NEW_IMAGE = "ghcr.io/zzbazzzbaz/ppt-deepagent:ppt-deepagent-sandbox-20260820-010203"


def _old_latest_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        id="old-latest-id",
        name=_LATEST_NAME,
        status="ready",
        image_digest="sha256:old",
        docker_image=_OLD_IMAGE,
    )


def _make_stateful_sync_client(
    snapshots: list[SimpleNamespace],
    *,
    fail_creates: list[str] | None = None,
) -> tuple[Mock, list[str], list[SimpleNamespace]]:
    """Simulates LangSmith snapshot state to assert sync ordering and cleanup."""
    state = list(snapshots)
    events: list[str] = []
    pending_failures = list(fail_creates or [])
    client = Mock()

    def list_snapshots(name_contains: str = "") -> list[SimpleNamespace]:
        return [snapshot for snapshot in state if name_contains in snapshot.name]

    def create_snapshot(name: str, **kwargs: object) -> SimpleNamespace:
        if pending_failures and pending_failures[0] == name:
            pending_failures.pop(0)
            events.append(f"create-failed:{name}")
            raise SandboxClientError(f"create failed for {name}")
        created = SimpleNamespace(
            id=f"{name}-id",
            name=name,
            status="ready",
            image_digest="sha256:new",
            docker_image=kwargs["docker_image"],
        )
        state.append(created)
        events.append(f"create:{name}")
        return created

    def delete_snapshot(snapshot_id: str) -> None:
        state[:] = [snapshot for snapshot in state if snapshot.id != snapshot_id]
        events.append(f"delete:{snapshot_id}")

    client.list_snapshots.side_effect = list_snapshots
    client.create_snapshot.side_effect = create_snapshot
    client.delete_snapshot.side_effect = delete_snapshot
    return client, events, state


def test_sync_replaces_latest_after_candidate_verification() -> None:
    """Catches replacing a known-good latest before the candidate is validated."""
    client, events, state = _make_stateful_sync_client([_old_latest_snapshot()])

    def record_verify(client_arg: Mock, name: str) -> None:
        events.append(f"verify:{name}")

    with patch("scripts.sandbox_snapshot.verify_snapshot", side_effect=record_verify):
        result = sync_snapshot_from_image(
            client,
            latest_name=_LATEST_NAME,
            candidate_name="candidate",
            docker_image=_NEW_IMAGE,
            image_digest="sha256:new",
        )

    assert events == [
        "create:candidate",
        "verify:candidate",
        "delete:old-latest-id",
        f"create:{_LATEST_NAME}",
        f"verify:{_LATEST_NAME}",
        "delete:candidate-id",
    ]
    assert result.action == "updated"
    assert result.snapshot.id == f"{_LATEST_NAME}-id"
    assert [(snapshot.id, snapshot.docker_image) for snapshot in state] == [
        (f"{_LATEST_NAME}-id", _NEW_IMAGE)
    ]


def test_sync_candidate_failure_keeps_old_latest() -> None:
    """Catches candidate verification failures deleting the known-good latest."""
    client, events, state = _make_stateful_sync_client([_old_latest_snapshot()])

    def fail_candidate(client_arg: Mock, name: str) -> None:
        if name == "candidate":
            raise RuntimeError("candidate verification failed")

    with patch("scripts.sandbox_snapshot.verify_snapshot", side_effect=fail_candidate):
        with pytest.raises(RuntimeError, match="candidate verification failed"):
            sync_snapshot_from_image(
                client,
                latest_name=_LATEST_NAME,
                candidate_name="candidate",
                docker_image=_NEW_IMAGE,
                image_digest="sha256:new",
            )

    assert events == ["create:candidate", "delete:candidate-id"]
    assert [(snapshot.id, snapshot.docker_image) for snapshot in state] == [
        ("old-latest-id", _OLD_IMAGE)
    ]


def test_sync_restores_old_latest_when_release_fails() -> None:
    """Catches a broken release permanently removing the known-good latest."""
    client, events, state = _make_stateful_sync_client(
        [_old_latest_snapshot()], fail_creates=[_LATEST_NAME]
    )

    def record_verify(client_arg: Mock, name: str) -> None:
        events.append(f"verify:{name}")

    with patch("scripts.sandbox_snapshot.verify_snapshot", side_effect=record_verify):
        with pytest.raises(SandboxClientError, match="create failed"):
            sync_snapshot_from_image(
                client,
                latest_name=_LATEST_NAME,
                candidate_name="candidate",
                docker_image=_NEW_IMAGE,
                image_digest="sha256:new",
            )

    assert events == [
        "create:candidate",
        "verify:candidate",
        "delete:old-latest-id",
        f"create-failed:{_LATEST_NAME}",
        f"create:{_LATEST_NAME}",
        f"verify:{_LATEST_NAME}",
        "delete:candidate-id",
    ]
    assert [(snapshot.id, snapshot.docker_image) for snapshot in state] == [
        (f"{_LATEST_NAME}-id", _OLD_IMAGE)
    ]


def test_sync_deletes_unverified_latest_before_rollback() -> None:
    """Catches a created-but-unverified latest leaking alongside the rollback."""
    client, events, state = _make_stateful_sync_client([_old_latest_snapshot()])
    latest_verifies = 0

    def fail_first_latest_verify(client_arg: Mock, name: str) -> None:
        nonlocal latest_verifies
        if name == _LATEST_NAME:
            latest_verifies += 1
            if latest_verifies == 1:
                raise RuntimeError("latest verification failed")
        events.append(f"verify:{name}")

    with patch(
        "scripts.sandbox_snapshot.verify_snapshot", side_effect=fail_first_latest_verify
    ):
        with pytest.raises(RuntimeError, match="latest verification failed"):
            sync_snapshot_from_image(
                client,
                latest_name=_LATEST_NAME,
                candidate_name="candidate",
                docker_image=_NEW_IMAGE,
                image_digest="sha256:new",
            )

    assert events == [
        "create:candidate",
        "verify:candidate",
        "delete:old-latest-id",
        f"create:{_LATEST_NAME}",
        f"delete:{_LATEST_NAME}-id",
        f"create:{_LATEST_NAME}",
        f"verify:{_LATEST_NAME}",
        "delete:candidate-id",
    ]
    assert [(snapshot.id, snapshot.docker_image) for snapshot in state] == [
        (f"{_LATEST_NAME}-id", _OLD_IMAGE)
    ]


def test_sync_rollback_failure_retains_candidate() -> None:
    """Catches losing the only validated artifact when rollback also fails."""
    client, events, state = _make_stateful_sync_client(
        [_old_latest_snapshot()], fail_creates=[_LATEST_NAME, _LATEST_NAME]
    )

    def record_verify(client_arg: Mock, name: str) -> None:
        events.append(f"verify:{name}")

    with patch("scripts.sandbox_snapshot.verify_snapshot", side_effect=record_verify):
        with pytest.raises(
            RuntimeError,
            match=(
                "Snapshot release failed.*rollback failed.*"
                "candidate retained: candidate"
            ),
        ):
            sync_snapshot_from_image(
                client,
                latest_name=_LATEST_NAME,
                candidate_name="candidate",
                docker_image=_NEW_IMAGE,
                image_digest="sha256:new",
            )

    assert events == [
        "create:candidate",
        "verify:candidate",
        "delete:old-latest-id",
        f"create-failed:{_LATEST_NAME}",
        f"create-failed:{_LATEST_NAME}",
    ]
    assert [snapshot.id for snapshot in state] == ["candidate-id"]
