# PPTX Sandbox 生成流水线设计

## 状态

- 日期：2026-08-19
- 状态：已完成书面规范审阅，等待实施
- 目标环境：本地 LangGraph Agent Server + 远程 LangSmith Sandbox
- 范围：Sandbox snapshot、PPTX Skill 加载、审批后生成、Thread 工作区同步、视觉迭代和真实端到端 smoke

## 背景

项目当前已实现 DeepSeek 主 Agent、结构化逐页大纲、`submit_outline` 人在回路审批、线程级 LangSmith Sandbox，以及调用远程 `qwen3.6-flash` 的 `view` 工具。仓库内保留了完整的 `agent/skills/pptx`，但它尚未安装到 Sandbox，也没有通过 Deep Agents 加载。

当前缺少以下完整链路：

1. 包含 PPTX 工具链和 Skill 的自定义 LangSmith Sandbox snapshot。
2. 大纲批准后的生成、编辑、校验、渲染和视觉修订流程。
3. 本机 Thread 工作区与远程 Sandbox 的文件传输。
4. 从远程工作目录保存全部文件，并按时间戳保留 PPTX 产物。
5. 覆盖 DeepSeek、Qwen、Sandbox 和可编辑 PPTX 的真实端到端 smoke。

LangSmith Sandbox 运行在远程云端，不能直接 bind mount 本机 `/Volumes/.../workspace` 目录。官方挂载能力面向 Git、S3 和 GCS。本阶段不引入对象存储，而是使用 LangSmith Sandbox 文件 API 在 Run 初始化时上传，在 Agent 完成后下载。

## 与旧规范的关系

本规范取代 `2026-08-19-pptx-view-adapter-design.md` 中以下未来约定：

- `/workspace/.work/<job-id>/` 和 `/workspace/output/` 路径；
- 宿主机目录直接挂载到 `/workspace` 的设想；
- 视觉轮数完全由 Agent 决定且不设上限；
- E2E 从 `/workspace/output/` 读取产物的断言。

旧规范中的 `view(image_paths, prompt)` 接口、Qwen 只读权限、图片下载失败和模型失败行为继续有效。

## 已确认决策

1. 保留单个 Deep Agent 主编排器，不新增自定义规划 Agent、生成 Agent 或显式多阶段 LangGraph 状态机；Deep Agents 自带能力不在本阶段重构。
2. `submit_outline` 保持唯一 HITL 工具；审批后由同一 Agent 继续生成。
3. DeepSeek 的文件和 shell 权限全程保留，不增加审批前 backend 权限门。
4. 审批前不得生成 PPTX 由系统提示词约束，不声明为强制安全边界。
5. 完整安装并加载 `agent/skills/pptx`，支持从零创建、模板套用、读取和编辑已有 PPTX/POTX。
6. 真实 E2E smoke 使用真实 DeepSeek、真实 Qwen 和真实 LangSmith Sandbox。
7. Qwen 视觉检查最多 3 轮。
8. 不引入 Job、对象存储、版本 manifest 或复杂双向同步协议。
9. 本机 `input/` 和 `work/` 在图初始化时都上传到远程对应目录。
10. 初始化上传是非破坏性的：同名文件覆盖，不删除远程独有文件。
11. `save_output` 下载远程 `work/` 的所有文件到本机 `work/`。
12. `save_output` 将所有 PPTX 额外复制到本机 `output/<时间戳>/`，时间戳目录不自动清理。

## 目标

1. 构建可复现的 LangSmith Sandbox snapshot，并验证完整 PPTX 工具链。
2. 将完整 PPTX Skill 安装到 `/skills/pptx/`，通过 `skills=["/skills/"]` 加载。
3. 让 DeepSeek 在大纲批准后自动生成、编辑、校验、渲染和修订 PPTX。
4. 在本机和远程之间建立简单、可测试的 Thread 目录传输。
5. 确保保存前所有 PPTX 通过确定性 OOXML 校验。
6. 使用真实 Qwen 对 150 DPI 逐页渲染图执行最多 3 轮视觉检查。
7. 用真实双模型 E2E smoke 证明最终可编辑 PPTX 已保存到本机。

