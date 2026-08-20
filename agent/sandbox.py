from __future__ import annotations

from uuid import UUID

from deepagents.backends.langsmith import LangSmithSandbox
from langsmith.sandbox import (
    ResourceNotFoundError,
    Sandbox,
    SandboxClient,
    SandboxClientError,
)

from agent.settings import langsmith_settings, sandbox_settings
from agent.snapshot import find_ready_snapshot

_client = SandboxClient(api_key=langsmith_settings.api_key)


def sandbox_name_for_thread(thread_id: str) -> str:
    try:
        normalized_thread_id = str(UUID(thread_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Agent Server thread_id must be a UUID") from exc
    return f"{sandbox_settings.name_prefix}-{normalized_thread_id}"


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
