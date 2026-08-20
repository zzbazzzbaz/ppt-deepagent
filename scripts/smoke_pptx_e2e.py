from __future__ import annotations

import asyncio
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import langsmith as ls
from langgraph_sdk import get_client
from langgraph_sdk.schema import Command
from langsmith.sandbox import ResourceNotFoundError, SandboxClient

AGENT_SERVER_URL = os.getenv("AGENT_SERVER_URL", "http://127.0.0.1:2024")
ASSISTANT_ID = "ppt_agent"
_REQUIRED_TEXT = "可编辑演示"

if TYPE_CHECKING:
    from agent.workspace import ThreadWorkspace


def _tool_calls(messages: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        for call in message.get("tool_calls", []) or []:
            if call.get("name") == name:
                calls.append(call)
    return calls


def _validate_interrupt(result: dict[str, Any]) -> None:
    interrupts = result.get("__interrupt__")
    if not isinstance(interrupts, list) or len(interrupts) != 1:
        raise RuntimeError(f"Expected one outline interrupt, got: {interrupts!r}")

    value = interrupts[0].get("value", {})
    action_requests = value.get("action_requests", [])
    if [action.get("name") for action in action_requests] != ["submit_outline"]:
        raise RuntimeError(f"Unexpected action requests: {action_requests!r}")
    review_configs = value.get("review_configs", [])
    if len(review_configs) != 1:
        raise RuntimeError(f"Unexpected review configs: {review_configs!r}")
    allowed = set(review_configs[0].get("allowed_decisions", []))
    if allowed != {"approve", "edit", "reject"}:
        raise RuntimeError(f"Unexpected approval decisions: {sorted(allowed)!r}")


def _validate_editable_pptx(path: Path, required_text: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            required_parts = {"[Content_Types].xml", "ppt/presentation.xml"}
            if not required_parts.issubset(names):
                raise RuntimeError(f"PPTX is missing core OOXML parts: {path}")
            slides = sorted(
                name
                for name in names
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            if len(slides) != 3:
                raise RuntimeError(f"Expected exactly three slides, got {len(slides)}: {path}")
            slide_xml = [archive.read(slide).decode("utf-8", errors="replace") for slide in slides]
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"PPTX is not a readable ZIP file: {path}") from exc

    combined = "\n".join(slide_xml)
    if required_text not in combined:
        raise RuntimeError(f"PPTX does not contain required editable text: {path}")
    if "<p:sp" not in combined and "<c:chart" not in combined:
        raise RuntimeError(f"PPTX is not editable: {path}")


def _latest_output(workspace: ThreadWorkspace) -> Path:
    candidates = [path for path in workspace.output.iterdir() if path.is_dir()]
    if not candidates:
        raise RuntimeError(f"No timestamped output directory found: {workspace.output}")
    return max(candidates, key=lambda path: path.name)


def _assert_successful_tool_message(messages: list[dict[str, Any]], name: str) -> None:
    matching = [message for message in messages if message.get("name") == name]
    if not matching:
        raise RuntimeError(f"No {name} ToolMessage found")
    if any(message.get("status") == "error" for message in matching):
        raise RuntimeError(f"{name} ToolMessage reported an error: {matching!r}")


def _metadata(run: Any) -> dict[str, Any]:
    extra = getattr(run, "extra", None) or {}
    metadata = extra.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


async def _wait_for_trace_runs(
    client: ls.Client,
    project_name: str,
    smoke_id: str,
    started_at: datetime,
) -> tuple[str, list[Any]]:
    project = await client.aread_project(project_name=project_name)
    for _ in range(120):
        roots: list[Any] = []
        async for run in client.runs.query(
            project_ids=[str(project.id)],
            is_root=True,
            min_start_time=started_at,
            page_size=100,
            selects=["TRACE_ID", "START_TIME", "EXTRA"],
        ):
            if _metadata(run).get("smoke_id") == smoke_id:
                roots.append(run)
        if roots:
            generation_root = max(roots, key=lambda run: run.start_time)
            trace_id = str(generation_root.trace_id)
            runs = [
                run
                async for run in client.runs.query(
                    project_ids=[str(project.id)],
                    trace_id=trace_id,
                    page_size=100,
                    selects=["NAME", "EXTRA"],
                )
            ]
            run_names = {str(getattr(run, "name", "")) for run in runs}
            if {"view", "save_output"}.issubset(run_names):
                return trace_id, runs
        await asyncio.sleep(1)
    raise RuntimeError(
        f"Completed LangSmith generation trace not found for smoke_id={smoke_id}"
    )


def _assert_trace_components(
    runs: list[Any], deepseek_model_name: str, qwen_model_name: str
) -> None:
    names = {str(getattr(run, "name", "")) for run in runs}
    if "view" not in names or "save_output" not in names:
        raise RuntimeError(f"Trace is missing view or save_output: {sorted(names)}")

    invocation_text = "\n".join(
        str((getattr(run, "extra", None) or {}).get("invocation_params", {}))
        for run in runs
    )
    if deepseek_model_name not in invocation_text:
        raise RuntimeError("Trace does not contain the configured DeepSeek model")
    if qwen_model_name not in invocation_text:
        raise RuntimeError("Trace does not contain the configured Qwen model")


def _prepare_local_workspace(workspace: ThreadWorkspace) -> None:
    (workspace.input / "nested").mkdir(parents=True, exist_ok=True)
    workspace.work.mkdir(parents=True, exist_ok=True)
    (workspace.input / "nested" / "brief.txt").write_text("固定输入素材")
    (workspace.work / "local-only.txt").write_text("本机工作目录标记")


async def _upload_remote_sentinel(backend: Any) -> None:
    responses = await backend.aupload_files(
        [("/workspace/work/remote-only.txt", "远程工作目录标记".encode())]
    )
    if len(responses) != 1 or responses[0].error is not None:
        error = responses[0].error if responses else "missing upload response"
        raise RuntimeError(f"Failed to upload remote sentinel: {error}")


async def _run_smoke() -> None:
    from agent.sandbox import get_thread_sandbox_backend, sandbox_name_for_thread
    from agent.settings import deepseek_settings, langsmith_settings, qwen_settings
    from agent.workspace import thread_workspace

    if not langsmith_settings.tracing:
        raise RuntimeError("LANGSMITH_TRACING must be true")

    agent_client = get_client(url=AGENT_SERVER_URL)
    trace_client = ls.Client(api_key=langsmith_settings.api_key)
    sandbox_client = SandboxClient(api_key=langsmith_settings.api_key)
    thread = await agent_client.threads.create()
    thread_id = thread["thread_id"]
    workspace = thread_workspace(thread_id)
    sandbox_name = sandbox_name_for_thread(thread_id)
    smoke_id = f"pptx-e2e-{uuid4()}"
    started_at = datetime.now(UTC)
    primary_error: Exception | None = None
    cleanup_errors: list[Exception] = []

    try:
        _prepare_local_workspace(workspace)
        backend = await asyncio.to_thread(get_thread_sandbox_backend, thread_id)
        await _upload_remote_sentinel(backend)
        interrupted = await agent_client.runs.wait(
            thread_id,
            ASSISTANT_ID,
            input={
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "请创建一份固定的三页中文演示文稿。主题是‘可编辑演示’，"
                            "每页必须包含这四个字，至少使用一个原生图表，不要使用整页图片。"
                            "需求已经完整，先提交大纲等待审批；获批后完成 PPTX 生成、"
                            "校验、150 DPI 渲染、视觉检查并调用 save_output。"
                        ),
                    }
                ]
            },
            metadata={"smoke_id": smoke_id},
        )
        _validate_interrupt(interrupted)

        resumed = await agent_client.runs.wait(
            thread_id,
            ASSISTANT_ID,
            command=Command(resume={"decisions": [{"type": "approve"}]}),
            metadata={"smoke_id": smoke_id},
        )
        if resumed.get("__interrupt__"):
            raise RuntimeError(f"Run interrupted after approval: {resumed['__interrupt__']!r}")
        messages = resumed.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("Resumed run returned no messages")
        view_calls = _tool_calls(messages, "view")
        if not 1 <= len(view_calls) <= 3:
            raise RuntimeError(f"Expected 1-3 view calls after approval, got {len(view_calls)}")
        _assert_successful_tool_message(messages, "save_output")

        for required_name in ("local-only.txt", "remote-only.txt"):
            if not (workspace.work / required_name).is_file():
                raise RuntimeError(f"Saved work directory is missing {required_name}")
        if not list(workspace.work.rglob("*.js")):
            raise RuntimeError("Saved work directory is missing generation source")
        if not list(workspace.work.rglob("*.jpg")):
            raise RuntimeError("Saved work directory is missing rendered JPEG files")
        saved_pptx = sorted(workspace.work.rglob("*.pptx"))
        if not saved_pptx:
            raise RuntimeError("Saved work directory is missing PPTX files")
        output = _latest_output(workspace)
        output_pptx = sorted(output.rglob("*.pptx"))
        if not output_pptx:
            raise RuntimeError(f"Timestamped output is missing PPTX files: {output}")
        for path in output_pptx:
            _validate_editable_pptx(path, required_text=_REQUIRED_TEXT)

        trace_id, runs = await _wait_for_trace_runs(
            trace_client,
            langsmith_settings.project,
            smoke_id,
            started_at,
        )
        _assert_trace_components(runs, deepseek_settings.model, qwen_settings.model)
        print(f"PPTX E2E smoke passed for thread {thread_id}")
        print(f"Sandbox: {sandbox_name}")
        print(f"LangSmith trace: {trace_id}")
        print(f"Work directory: {workspace.work}")
        print(f"Output directory: {output}")
    except Exception as exc:
        primary_error = exc
    finally:
        try:
            await agent_client.threads.delete(thread_id)
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
        primary_error.add_note(
            f"thread_id={thread_id}; sandbox={sandbox_name}; smoke_id={smoke_id}; "
            f"workspace={workspace.root}"
        )
        raise primary_error
    if cleanup_errors:
        raise RuntimeError(
            "; ".join(f"Cleanup failed: {error}" for error in cleanup_errors)
        )


if __name__ == "__main__":
    asyncio.run(_run_smoke())
