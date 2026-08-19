# 大纲工具契约实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：**完善大纲审批工具的结构化字段、中文 LLM 引导和 Markdown 输入，并通过真实 Agent Server HITL 流程验证。

**架构：**`submit_outline` 继续作为唯一的大纲审批工具，不承载完整需求状态。它接收标题、大纲 Markdown、按顺序排列的页面；每页包含标题、2-5 条结构化要点和页面 Markdown。Agent Server 继续负责原生 `approve/edit/reject` 中断与恢复。

**技术栈：**Python 3.13、Pydantic、LangChain tools、Deep Agents、LangGraph Agent Server、LangGraph SDK、DeepSeek、LangSmith Sandbox

## 全局约束

- 所有面向 LLM 的系统提示词、工具 docstring、字段描述、校验错误和工具返回文案使用中文。
- 删除 `audience` 和页面级 `objective`，不保留兼容字段。
- 增加必填的 Outline 级 `markdown` 和 Slide 级 `markdown`。
- 保留 `key_points`，每页必须是 2-5 条非空原子化要点。
- `key_points` 与 Markdown 必须保持语义一致，但本阶段不解析 Markdown 语法。
- 不新增 `topic`、`style`、`constraints`、`duration` 等重复需求状态字段。
- 不加载、修改或执行 `agent/skills/pptx`。
- 不采用 TDD、mock 或新增测试框架；使用直接 schema 检查、Ruff、Agent Server 校验和真实 smoke。
- 不提交 Git，除非用户另行明确要求。

---

## 文件清单

- 修改 `agent/tools/outline.py`：更新 Pydantic schema、字段 descriptions、校验规则、中文 docstring 和中文返回值。
- 修改 `agent/agent.py`：将系统提示词全部改为中文，并修复“一次调用”与“拒绝后重提”的矛盾。
- 修改 `scripts/smoke_agent_server.py`：将真实用户请求改为中文，并移除对旧字段的依赖。
- 保留 `docs/superpowers/specs/2026-08-19-outline-tool-contract-design.md`：作为本次契约的中文设计依据。

### 任务 1：重建大纲 Schema 和工具说明

**文件：**
- 修改：`agent/tools/outline.py`

**接口：**
- 产出：`SlideOutline(index, title, key_points, markdown)`。
- 产出：`OutlineSubmission(title, markdown, slides)`。
- 产出：工具名保持 `submit_outline`。

- [x] **步骤 1：定义页面级字段描述和 2-5 条要点边界**

将 `SlideOutline` 调整为：

```python
class SlideOutline(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    index: int = Field(
        ge=1,
        description="从 1 开始的连续页面编号，必须与页面顺序一致。",
    )
    title: str = Field(
        min_length=1,
        description="当前页面的短标题，应表达该页的核心信息。",
    )
    key_points: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=2,
        max_length=5,
        description="当前页面的 2-5 条原子化事实、观点或结论，每条只表达一个要点。",
    )
    markdown: str = Field(
        min_length=1,
        description=(
            "当前页面的 Markdown 内容草稿，可使用段落、列表、引用或表格；"
            "不要包含 HTML、PPTX XML、布局代码或纯演讲稿填充内容。"
        ),
    )
```

为了让每条嵌套要点也有 schema 语义，将 `Annotated` 改为：

```python
key_points: list[
    Annotated[str, Field(min_length=1, description="一条独立的页面要点。")]
] = Field(
    min_length=2,
    max_length=5,
    description="当前页面的 2-5 条原子化事实、观点或结论，每条只表达一个要点。",
)
```

- [x] **步骤 2：定义大纲级字段描述并删除旧字段**

将 `OutlineSubmission` 调整为：

```python
class OutlineSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(
        min_length=1,
        description="演示文稿标题，供人工审批和后续阶段使用。",
    )
    markdown: str = Field(
        min_length=1,
        description=(
            "整套大纲的人类可读 Markdown 视图，应概括总体叙事、页面顺序和每页摘要，"
            "不包含 PPTX 实现细节。"
        ),
    )
    slides: list[SlideOutline] = Field(
        min_length=1,
        description="按页面顺序排列的结构化大纲，至少包含一页。",
    )
```

删除 `audience`、页面级 `objective`，不新增别名或兼容输入。

- [x] **步骤 3：保留连续页码校验并改为中文错误**

保留 `require_sequential_slide_indices`，将错误信息改为：

```python
raise ValueError(f"页面编号必须从 1 开始连续递增，当前收到：{actual}")
```

- [x] **步骤 4：完善中文工具 docstring 和返回值**

使用以下职责边界：

```python
@tool("submit_outline", args_schema=OutlineSubmission)
def submit_outline(
    title: str,
    markdown: str,
    slides: list[SlideOutline],
) -> str:
    """提交完整的逐页演示大纲，等待人工审批。

    只有在需求已经完整、大纲已经自检并且所有字段都已准备好时才能调用。
    请保持大纲 Markdown、页面 Markdown 与结构化要点的一致性。
    调用后会触发 approve、edit 或 reject 审批；如果被拒绝，应根据人工反馈修改后重新提交。
    本工具不负责补充缺失需求、不生成 PPTX、不写入文件，也不代表整个演示文稿已经完成。
    """
    return "大纲已提交，结构校验通过，可进入下一阶段。"
```

