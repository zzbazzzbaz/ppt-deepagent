from __future__ import annotations

import importlib
import os
from unittest.mock import Mock, patch

import pytest

_TEST_ENV = {
    "DEEPSEEK_API_KEY": "test-deepseek-key",
    "DEEPSEEK_BASE_URL": "https://example.com/deepseek",
    "DEEPSEEK_MODEL": "test-deepseek-model",
    "DEEPSEEK_MODEL_PROVIDER": "deepseek",
    "LANGSMITH_API_KEY": "test-langsmith-key",
    "LANGSMITH_PROJECT": "test-project",
    "LANGSMITH_TRACING": "false",
    "QWEN_API_KEY": "test-qwen-key",
    "QWEN_BASE_URL": "https://example.com/qwen",
    "QWEN_MODEL": "test-qwen-model",
    "QWEN_MODEL_PROVIDER": "openai",
    "SANDBOX_DELETE_AFTER_STOP_SECONDS": "60",
    "SANDBOX_IDLE_TTL_SECONDS": "60",
    "SANDBOX_NAME_PREFIX": "test-sandbox",
    "SANDBOX_SNAPSHOT_NAME": "test-snapshot",
}

THREAD_ID = "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"


@pytest.fixture(scope="module", autouse=True)
def _load_agent_module_with_test_env() -> None:
    with patch.dict(os.environ, _TEST_ENV, clear=False):
        importlib.import_module("agent.agent")


async def test_graph_passes_backend_and_thread_id_to_output_tool() -> None:
    agent_module = importlib.import_module("agent.agent")
    backend = Mock()
    view_tool = Mock()
    view_tool.name = "view"
    save_output_tool = Mock()
    save_output_tool.name = "save_output"
    with (
        patch.object(
            agent_module,
            "get_thread_sandbox_backend",
            return_value=backend,
        ),
        patch.object(agent_module, "create_view_tool", return_value=view_tool),
        patch.object(
            agent_module,
            "create_save_output_tool",
            return_value=save_output_tool,
        ) as create_save_output_tool,
        patch.object(
            agent_module,
            "create_deep_agent",
            return_value=Mock(),
        ),
    ):
        await agent_module.graph({"configurable": {"thread_id": THREAD_ID}})

    create_save_output_tool.assert_called_once_with(backend, THREAD_ID)


async def test_graph_loads_skill_and_registers_output_tools() -> None:
    """Catches the generation runtime starting without the Skill or output publishing."""
    agent_module = importlib.import_module("agent.agent")
    backend = Mock()
    expected_graph = object()
    view_tool = Mock()
    view_tool.name = "view"
    save_output_tool = Mock()
    save_output_tool.name = "save_output"
    with (
        patch.object(
            agent_module,
            "get_thread_sandbox_backend",
            return_value=backend,
        ),
        patch.object(agent_module, "create_view_tool", return_value=view_tool),
        patch.object(
            agent_module, "create_save_output_tool", return_value=save_output_tool
        ),
        patch.object(
            agent_module,
            "create_deep_agent",
            return_value=expected_graph,
        ) as create_deep_agent,
    ):
        actual_graph = await agent_module.graph(
            {"configurable": {"thread_id": THREAD_ID}}
        )

    assert actual_graph is expected_graph
    assert create_deep_agent.call_args.kwargs["skills"] == ["/skills/"]
    tools = create_deep_agent.call_args.kwargs["tools"]
    assert [tool.name for tool in tools] == [
        "submit_outline",
        "view",
        "save_output",
    ]
