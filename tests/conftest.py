from __future__ import annotations

import os

_TEST_ENV_DEFAULTS = {
    "DEEPSEEK_API_KEY": "test-deepseek-key",
    "DEEPSEEK_BASE_URL": "https://example.com/deepseek",
    "DEEPSEEK_MODEL": "test-deepseek-model",
    "DEEPSEEK_MODEL_PROVIDER": "deepseek",
    "LANGSMITH_API_KEY": "test-langsmith-key",
    "LANGSMITH_PROJECT": "test-project",
    "LANGSMITH_TRACING": "false",
    "MINIO_BUCKET": "test-bucket",
    "MINIO_ENDPOINT_URL": "https://test-minio.example.com",
    "MINIO_PUBLIC_BASE_URL": "https://test-minio.example.com/test-bucket",
    "MINIO_ACCESS_KEY": "test-access-key",
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


def pytest_configure() -> None:
    for key, value in _TEST_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)
