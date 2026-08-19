from __future__ import annotations

import importlib
import os
from unittest.mock import AsyncMock, Mock, patch

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


@pytest.fixture(scope="module", autouse=True)
def _load_agent_module_with_test_env() -> None:
    with patch.dict(os.environ, _TEST_ENV, clear=False):
        importlib.import_module("agent.agent")


async def test_graph_registers_outline_and_view_tools() -> None:
    agent_module = importlib.import_module("agent.agent")
    backend = Mock()
    workspace = Mock()
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
        patch.object(
            agent_module,
            "create_deep_agent",
            return_value=expected_graph,
        ) as create_deep_agent,
        patch.object(
            agent_module,
            "create_view_tool",
            return_value=view_tool,
        ) as create_view_tool,
        patch.object(agent_module, "thread_workspace", return_value=workspace),
        patch.object(
            agent_module,
            "initialize_thread_workspace",
            AsyncMock(),
        ),
        patch.object(
            agent_module,
            "create_save_output_tool",
            return_value=save_output_tool,
        ),
    ):
        actual_graph = await agent_module.graph(
            {"configurable": {"thread_id": "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"}}
        )

    assert actual_graph is expected_graph
    create_view_tool.assert_called_once_with(backend, agent_module.qwen_model)
    tools = create_deep_agent.call_args.kwargs["tools"]
    assert [tool.name for tool in tools] == [
        "submit_outline",
        "view",
        "save_output",
    ]


async def test_graph_syncs_workspace_loads_skill_and_registers_save_output() -> None:
    """Catches the generation runtime starting without files, Skill, or output saving."""
    agent_module = importlib.import_module("agent.agent")
    backend = Mock()
    workspace = Mock()
    expected_graph = object()
    view_tool = Mock()
    view_tool.name = "view"
    save_output_tool = Mock()
    save_output_tool.name = "save_output"
    initialize = AsyncMock()
    thread_id = "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"
    with (
        patch.object(
            agent_module,
            "get_thread_sandbox_backend",
            return_value=backend,
        ),
        patch.object(agent_module, "thread_workspace", return_value=workspace),
        patch.object(
            agent_module,
            "initialize_thread_workspace",
            initialize,
        ),
        patch.object(agent_module, "create_view_tool", return_value=view_tool),
        patch.object(
            agent_module,
            "create_save_output_tool",
            return_value=save_output_tool,
        ),
        patch.object(
            agent_module,
            "create_deep_agent",
            return_value=expected_graph,
        ) as create_deep_agent,
    ):
        actual_graph = await agent_module.graph(
            {"configurable": {"thread_id": thread_id}}
        )

    assert actual_graph is expected_graph
    initialize.assert_awaited_once_with(backend, workspace)
    assert create_deep_agent.call_args.kwargs["skills"] == ["/skills/"]
    tools = create_deep_agent.call_args.kwargs["tools"]
    assert [tool.name for tool in tools] == [
        "submit_outline",
        "view",
        "save_output",
    ]


async def test_graph_does_not_create_agent_when_workspace_upload_fails() -> None:
    """Catches an Agent starting after only a partial local-to-remote sync."""
    agent_module = importlib.import_module("agent.agent")
    backend = Mock()
    with (
        patch.object(
            agent_module,
            "get_thread_sandbox_backend",
            return_value=backend,
        ),
        patch.object(agent_module, "thread_workspace", return_value=Mock()),
        patch.object(
            agent_module,
            "initialize_thread_workspace",
            AsyncMock(side_effect=RuntimeError("upload failed")),
        ),
        patch.object(agent_module, "create_view_tool") as create_view_tool,
        patch.object(
            agent_module, "create_save_output_tool"
        ) as create_save_output_tool,
        patch.object(agent_module, "create_deep_agent") as create_deep_agent,
    ):
        with pytest.raises(RuntimeError, match="upload failed"):
            await agent_module.graph(
                {"configurable": {"thread_id": "4ef6e832-7c8d-4d15-9b28-0547bf2090b0"}}
            )

    create_view_tool.assert_not_called()
    create_save_output_tool.assert_not_called()
    create_deep_agent.assert_not_called()
