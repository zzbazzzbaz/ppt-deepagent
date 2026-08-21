from __future__ import annotations

import asyncio

from deepagents import create_deep_agent
from langchain.agents.middleware import InterruptOnConfig
from langchain_core.runnables import RunnableConfig

from agent.infra import (
    create_s3_client,
    deepseek_model,
    get_thread_sandbox_backend,
    qwen_model,
)
from agent.prompts.presentation_planner import PRESENTATION_PLANNER_SYSTEM_PROMPT
from agent.tools import (
    create_save_output_tool,
    create_sync_tool,
    create_view_tool,
    submit_outline,
)

_OUTLINE_INTERRUPT_CONFIG: InterruptOnConfig = {
    "allowed_decisions": ["approve", "edit", "reject"],
}


async def graph(config: RunnableConfig):
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if not thread_id:
        raise ValueError("Agent Server thread_id is required")

    backend = await asyncio.to_thread(
        get_thread_sandbox_backend,
        str(thread_id),
    )
    s3_client = await asyncio.to_thread(create_s3_client)
    view_tool = create_view_tool(backend, qwen_model)
    sync_tool = create_sync_tool(backend, str(thread_id), s3_client=s3_client)
    save_output_tool = create_save_output_tool(
        backend, str(thread_id), s3_client=s3_client
    )
    return create_deep_agent(
        model=deepseek_model,
        tools=[submit_outline, view_tool, sync_tool, save_output_tool],
        skills=["/skills/"],
        system_prompt=PRESENTATION_PLANNER_SYSTEM_PROMPT,
        backend=backend,
        interrupt_on={
            "submit_outline": _OUTLINE_INTERRUPT_CONFIG,
        },
        name="ppt_agent",
    )
