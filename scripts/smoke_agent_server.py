from __future__ import annotations

import asyncio
import os
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.schema import Command
from langsmith.sandbox import ResourceNotFoundError, SandboxClient

from agent.infra import sandbox_name_for_thread
from agent.settings import langsmith_settings

AGENT_SERVER_URL = os.getenv("AGENT_SERVER_URL", "http://127.0.0.1:2024")
ASSISTANT_ID = "ppt_agent"


def _validate_interrupt(result: dict[str, Any]) -> None:
    interrupts = result.get("__interrupt__")
    if not isinstance(interrupts, list) or len(interrupts) != 1:
        raise RuntimeError(f"Expected one interrupt, got: {interrupts!r}")

    value = interrupts[0].get("value", {})
    action_requests = value.get("action_requests", [])
    if [action.get("name") for action in action_requests] != ["submit_outline"]:
        raise RuntimeError(f"Unexpected action requests: {action_requests!r}")

    review_configs = value.get("review_configs", [])
    if len(review_configs) != 1:
        raise RuntimeError(f"Unexpected review configs: {review_configs!r}")
    if review_configs[0].get("action_name") != "submit_outline":
        raise RuntimeError(f"Unexpected review config: {review_configs[0]!r}")
    allowed = set(review_configs[0].get("allowed_decisions", []))
    if allowed != {"approve", "edit", "reject"}:
        raise RuntimeError(f"Unexpected approval decisions: {sorted(allowed)!r}")


async def _run_smoke() -> None:
    client = get_client(url=AGENT_SERVER_URL)
    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    sandbox_name = sandbox_name_for_thread(thread_id)
    sandbox_client = SandboxClient(api_key=langsmith_settings.api_key)
    primary_error: Exception | None = None
    cleanup_errors: list[Exception] = []

    try:
        interrupted = await client.runs.wait(
            thread_id,
            ASSISTANT_ID,
            input={
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "请立即生成并提交一份关于 LangGraph Agent Server 的 3 页技术演示大纲，"
                            "面向有 Python 基础的工程师，使用简洁的技术风格。需求已经完整，"
                            "不要继续提问。请同时生成大纲级 Markdown、每页 Markdown 和结构化 key_points。"
                        ),
                    }
                ]
            },
        )
        _validate_interrupt(interrupted)

        sandbox = sandbox_client.get_sandbox(sandbox_name)
        if sandbox.name != sandbox_name or sandbox.status != "ready":
            raise RuntimeError(f"Unexpected Sandbox state: {sandbox!r}")

        resumed = await client.runs.wait(
            thread_id,
            ASSISTANT_ID,
            command=Command(resume={"decisions": [{"type": "approve"}]}),
        )
        if resumed.get("__interrupt__"):
            raise RuntimeError(f"Run interrupted again: {resumed['__interrupt__']!r}")
        if not resumed.get("messages"):
            raise RuntimeError("Resumed run returned no messages")
    except Exception as exc:
        primary_error = exc
    finally:
        try:
            await client.threads.delete(thread_id)
        except Exception as exc:
            cleanup_errors.append(exc)
        try:
            sandbox_client.delete_sandbox(sandbox_name)
        except ResourceNotFoundError:
            pass
        except Exception as exc:
            cleanup_errors.append(exc)
        finally:
            sandbox_client.close()

    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            primary_error.add_note(f"Cleanup also failed: {cleanup_error}")
        raise primary_error
    if cleanup_errors:
        raise RuntimeError(
            "; ".join(f"Cleanup failed: {error}" for error in cleanup_errors)
        )
    print(f"Smoke verification passed for thread {thread_id}")


if __name__ == "__main__":
    asyncio.run(_run_smoke())
