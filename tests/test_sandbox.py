from __future__ import annotations

import importlib
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from langsmith.sandbox import ResourceNotFoundError

_TEST_ENV = {
    "DEEPSEEK_API_KEY": "test-deepseek-key",
    "DEEPSEEK_BASE_URL": "https://example.com/deepseek",
    "DEEPSEEK_MODEL": "test-deepseek-model",
    "DEEPSEEK_MODEL_PROVIDER": "deepseek",
    "LANGSMITH_API_KEY": "test-langsmith-key",
    "LANGSMITH_PROJECT": "test-project",
    "LANGSMITH_TRACING": "false",
    "MINIO_ACCESS_KEY": "test-access-key",
    "MINIO_BUCKET": "test-bucket",
    "MINIO_ENDPOINT_URL": "https://test-minio.example.com",
    "MINIO_PUBLIC_BASE_URL": "https://test-minio.example.com/test-bucket",
    "MINIO_SECRET_KEY": "test-secret-key",
    "QWEN_API_KEY": "test-qwen-key",
    "QWEN_BASE_URL": "https://example.com/qwen",
    "QWEN_MODEL": "test-qwen-model",
    "QWEN_MODEL_PROVIDER": "openai",
    "SANDBOX_DELETE_AFTER_STOP_SECONDS": "60",
    "SANDBOX_IDLE_TTL_SECONDS": "60",
    "SANDBOX_MEM_BYTES": str(2 * 1024**3),
    "SANDBOX_NAME_PREFIX": "test-sandbox",
    "SANDBOX_SNAPSHOT_NAME": "ppt-v1",
}

THREAD_ID = "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"


@pytest.fixture(scope="module", autouse=True)
def _load_sandbox_module_with_test_env() -> None:
    with patch.dict(os.environ, _TEST_ENV, clear=False):
        importlib.import_module("agent.sandbox")


@pytest.fixture
def client() -> Mock:
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(id="snapshot-id", name="ppt-v1", status="ready")
    ]
    return client


@pytest.fixture(autouse=True)
def sandbox_settings_stub() -> None:
    sandbox_module = importlib.import_module("agent.sandbox")
    settings_patch = patch.object(
        sandbox_module,
        "sandbox_settings",
        SimpleNamespace(
            name_prefix="test-sandbox",
            snapshot_name="ppt-v1",
            idle_ttl_seconds=60,
            delete_after_stop_seconds=60,
            mem_bytes=2 * 1024**3,
        ),
    )
    settings_patch.start()
    yield
    settings_patch.stop()


def test_creates_thread_sandbox_from_configured_snapshot(client: Mock) -> None:
    """Catches new Thread Sandboxes silently using LangSmith's default image."""
    sandbox_module = importlib.import_module("agent.sandbox")
    client.get_sandbox.side_effect = ResourceNotFoundError("missing")
    created = SimpleNamespace(
        name=sandbox_module.sandbox_name_for_thread(THREAD_ID),
        status="ready",
        snapshot_id="snapshot-id",
    )
    client.create_sandbox.return_value = created

    with patch.object(sandbox_module, "_client", client):
        actual = sandbox_module._get_or_create_thread_sandbox(THREAD_ID)

    assert actual is created
    kwargs = client.create_sandbox.call_args.kwargs
    assert kwargs["snapshot_id"] == "snapshot-id"
    assert kwargs["name"] == sandbox_module.sandbox_name_for_thread(THREAD_ID)
    assert kwargs["idle_ttl_seconds"] == 60
    assert kwargs["delete_after_stop_seconds"] == 60
    assert kwargs["mem_bytes"] == 2 * 1024**3


def test_creates_thread_sandbox_without_mount_config(client: Mock) -> None:
    """Catches a Sandbox still requesting S3 mounts that no longer exist."""
    sandbox_module = importlib.import_module("agent.sandbox")
    client.get_sandbox.side_effect = ResourceNotFoundError("missing")
    client.create_sandbox.return_value = SimpleNamespace(
        name=sandbox_module.sandbox_name_for_thread(THREAD_ID),
        status="ready",
        snapshot_id="snapshot-id",
    )

    with patch.object(sandbox_module, "_client", client):
        sandbox_module._get_or_create_thread_sandbox(THREAD_ID)

    kwargs = client.create_sandbox.call_args.kwargs
    assert "mount_config" not in kwargs


def test_rejects_existing_thread_sandbox_from_different_snapshot(
    client: Mock,
) -> None:
    """Catches reusing an older Sandbox that lacks the PPTX toolchain."""
    sandbox_module = importlib.import_module("agent.sandbox")
    client.get_sandbox.return_value = SimpleNamespace(
        name=sandbox_module.sandbox_name_for_thread(THREAD_ID),
        status="ready",
        snapshot_id="old-snapshot-id",
    )

    with patch.object(sandbox_module, "_client", client):
        with pytest.raises(RuntimeError, match="snapshot mismatch"):
            sandbox_module._get_or_create_thread_sandbox(THREAD_ID)

    client.create_sandbox.assert_not_called()