## 非目标

- 不实现本机目录到远程 Sandbox 的 bind mount。
- 不接入 S3、GCS、MinIO 或其他对象存储。
- 不实现生产 Agent Server、PostgreSQL、Redis、认证或容器编排。
- 不拆分多 Agent 或新增外层 LangGraph 阶段状态机。
- 不对 DeepSeek 的文件或 shell 工具增加条件权限控制。
- 不修改、裁剪或重构 PPTX Skill 的内容。
- 不增加联网图片搜索或图片生成模型。
- 不自动清理本机时间戳输出目录。

## 总体架构

运行时保持现有异步图工厂：

1. Agent Server 从 `langgraph.json` 加载 `agent/agent.py:graph`。
2. 图工厂读取 UUID 格式的 `thread_id`。
3. 使用配置的 snapshot 获取或创建该 Thread 的 LangSmith Sandbox。
4. 创建本机 Thread 目录和远程工作目录。
5. 把本机 `input/` 与 `work/` 递归上传到远程目录。
6. 构造绑定当前 backend 的 `view` 和 `save_output` 工具。
7. 使用 DeepSeek、PPTX Skill 和工具创建单个 Deep Agent。
8. DeepSeek 收集需求并调用 `submit_outline`。
9. HITL 批准后，同一 Agent 继续生成、校验、渲染和视觉修订。
10. Agent 调用 `save_output`，把完整工作目录和 PPTX 产物保存到本机。

## Sandbox Snapshot

### 构建方式

新增 `sandbox/Dockerfile`，通过 LangSmith SDK：

```python
client.create_snapshot_from_dockerfile(
    name,
    dockerfile="sandbox/Dockerfile",
    context=temporary_build_context,
    fs_capacity_bytes=32 * 1024**3,
    vcpus=2,
    mem_bytes=8 * 1024**3,
    timeout=3600,
)
```

新增构建脚本负责：

- 接收 snapshot 名称或从配置读取名称；
- 创建只包含 Dockerfile、锁文件和 `agent/skills/pptx/` 副本的临时最小构建上下文；
- 流式输出远程构建日志；
- 固定使用 32 GiB 文件系统、2 vCPU、8 GiB 内存和 3600 秒构建超时；LangSmith 默认 builder snapshot 最小文件系统容量为 16 GiB，BuildKit 导出 PPTX 重镜像需要约 2-3 倍镜像大小的额外空间，16 GiB 实测不足；
- 构建完成后输出 snapshot ID、名称和状态；
- 同名 snapshot 已存在时拒绝覆盖，要求显式使用新版本名称；
- 不把 API Key、snapshot ID 或其他环境专属凭证写入版本控制。

构建上下文不得包含仓库 `.env`、`.venv`、`workspace/`、Git 元数据或其他项目文件。单元测试必须检查临时上下文的允许路径清单。

### 镜像内容

镜像基于 Python 3.13 Linux 环境，至少安装：

- Node.js 和 npm；
- `pptxgenjs`；
- `react`、`react-dom`、`react-icons` 和 `sharp`；
- LibreOffice Impress；
- Poppler 的 `pdftoppm`；
- `zip` 和 `unzip`；
- GCC，用于 Skill 的 LibreOffice socket shim；
- `fontconfig`；
- Noto CJK、Noto Core、Noto Emoji、Liberation、Carlito 和 Caladea 字体；
- `markitdown[pptx]`；
- Pillow、defusedxml 和 lxml。

完整复制：

```text
agent/skills/pptx/ -> /skills/pptx/
```

Skill 的 `SKILL.md`、`LICENSE.txt`、脚本和 OOXML schema 必须全部保留。

