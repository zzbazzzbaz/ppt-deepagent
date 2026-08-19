# 大纲工具契约设计

## 状态

- 日期：2026-08-19
- 状态：已在对话中批准，等待书面规范审阅
- 范围：`agent/tools/outline.py`、Agent 系统提示词、真实 smoke 请求

## 背景

当前大纲工具会校验标题、受众、页面索引、页面目标和要点，但 schema 没有字段描述，工具说明也没有解释何时调用以及调用后的处理方式。当前系统提示词还要求 Agent 收集风格、页数和内容约束，却没有明确这些需求在大纲提交中如何表示。

应用会将其他演示需求保存在独立状态中。因此，本工具表示“待审批的大纲快照”，不表示完整需求对象，也不是最终 PPTX 生成输入的唯一来源。

## 目标

1. 通过 JSON Schema 描述，让 LLM 能理解每个大纲输入字段。
2. 使用中文明确工具用途、调用时机、审批行为和非职责。
3. 在大纲和页面两个层级增加人类可读的 Markdown。
4. 保留结构化 `key_points`，用于校验和后续程序处理。
5. 按要求从工具契约中删除 `audience` 和 `objective`。
6. 让 Agent 系统提示词、smoke 请求、校验错误和工具返回文案使用中文。
7. 保留 Agent Server 原生的 `approve`、`edit`、`reject` HITL 流程。

## 非目标

- 不增加 `topic`、`style`、`constraints`、`duration` 或 `audience` 等重复需求状态字段。
- 暂不增加页面布局枚举、图表 schema、素材引用、演讲者备注或 PPTX 专用字段。
- 不通过新增依赖解析或规范化 Markdown。
- 不修改 `submit_outline` 工具名或 Agent Server 审批协议。
- 不加载或修改 `agent/skills/pptx`。

## 数据模型

工具输入严格包含以下字段：

```python
class SlideOutline(BaseModel):
    index: int
    title: str
    key_points: list[str]
    markdown: str


class OutlineSubmission(BaseModel):
    title: str
    markdown: str
    slides: list[SlideOutline]
```

### `OutlineSubmission`

- `title`：展示文稿标题，供审批人和后续消费者使用。
- `markdown`：整套大纲的人类可读视图，应概括总体叙事和页面顺序，不包含完整 PPTX 实现细节。
- `slides`：按顺序排列的结构化页面列表。

### `SlideOutline`

- `index`：从 1 开始的连续页面编号。
- `title`：表达本页核心信息的短标题。
- `key_points`：2-5 条非空的原子化事实、观点或结论。每条只表达一个要点，不应是长段落或混合论证。
- `markdown`：当前页面的 Markdown 内容草稿，可以包含段落、列表、引用或表格，但不能包含 HTML、PPTX XML、布局代码或仅供演讲稿使用的填充内容。

Markdown 与 `key_points` 有意存在部分重叠：Markdown 服务人工审批和文本消费者，`key_points` 服务结构化校验和未来的程序处理。

## 校验规则

- 所有字符串都会去除首尾空白，且不能为空。
- 拒绝未声明的额外字段。
- `slides` 至少包含一页。
- `key_points` 必须包含 2-5 条要点。
- 页面 `index` 必须严格从 1 开始连续递增。
- 本阶段只校验 Markdown 非空，不校验 Markdown 语法；语法校验延后到 PPTX Skill 和渲染流水线设计阶段。

校验错误使用中文，并明确告诉 Agent 需要修正的内容。

## 工具引导

`submit_outline` 的 docstring 是面向 LLM 的操作契约，必须使用中文说明：

- 工具用于提交完整大纲，等待人工审批。
- 只有在演示需求完整且大纲自检通过后才能调用。
- 必须按照 schema 描述填写每个字段。
- Markdown 与结构化要点必须保持一致。
- 调用后会触发 `approve`、`edit` 或 `reject` 审批。
- 被拒绝后，必须根据人工反馈修改并重新提交。
- 工具不负责收集缺失需求、不生成 PPTX、不写文件，也不声称最终演示文稿已经完成。

工具返回中文，并保持职责边界：

```text
大纲已提交，结构校验通过，可进入下一阶段。
```

该返回不声称 PPTX 生成完成，也不声称整个业务流程完成。

## Agent 系统提示词

将当前英文系统提示词替换为中文提示词，并要求 Agent：

1. 在提问前读取对话和其他结构化状态。
2. 只询问真正缺失的需求。
3. 使用工具 schema 中的准确字段构建大纲。
4. 同时生成大纲级和页面级 Markdown，并保证其与 `key_points` 一致。
5. 调用工具前检查页面顺序、要点数量、重复内容、叙事连贯性以及与其他状态的一致性。
6. 首次完整草稿准备好后调用 `submit_outline`。
7. 将拒绝视为预期审批结果；必要时使用同一个工具修改并重新提交。
8. 将批准或编辑后批准视为进入下一阶段的交接点。
9. 本阶段绝不声称已经生成 PPTX。

提示词不能要求 `audience` 或页面级 `objective` 作为工具字段。准确的工具名 `submit_outline` 作为 API 标识保留英文。

## Smoke 请求

Smoke 客户端发送一条中文请求，其中包含完整需求，并要求 Agent 立即提交大纲。请求不再提及已删除的工具字段。

Smoke 断言保持协议导向：

- 存在一个 `submit_outline` 动作请求；
- 允许 `approve`、`edit`、`reject`；
- 同一 Thread 可以恢复；
- 审批后 Run 返回消息；
- 临时 Sandbox 清理成功。

## 验证

实现后运行：

```bash
uv run ruff check .
uv run langgraph validate
uv run python -m scripts.smoke_agent_server
```

另外执行本地 schema 检查，确认：

- `OutlineSubmission` 和 `SlideOutline` 中不存在 `audience`、`objective`；
- 两个 Markdown 字段都有描述；
- 非法页面顺序会被拒绝；
- 少于 2 条或多于 5 条要点会被拒绝；
- 所有必填字段都存在。

## 验收标准

1. schema 中没有 `audience` 或 `objective` 字段。
2. schema 中包含必填的大纲级和页面级 Markdown 字段。
3. 所有面向 LLM 的字段描述和提示词均为中文。
4. 工具 docstring 说明用途、时机、审批、拒绝处理和非职责。
5. Agent 不再包含互相矛盾的“只能调用一次”指引。
6. 本地 Agent Server 校验通过，真实 HITL smoke 流程通过。
7. `agent/skills/pptx` 保持不变。
