# 本地 Agent Server 脚手架设计

## 状态

- 日期：2026-08-19
- 状态：已批准进入实现
- 范围：本地 Deep Agent、Agent Server 运行时、大纲审批的人机协同（HITL）、以及线程隔离的 LangSmith Sandbox
- 延后：PPTX 技能集成、Sandbox 镜像或快照、以及生产环境自托管 Agent Server 部署

## 背景

仓库中已包含 DeepSeek 与 LangSmith 相关设置、模型初始化、Deep Agents 与 LangGraph 依赖，以及 `agent/skills/pptx` 下的未经修改的 PPTX 技能。仓库目前尚不含 agent 图、Agent Server 配置、人工审批流程、本地启动文档或端到端验证客户端。

第一阶段必须在不引入应用 API 层的前提下，建立一个规模虽小但具备生产形态的本地运行时。Agent Server 负责线程、运行、流式输出、检查点持久化以及中断恢复行为。LangSmith Sandbox 是唯一的代码与文件系统执行后端。现有 PPTX 技能被有意排除，待后续阶段重新设计后再引入。

## 目标

1. 导出一个可在 `langgraph dev` 下运行的 Deep Agent 图工厂。
2. 为每个 Agent Server 线程提供并复用默认的 LangSmith Sandbox。
3. 以对话方式收集演示需求，并提交结构化的逐页大纲。
4. 在大纲提交前暂停，并支持标准的 `approve`、`edit` 与 `reject` 决策。
5. 通过本地 Agent Server 结合真实 DeepSeek 与 LangSmith API 验证完整流程。
6. 保持职责清晰、代码量精简。

## 非目标

- 不加载、复制、编辑或执行 `agent/skills/pptx`。
- 不生成或持久化 PPTX 文件。
- 不创建自定义 Sandbox 镜像或快照。
- 不新增 FastAPI、BFF、或私有的线程、运行、审批端点。
- 本阶段不新增生产环境 Docker Compose、PostgreSQL、Redis、Kubernetes 或自托管授权配置。
- 不采用 TDD、mock 或单元测试框架。
- 不通过本地 shell 增加回退执行。

## 选定架构

采用 Deep Agents 原生的人机协同中间件，而不是在 agent 外加一层 `StateGraph` 或在某个工具内部手动调用 `interrupt()`。

`langgraph.json` 注册一个图工厂。每次运行时，Agent Server 将运行时配置传给工厂。工厂要求提供 `thread_id`，解析线程隔离的 LangSmith Sandbox，并返回一个由 `create_deep_agent(...)` 构建的图，其中配置了模型、系统提示词、大纲工具、Sandbox 后端与审批策略。

Agent Server 负责检查点与持久化。图本身不创建 `MemorySaver` 或其它检查点器。这样可避免重复的持久化归属，并让本地行为与未来的 Agent Server 部署保持一致。

## 组件

### `agent/agent.py`

- 导出 `langgraph.json` 引用的图工厂。
- 从 `RunnableConfig` 读取运行的 `thread_id`。
- 解析线程对应的 Sandbox。
- 通过 `create_deep_agent` 构建 Deep Agent。
- 在 `interrupt_on` 中配置 `submit_outline`，并提供 `approve`、`edit`、`reject` 三种决策。
- 定义需求收集与大纲提交的精简系统行为。

### `agent/sandbox.py`

- 将有效的 Agent Server 线程 ID 转换为确定性的 Sandbox 名称。
- 实现幂等的 get-or-create 行为。
- 处理并发创建冲突，通过解析已创建的 Sandbox 而非重复创建。
- 在不指定 `snapshot_id` 或 `snapshot_name` 的情况下创建 Sandbox，从而使用 LangSmith 默认环境。
- 应用可配置的空闲与删除 TTL。
- 返回 `LangSmithSandbox`；绝不在本地回退执行。

### `agent/tools/outline.py`

- 为演示文稿与有序幻灯片定义严格的 Pydantic 兼容工具输入类型。
- 要求提供演示标题、受众，以及至少一页幻灯片。
- 要求每页幻灯片包含索引、标题、目标与非空要点。
- 本阶段仅暴露 `submit_outline` 这一个业务工具。
- 产生一个小的成功结果，且无外部副作用。

### 现有设置与模型模块

- 保留现有 DeepSeek 与 LangSmith 设置，作为所需凭证的来源。
- 仅新增需要应用默认值的 Sandbox 设置。
- 保持模型超时行为明确。
- 对于缺失或无效的必需设置，尽早失败。

### `langgraph.json`

- 以仓库根目录作为依赖根。
- 注册唯一图 ID：`ppt_agent`。
- 本地开发时加载 `.env`。
- 目标 Python 3.13。

### `scripts/smoke_agent_server.py`

- 通过 `langgraph-sdk` 连接本地 Agent Server。
- 创建临时线程。
- 发送一条明确的请求，促使 agent 提交大纲。
- 等待并校验 `submit_outline` 中断契约。
- 以审批决策恢复同一线程。
- 确认运行完成。
- 在清理阶段删除临时 Sandbox 与 Agent Server 线程。
- 校验协议字段与生命周期结果，而非概率性的自然语言措辞。

### `README.md` 与 `.env.example`

