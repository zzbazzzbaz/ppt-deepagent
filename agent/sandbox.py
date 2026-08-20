from __future__ import annotations

from uuid import UUID

from deepagents.backends.langsmith import LangSmithSandbox
from langsmith.sandbox import (
    ResourceNotFoundError,
    Sandbox,
    SandboxClient,
    SandboxClientError,
    SandboxMountConfig,
    aws_auth,
    mount_config,
    s3_mount,
    workspace_secret,
)

from agent.settings import langsmith_settings, minio_settings, sandbox_settings
from agent.snapshot import find_ready_snapshot

_client = SandboxClient(api_key=langsmith_settings.api_key)


def _normalized_thread_id(thread_id: str) -> str:
    try:
        return str(UUID(thread_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Agent Server thread_id must be a UUID") from exc


def sandbox_name_for_thread(thread_id: str) -> str:
    return f"{sandbox_settings.name_prefix}-{_normalized_thread_id(thread_id)}"


def _thread_mount_config(thread_id: str) -> SandboxMountConfig:
    prefix = f"threads/{_normalized_thread_id(thread_id)}"
    return mount_config(
        auth=[
            aws_auth(
                access_key_id=workspace_secret(minio_settings.access_key_secret),
                secret_access_key=workspace_secret(minio_settings.secret_key_secret),
            )
        ],
        mounts=[
            s3_mount(
                id="skills",
                mount_path="/skills",
                bucket=minio_settings.bucket,
                prefix="skills",
                region=minio_settings.region,
                endpoint_url=minio_settings.endpoint_url,
                path_style=minio_settings.path_style,
                read_only=True,
            ),
            s3_mount(
                id="input",
                mount_path="/workspace/input",
                bucket=minio_settings.bucket,
                prefix=f"{prefix}/input",
                region=minio_settings.region,
                endpoint_url=minio_settings.endpoint_url,
                path_style=minio_settings.path_style,
                read_only=True,
            ),
            s3_mount(
                id="work",
                mount_path="/workspace/work",
                bucket=minio_settings.bucket,
                prefix=f"{prefix}/work",
                region=minio_settings.region,
                endpoint_url=minio_settings.endpoint_url,
                path_style=minio_settings.path_style,
                read_only=False,
            ),
            s3_mount(
                id="output",
                mount_path="/workspace/output",
                bucket=minio_settings.bucket,
                prefix=f"{prefix}/output",
                region=minio_settings.region,
                endpoint_url=minio_settings.endpoint_url,
                path_style=minio_settings.path_style,
                read_only=False,
            ),
        ],
    )


def get_thread_sandbox_backend(thread_id: str) -> LangSmithSandbox:
    return LangSmithSandbox(
        sandbox=_get_or_create_thread_sandbox(thread_id),
    )


def _get_or_create_thread_sandbox(thread_id: str) -> Sandbox:
    name = sandbox_name_for_thread(thread_id)
    snapshot = find_ready_snapshot(_client, sandbox_settings.snapshot_name)
    try:
        sandbox = _client.get_sandbox(name)
    except ResourceNotFoundError:
        try:
            sandbox = _client.create_sandbox(
                snapshot_id=snapshot.id,
                name=name,
                idle_ttl_seconds=sandbox_settings.idle_ttl_seconds,
                delete_after_stop_seconds=sandbox_settings.delete_after_stop_seconds,
                mem_bytes=sandbox_settings.mem_bytes,
                mount_config=_thread_mount_config(thread_id),
            )
        except SandboxClientError as creation_error:
            try:
                sandbox = _client.get_sandbox(name)
            except ResourceNotFoundError:
                raise creation_error
    if sandbox.snapshot_id != snapshot.id:
        raise RuntimeError(
            f"Sandbox '{sandbox.name}' snapshot mismatch: "
            f"expected '{snapshot.id}', got '{sandbox.snapshot_id}'"
        )
    return _ensure_ready(_client, sandbox)


def _ensure_ready(client: SandboxClient, sandbox: Sandbox) -> Sandbox:
    if sandbox.status == "ready":
        return sandbox
    if sandbox.status == "stopped":
        return client.start_sandbox(sandbox.name)
    if sandbox.status == "provisioning":
        return client.wait_for_sandbox(sandbox.name)
    raise RuntimeError(
        f"Sandbox '{sandbox.name}' is unavailable with status '{sandbox.status}'"
    )
