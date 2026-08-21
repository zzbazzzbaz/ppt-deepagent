from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeepSeekSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="DEEPSEEK_", extra="ignore"
    )

    model: str
    model_provider: Literal["deepseek", "openai"]
    base_url: str
    api_key: str


class QwenSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="QWEN_", extra="ignore"
    )

    model: str
    model_provider: Literal["openai"]
    base_url: str
    api_key: str


class LangsmithSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LANGSMITH_", extra="ignore"
    )

    api_key: str
    tracing: bool
    project: str


class MinioSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MINIO_", extra="ignore"
    )

    endpoint_url: str
    bucket: str
    region: str
    path_style: bool
    public_base_url: str
    access_key: str
    secret_key: str


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="SANDBOX_", extra="ignore"
    )

    name_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    snapshot_name: str = Field(min_length=1)
    mem_bytes: int = Field(ge=2 * 1024**3, le=2 * 1024**3)
    idle_ttl_seconds: int = Field(ge=0, multiple_of=60)
    delete_after_stop_seconds: int = Field(ge=0, multiple_of=60)


deepseek_settings = DeepSeekSettings()
qwen_settings = QwenSettings()
langsmith_settings = LangsmithSettings()
sandbox_settings = SandboxSettings()
minio_settings = MinioSettings()