Node 依赖安装到固定位置，并通过 `NODE_PATH` 保证 Agent 在 `/workspace/work/` 中运行脚本时可以直接 `require()`。镜像不得要求 Agent 在每次生成前重新安装依赖。

基础镜像在 Dockerfile 中固定 digest。npm 依赖使用 `package-lock.json`，Python 依赖使用固定版本 requirements 文件并通过固定版本的 uv 安装，避免 snapshot 随构建时间漂移。

### Snapshot 配置

新增环境变量：

```text
SANDBOX_SNAPSHOT_NAME=ppt-deepagent-pptx-v1
```

`get_thread_sandbox_backend()` 先把配置名称解析为唯一且状态为 `ready` 的 snapshot ID，再使用 `snapshot_id` 创建 Sandbox。若 snapshot 不存在、不唯一、状态异常或创建失败，图初始化立即失败，不回退到默认 snapshot 或本地 shell。

复用已有 Thread Sandbox 前必须比较它的 `snapshot_id` 与配置名称解析出的 snapshot ID。两者不一致时明确失败并要求使用新 Thread 或人工删除旧 Sandbox，不自动删除或迁移已有远程文件。

### Snapshot 验证

验证脚本从目标 snapshot 创建临时 Sandbox，并执行：

1. 确认 `/skills/pptx/SKILL.md`、许可证、脚本和 schema 存在。
2. 确认 Node、Python、LibreOffice、Poppler、GCC 和字体工具可执行。
3. 确认所有 npm 与 Python 依赖可导入。
4. 用 PptxGenJS 生成最小测试 PPTX。
5. 用 `markitdown` 读取生成的 PPTX。
6. 用 `thumbnail.py` 生成缩略图。
7. 用 `add_slide.py` 和 `clean.py` 执行一次编辑流程。
8. 用 Skill 的 `validate.py` 校验 OOXML，并对编辑结果使用 `--original`。
9. 用 `soffice.py` 转为 PDF。
10. 用 `pdftoppm` 渲染逐页图片。
11. 检查 PPTX、PDF、缩略图和逐页图片均为非空文件。
12. 无论成功或失败都删除临时 Sandbox；snapshot 保留。

## 目录契约

### 本机

```text
workspace/<thread_id>/input/
workspace/<thread_id>/work/
workspace/<thread_id>/output/<YYYYMMDD-HHMMSS>/
```

### 远程 Sandbox

```text
/workspace/input/
/workspace/work/
/skills/pptx/
```

用途：

| 路径 | 用途 |
| --- | --- |
| 本机 `input/` | 用户在 Run 前放入的 PPTX、POTX、图片和其他素材 |
| 本机 `work/` | 最近一次 `save_output` 下载的完整远程工作目录 |
| 本机 `output/<时间戳>/` | `save_output` 不覆盖、不自动清理的 PPTX 产物副本 |
| 远程 `/workspace/input/` | 按提示词约定不修改的输入素材；backend 不强制只读 |
| 远程 `/workspace/work/` | 生成源码、中间文件、解包目录、渲染图、报告和 PPTX |
| `/skills/pptx/` | snapshot 内完整安装的 PPTX Skill |

## 初始化上传

新增 workspace 模块，使用由模块文件位置解析出的仓库根目录下的 `workspace/`，不依赖当前工作目录，也不新增可配置的本机根路径。

每次构造图时：

1. 验证 `thread_id` 为 UUID。
2. 创建缺失的本机 `input/`、`work/` 和 `output/` 目录。
3. 创建缺失的远程 `/workspace/input/` 和 `/workspace/work/`。
4. 递归读取本机 `input/` 和 `work/` 中的普通文件。
5. 保留相对路径，分别上传到远程目标目录。
6. 同名远程文件由本机内容覆盖。
7. 不删除远程目录中只存在于远程的文件。
8. 任一上传返回错误或缺少内容确认时，图初始化失败，Agent 不启动。