- 记录使用 `uv` 进行环境搭建。
- 记录本地 Agent Server 启动与 smoke 命令。
- 说明 HITL 决策载荷与当前范围限制。
- 不将生产部署描述为已实现。

## 运行时流程

1. 客户端通过 Agent Server SDK 创建 Thread。
2. 客户端携带用户的演示请求创建或流式执行 Run。
3. Agent Server 以该 Thread 的配置调用图工厂。
4. 图工厂为该 Thread get-or-create 对应的 Sandbox。
5. Deep Agent 询问缺失的主题、受众、篇幅或风格信息。
6. Deep Agent 以完整逐页大纲调用 `submit_outline`。
7. 原生 HITL 中间件在工具执行前中断。
8. Agent Server 检查点化该 Run，并返回带动作请求与允许决策的 `__interrupt__`。
9. 客户端以 `Command(resume={"decisions": [...]})` 恢复同一 Thread 与 Run 上下文。
10. 当审批通过或编辑有效时，无副作用的工具执行，agent 确认大纲阶段完成。
11. 当被拒绝时，工具不执行；原因返回给 agent，使其可修改后重新提交。

## HITL 契约

审批边界是 `submit_outline` 工具调用。中间件生成的中断即为公开契约，因此不引入任何重复的应用层审批 schema。

决策行为：

- `approve`：执行原始工具参数。
- `edit`：校验并执行人工编辑后的工具参数；编辑后的版本即为获批的大纲。
- `reject`：不执行工具；将拒绝原因返回给 agent，允许其再次提出方案。

该工具不执行任何外部写入。因此，即使图执行在中断附近被重放也是安全的。后续的 PPTX 生成阶段必须把不可逆的副作用放到审批边界之后、置于单独的工具或节点中。

## Sandbox 生命周期

Sandbox 的作用域是一个 Agent Server Thread。默认配置如下：

```dotenv
SANDBOX_NAME_PREFIX=ppt-deepagent
SANDBOX_IDLE_TTL_SECONDS=3600
SANDBOX_DELETE_AFTER_STOP_SECONDS=86400
```

名称由前缀与 Thread UUID 派生。同一 Thread 中的第二次 Run 会解析到同一 Sandbox，从而看到相同的远端文件系统。不同 Thread 会获得相互隔离的 Sandbox。

图工厂不会在 Run 结束时删除正常的 Sandbox。LangSmith 服务端 TTL 会回收不活跃的环境。smoke 客户端属于例外：它会显式删除临时 Sandbox，以确保验证不会遗留产生计费的资源。

## 错误处理

- 缺失 `thread_id`：以清晰的配置错误拒绝构建图。
- 缺失凭证或设置无效：在应用加载阶段失败，而非等到后续工具调用时才失败。
- Sandbox API 失败：使 Run 失败并暴露可诊断的异常，不做本地回退。
- 并发创建 Sandbox：在创建冲突后解析确定性已存在的 Sandbox。
- 大纲或编辑参数无效：使工具 schema 校验失败，并保持审批边界完整。
- 大纲被拒绝：视为预期的业务结果，而非系统错误。
- smoke 客户端清理失败：如实报告，而不掩盖主要验证结果。
- 密钥：绝不在日志、异常、README 示例或命令输出中暴露 API 密钥。

## 依赖

运行时依赖仍聚焦于 Deep Agents、LangGraph、模型提供商、LangSmith Sandbox 与设置。仅本地使用的工具放在开发依赖组中：

- `langgraph-cli[inmem]`：用于 `langgraph dev` 与配置校验。
- `langgraph-sdk`：用于可重复执行的 smoke 客户端。
- `ruff`：用于静态检查。

本阶段不新增测试运行器、mock 库、Web 框架、数据库驱动或部署包。

## 验证

开发并非测试驱动。验证在实现之后通过轻量检查与真实的端到端外部流程进行：

```bash
uv run ruff check .
uv run langgraph validate
uv run langgraph dev --no-browser
uv run python scripts/smoke_agent_server.py
```

smoke 流程使用真实的 `.env` 凭证，执行一次真实的 DeepSeek 模型调用，在未使用自定义镜像的情况下创建真实的 LangSmith Sandbox，观察真实的 Agent Server 中断，以审批恢复，并清理临时资源。

## 验收标准

1. `langgraph validate` 接受配置与图的导出。
2. `langgraph dev --no-browser` 成功启动本地 Agent Server。
3. 客户端可通过标准 SDK 创建 Thread 并运行 `ppt_agent`。
4. 首次大纲提交会以 `submit_outline` 动作请求暂停，并给出 `approve`、`edit`、`reject` 选项。
5. 通过 SDK 审批后恢复同一 Thread 并完成 Run。
6. 该 Run 使用以 Thread ID 命名的 LangSmith Sandbox，且未使用自定义快照或镜像。
7. smoke 流程删除其临时 Sandbox。
8. Ruff 通过。
9. `agent/skills/pptx` 不被实现改动。
10. 本阶段不声明或交付任何生产自托管行为。

## 未来边界

后续设计将改造 PPTX 技能、将其植入线程 Sandbox、在获批大纲边界之后加入生成与渲染工具，并定义产物持久化存储。当自托管授权与目标基础设施可用时，将另行设计生产环境独立 Agent Server 部署。