### 任务 2：改造中文 Agent 引导和 Smoke 请求

**文件：**
- 修改：`agent/agent.py`
- 修改：`scripts/smoke_agent_server.py`

**接口：**
- 保持 `graph(config: RunnableConfig)` 的异步图工厂接口。
- 保持 `submit_outline` 的 `approve/edit/reject` 中断配置。
- Smoke 仍通过 `Command(resume={"decisions": [{"type": "approve"}]})` 恢复。

- [x] **步骤 1：替换为中文系统提示词**

系统提示词必须表达以下完整流程：

```text
你是一个演示文稿规划 Agent，负责收集需求并提交可供人工审批的逐页大纲。

在提问前先读取当前对话和其他结构化状态，只询问真正缺失的需求。需求完整后，生成连贯的逐页大纲。

大纲必须包含：演示文稿标题、大纲级 Markdown、按顺序排列的页面。每页必须包含页面编号、短标题、2-5 条原子化要点和页面级 Markdown。大纲 Markdown、页面 Markdown 与 key_points 必须保持一致。

调用 submit_outline 前检查页面编号是否从 1 开始连续递增、每页要点数量是否为 2-5 条、页面之间是否重复、叙事是否连贯，以及内容是否符合其他结构化状态。

首次完整草稿准备好后调用 submit_outline。调用会触发人工 approve、edit 或 reject 审批。被拒绝时根据人工反馈修改大纲并重新提交；批准或编辑后批准时，将其作为下一阶段的输入。

本阶段不生成 PPTX，不写入文件，也不要声称 PPTX 已经完成。
```

不得再使用“只调用一次”这类与拒绝重提流程矛盾的指令。

- [x] **步骤 2：将 smoke 用户请求改为中文**

请求应包含完整需求并要求立即提交，例如：

```text
请立即生成并提交一份关于 LangGraph Agent Server 的 3 页技术演示大纲，面向有 Python 基础的工程师，使用简洁的技术风格。需求已经完整，不要继续提问。请同时生成大纲级 Markdown、每页 Markdown 和结构化 key_points。
```

请求不传 `audience` 或 `objective` 工具参数；它只作为自然语言需求上下文。

- [x] **步骤 3：保持 smoke 协议断言稳定**

不按模型自然语言断言结果，只继续校验：

```python
assert action_requests[0]["name"] == "submit_outline"
assert set(review_configs[0]["allowed_decisions"]) == {
    "approve",
    "edit",
    "reject",
}
```

审批后继续确认同一 Thread 返回 `messages`，并删除临时 Thread 和 Sandbox。

### 任务 3：同步规范并执行轻量验证

**文件：**
- 参考：`docs/superpowers/specs/2026-08-19-outline-tool-contract-design.md`
- 验证：`agent/skills/pptx` 不得变化

- [x] **步骤 1：运行本地 schema 检查**

运行：

```bash
uv run python <<'PY'
from pydantic import ValidationError

from agent.tools.outline import OutlineSubmission, SlideOutline

schema = OutlineSubmission.model_json_schema()
slide_schema = SlideOutline.model_json_schema()
assert "audience" not in schema["properties"]
assert "objective" not in slide_schema["properties"]
assert schema["properties"]["markdown"]["description"]
assert slide_schema["properties"]["markdown"]["description"]

valid = {
    "title": "标题",
    "markdown": "# 大纲",
    "slides": [
        {
            "index": 1,
            "title": "第一页",
            "key_points": ["要点一", "要点二"],
            "markdown": "## 内容",
        }
    ],
}
OutlineSubmission.model_validate(valid)

invalid = {
    **valid,
    "slides": [{**valid["slides"][0], "key_points": ["只有一个要点"]}],
}
try:
    OutlineSubmission.model_validate(invalid)
except ValidationError:
    pass
else:
    raise AssertionError("key_points boundary was not enforced")
PY
```

预期：命令退出码为 0；旧字段不存在；两个 Markdown 字段存在描述；非法要点数量被拒绝。

- [x] **步骤 2：运行静态检查和配置校验**

运行：

```bash
uv run ruff check .
uv run langgraph validate
git diff --check
```

预期：全部退出码为 0，且 `agent/skills/pptx` 没有 diff。

- [x] **步骤 3：执行真实 Agent Server HITL smoke**

启动本地服务：

```bash
uv run langgraph dev --no-browser --no-reload --port 2024
```

另一个终端运行：

```bash
uv run python -m scripts.smoke_agent_server
```

预期：真实 DeepSeek 调用成功，出现 `submit_outline` 中断，审批恢复成功，输出 smoke 通过，并清理临时 Sandbox。

- [x] **步骤 4：检查未提交范围**

运行：

```bash
git status --short
git diff -- agent/skills/pptx
```

预期：只出现本次实现和规范相关改动；不提交 `docs/_chat`、预先存在的 PPTX Skill 或其他无关改动。