上传逻辑不跟随本机符号链接，不允许路径逃逸 Thread 目录，也不上传目录条目本身。

上传在每次新 Run 的图工厂初始化时执行，包括 HITL 恢复 Run。同一 Thread 不支持并发活动 Run；调用方必须等待当前 Run 完成或中断后再发起下一次 Run。本机同名 `work/` 文件覆盖远程较新文件是已确认的同步方向，发生失败恢复时应先人工下载或检查远程文件。

## Deep Agent 与 Skill 加载

`create_deep_agent` 增加：

```python
skills=["/skills/"]
```

继续显式注册：

- `submit_outline`
- `view`
- `save_output`

Deep Agents 默认文件、搜索、shell 和子任务能力保持现状。`submit_outline` 仍配置 `approve`、`edit`、`reject` 三种决策。

## 审批后的生成阶段

系统提示词分为两个阶段。

### 审批前

- 检查对话和 `/workspace/input/` 中已有素材。
- 只询问缺失需求。
- 形成完整逐页大纲。
- 调用 `submit_outline` 等待人工审批。
- 不生成或修改 PPTX。

### 审批后

`submit_outline` 只有在 HITL 批准或编辑后才实际执行，因此成功 ToolMessage 是进入生成阶段的信号。

DeepSeek 必须：

1. 阅读并遵循 `/skills/pptx/SKILL.md`。
2. 根据任务选择创建、模板套用、读取或编辑流程。
3. 保留 `/workspace/input/` 中的原始文件。
4. 把源码、工作副本和全部中间文件写入 `/workspace/work/`。
5. 优先生成真正可编辑的文本、图形和图表，不把整页扁平化为图片。
6. 在保存前完成内容检查、OOXML 校验、渲染和视觉检查。
7. 最终调用 `save_output`。

### 权限说明

按已确认决策，DeepSeek 的文件和 shell 权限在审批前后都存在。审批前禁止生成是 Agent 行为规则，而不是 backend 强制权限边界。实现和文档不得把它描述为技术上不可绕过的安全控制。

Qwen 仍只通过 `view` 接收图片和文字提示，不获得 backend、文件工具或 shell 工具。

## 生成、校验与视觉迭代

每次候选 PPTX 按以下顺序处理：

1. 使用 PptxGenJS 创建新演示文稿，或按 Skill 指引使用 OOXML/Python 工具编辑已有演示文稿。
2. 使用 `markitdown` 检查文本内容，并按 Skill 的固定占位检查规则拒绝 `xxx`、Lorem Ipsum、TODO、`[insert ...]` 等占位内容。
3. 使用 `/skills/pptx/scripts/office/validate.py` 执行 OOXML 确定性校验。
4. 使用 `/skills/pptx/scripts/office/soffice.py` 转换 PDF。
5. 使用 Poppler 以 150 DPI JPEG 将 PDF 渲染为逐页图片，并确认图片数量等于幻灯片数量。
6. 单次 `view` 调用提交当前候选版本的全部逐页图片，要求 Qwen 检查版式、溢出、重叠、可读性、视觉层级和整稿一致性。
7. DeepSeek 根据报告修订，再从内容和确定性校验开始重复。

确定性校验失败时不得保存。Qwen 返回自由 Markdown，由 DeepSeek 判断是否修订。一轮视觉检查定义为：同一候选 PPTX 的全部逐页图片在一次成功 `view` 调用中被检查。视觉检查至少执行一次，最多执行三次；第三次后不再继续视觉循环，保留最后报告并完成或明确报告无法解决的问题。三轮上限由系统提示词约束，不声明为 backend 强制调用配额。

## `save_output` 工具

### 接口

工具不接受本机路径，也不允许 Agent 选择任意远程根目录：

```python
save_output() -> str
```

工具在创建时绑定当前 Thread backend 和本机 Thread 目录。

### 保存流程

1. 递归列出远程 `/workspace/work/` 中的普通文件。
2. 若目录为空，返回 ToolException。
3. 找出全部大小写不敏感的 `.pptx` 文件；若不存在 PPTX，返回 ToolException。
4. 对每个 PPTX 执行 Skill 的基础 `validate.py`。模板或编辑任务必须在此前生成阶段额外使用 `--original` 完成来源感知校验。
5. 任一 PPTX 校验失败时返回 ToolException，不修改本机目录。
6. 拒绝符号链接、规范化后逃逸 `/workspace/work/` 的路径、重复相对路径和大小写折叠后冲突的路径。
7. 下载全部远程工作文件到本机临时目录，并验证每个响应成功。
8. 在临时目录中准备完整 `work/` 树和时间戳 PPTX 树。
9. 使用旧目录备份和重命名提交本机 `workspace/<thread_id>/work/`，然后提交时间戳输出目录；提交失败时在进程内尽力回滚。
10. 返回本机 `work/`、时间戳输出目录和保存的 PPTX 列表。

生成阶段在调用 `save_output` 前必须删除无效候选 PPTX，或把未完成候选改用非 `.pptx` 后缀。时间戳使用 Agent Server 本机时间。若同一秒内目录冲突，依次使用 `-01`、`-02` 等数字后缀避免覆盖。已创建的时间戳目录不自动删除。

### 暂存与一致性

在远程列举、验证、下载或临时树准备阶段发生任何错误时，本机现有 `work/` 和 `output/` 保持不变。提交阶段提供进程内异常回滚，但不承诺机器断电或进程强杀下跨两个目录的崩溃级事务原子性。后续调用会清理遗留 staging/backup 目录并重新保存。

## 错误处理

1. snapshot 未配置、不存在或不可创建时，图初始化失败。
2. snapshot 构建失败时保留并输出远程构建日志，不更新运行配置。
3. 任一初始化上传失败时，Agent 不启动。
4. 本机符号链接、路径逃逸和非普通文件不上传。
5. 生成命令失败时，Agent 根据工具输出修复；远程 `work/` 保留用于后续 Run。
6. `markitdown`、OOXML 校验、LibreOffice 或 Poppler 失败时不得调用 `save_output`。
7. Qwen 调用失败时返回错误 ToolMessage，由 DeepSeek决定重试；失败调用不算成功视觉检查。
8. `save_output` 的空目录、无 PPTX、校验失败、列举失败或下载失败都不得产生半成品本机结果；工具设置 `handle_tool_error=True`，让 DeepSeek 可以修复后重试。
9. E2E smoke 失败时输出 Thread ID、Sandbox 名称、trace ID 和本机工作目录。

## 测试设计

### 单元测试

使用临时本机目录、fake backend 和 fake 模型覆盖：

1. Thread 路径由 UUID 安全构造。
2. 缺失的本机目录会创建。
3. 本机 `input/` 和 `work/` 都递归上传。
4. 上传保留相对路径并覆盖同名远程文件。
5. 初始化不执行远程删除。
6. 空目录允许初始化。
7. 任一上传失败会阻止图创建。
8. 复用 snapshot 不匹配的已有 Sandbox 时明确失败。
9. `save_output` 递归下载全部普通文件。
10. 远程工作目录为空时失败。
11. 不存在 PPTX 时失败。
12. 任一 PPTX 校验失败时失败。
13. 任一下载失败时不修改已有本机结果。
14. 成功下载时替换本机 `work/`。
15. 所有 PPTX 都复制到新的时间戳输出目录。
16. 相对路径被保留，同名 PPTX 不冲突。
17. 远程符号链接、路径逃逸和大小写冲突被拒绝。
18. 时间戳冲突时使用数字后缀。
19. 图使用配置的 snapshot 创建 Sandbox。
20. 图上传工作区、加载 `/skills/` 并注册 `save_output`。
21. 临时 snapshot 构建上下文不包含 `.env`、`.venv` 或 `workspace/`。
22. 现有 `view` 与大纲 HITL 测试继续通过。

### Snapshot 集成验证

真实创建临时 Sandbox，验证：

- Skill 全量存在；
- npm 和 Python 依赖可用；
- PptxGenJS 可生成 PPTX；
- `validate.py` 通过；
- `markitdown`、缩略图、添加/清理幻灯片和 `--original` 校验通过；
- LibreOffice 可转换 PDF；
- Poppler 可渲染图片；
- 临时 Sandbox 被清理。

### 真实双模型 E2E Smoke

E2E 脚本要求本地 Agent Server 已启动，并执行：

1. 创建真实 Agent Server Thread 和对应本机目录。
2. 把固定嵌套输入素材和本机 `work/` 标记文件写入 Thread 目录，并在远程预置一个独有标记文件。
3. 请求 DeepSeek 创建固定三页演示文稿。
4. 验证只出现一个 `submit_outline` 中断，并检查允许决策。
5. 使用 `approve` 恢复同一 Thread。
6. 等待 DeepSeek 加载 Skill并完成生成。
7. 从消息和 LangSmith trace 确认批准后 `view` 成功执行 1 至 3 次，每次包含当前候选的全部页面，且渲染页数等于 PPTX 页数。
8. 确认 `save_output` 成功执行。
9. 检查本机 `work/` 存在生成源码、渲染或检查文件、PPTX、本机上传标记和远程独有标记，证明双向传输及非破坏上传生效。
10. 检查本机 `output/<时间戳>/` 存在非空 PPTX。
11. 将 PPTX 作为 ZIP 打开，检查核心 OOXML parts、三张幻灯片、请求中的固定文本 `<a:t>` 和原生 shape/chart，并拒绝只有整页栅格图的产物。
12. 查询 LangSmith，确认真实 DeepSeek、Qwen、`view` 和 `save_output` trace。
13. 清理临时 Agent Server Thread 和远程 Sandbox。
14. 保留本机 smoke 工作目录和输出，供人工查看。

E2E smoke 只固定验证从零创建流程。完整 Skill 的模板、读取和编辑能力通过安装完整性、工具链验证和后续专项测试保证，不要求一次 smoke 覆盖所有模式。

## 验收标准

1. 可以从 `sandbox/Dockerfile` 成功构建指定名称的 LangSmith snapshot。
2. snapshot 验证脚本完整通过，并清理临时 Sandbox。
3. Thread Sandbox 必须从配置的 snapshot 创建。
4. `/skills/pptx/` 与仓库 `agent/skills/pptx/` 内容完整对应。
5. Deep Agent 通过 `skills=["/skills/"]` 发现 PPTX Skill。
6. 图初始化上传本机 `input/` 和 `work/`，且不删除远程独有文件。
7. 大纲批准后，同一 Agent 完成生成、确定性校验、渲染和 1 至 3 次 Qwen 视觉检查。
8. 所有保存的 PPTX 在下载前通过 `validate.py`。
9. `save_output` 通过 staging 和进程内回滚更新本机 `work/`，并创建包含全部 PPTX 的时间戳输出目录。
10. 真实 E2E smoke 产出可打开且包含可编辑结构的 PPTX。
11. LangSmith trace 能证明 DeepSeek、Qwen、`view` 和 `save_output` 实际参与。
12. 全部单元测试、Ruff 和 LangGraph 配置校验通过。

## 实施顺序

1. 新增 Dockerfile、snapshot 设置、构建脚本和验证脚本。
2. 构建并验证真实 snapshot。
3. 新增 Thread workspace 路径和初始化上传。
4. 新增 `save_output` 工具及暂存、回滚测试。
5. 接入 snapshot、workspace 上传、Skill 和 `save_output`。
6. 更新系统提示词，加入审批后 PPTX 流程和三轮视觉上限。
7. 新增并运行真实双模型 E2E smoke。
8. 运行完整回归验证并更新架构文档中的当前状态。
