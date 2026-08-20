# Development Plan for ppt-deepagent MinIO 挂载改造

## Project Purpose and Goals

将 ppt-deepagent 的文件持久化层从「本地 `workspace/` 目录 + 启动/结束双向同步」改为「MinIO 对象存储 + LangSmith Sandbox 原生 S3 挂载」，使 MinIO 成为唯一持久化存储，Sandbox 只承担计算，本地 Agent Server 只负责编排、不再保存工作文件。

目标：

1. Sandbox 通过 `mount_config` 直接挂载 MinIO：`/skills`（只读）、`/workspace/input`（只读）、`/workspace/work`（读写）、`/workspace/output`（读写）。
2. `save_output` 重写为「校验 + 发布」工具：在 Sandbox 内将 `/workspace/work` 下全部 PPTX 复制到 `/workspace/output/<output-id>/`，并返回公网下载 URL。
3. 新增 `scripts/sync_skills_to_minio.py`，将本地 `agent/skills/` 同步到 MinIO `skills/` 前缀。
4. 删除本地 `workspace/` 目录及全部本地-远程同步代码（`agent/workspace.py` 等）。

## Context and Background

- LangSmith SDK（langsmith[sandbox]==0.11.0）已验证支持：
  - `s3_mount(*, id, mount_path, bucket, region='us-east-1', prefix=None, endpoint_url='https://s3.amazonaws.com', path_style=False, read_only=None, cache=None)`
  - `mount_config(*, mounts, auth=())`
  - `aws_auth(*, access_key_id, secret_access_key, name='aws', enabled=True, env_vars=None)`
  - `workspace_secret(name)` —— 引用 LangSmith Workspace Secret
  - `SandboxClient.create_sandbox(..., mount_config=...)`
- MinIO：`https://minio.gqt.plus`，Bucket `ppt-deepagent`，Region `us-east-1`，Path-style。公网 URL 形如 `https://minio.gqt.plus/ppt-deepagent/<key>`。
- 挂载认证密钥存放于 LangSmith Workspace Secrets（名称 `PPT_MINIO_ACCESS_KEY`、`PPT_MINIO_SECRET_KEY`），通过 `workspace_secret()` 引用；本地 `.env` 中的密钥只供同步脚本使用。密钥轮换与桶策略收紧由用户后续自行处理，不在本计划范围内。
- 当前架构（将被替换）：
  - `agent/workspace.py`：`thread_workspace()` 计算本地路径，`initialize_thread_workspace()` 启动时把本地 input/work 上传到 Sandbox。
  - `agent/tools/output.py`：`save_output` 下载全部远程 work 文件到本地，staging + 原子替换本地 `workspace/<id>/work`，并把 PPTX 复制到本地 `workspace/<id>/output/<时间戳>/`。
  - `sandbox/Dockerfile`：`COPY pptx /skills/pptx` 把 Skill 打进镜像。
- 不使用 `CompositeBackend`：Sandbox 直接使用 `LangSmithSandbox` backend，文件 API 与远程命令看到的是同一个由挂载提供的文件系统。
- MinIO Bucket 最终结构（对象 Key 前缀）：

  ```text
  ppt-deepagent/
  ├── skills/
  │   └── pptx/
  │       ├── SKILL.md
  │       ├── LICENSE.txt
  │       └── scripts/
  └── threads/
      └── <thread-id>/
          ├── input/...
          ├── work/...
          └── output/
              └── <output-id>/
                  └── *.pptx
  ```

- 挂载映射：

  | MinIO Prefix | Sandbox 路径 | 属性 |
  |---|---|---|
  | `skills` | `/skills` | 只读 |
  | `threads/<id>/input` | `/workspace/input` | 只读 |
  | `threads/<id>/work` | `/workspace/work` | 读写 |
  | `threads/<id>/output` | `/workspace/output` | 读写 |

- 挂载会遮盖镜像中的同名目录：必须先完成 Phase 2 的 Skill 上传，才能部署挂载改造；`skills/` 为空时 Agent 将看不到任何 Skill。
- 已存在的 Thread Sandbox 不会自动获得新挂载，需要删除让其重建（Phase 5 人工步骤）。现有 `snapshot_id` 不匹配即报错的守卫（`agent/sandbox.py:52`）保证 Snapshot 重建后旧 Sandbox 不会被复用。
- 已知风险（Phase 5 验证项）：S3 挂载的写回缓存对 PPTX 解包产生的大量小文件的性能与一致性。若验证失败，备选方案是 `/workspace/work` 不挂载、保留 Sandbox 本地磁盘并在 `save_output` 时经 MinIO API 上传（本计划不实现该方案，仅在验证失败时回到讨论）。

## Development Tasks

### Phase 1: MinIO 配置与 Sandbox 挂载

- [x] 在 `agent/settings.py` 增加 `MinioSettings`（`env_prefix="MINIO_"`，读取 `.env`，`extra="ignore"`）并实例化 `minio_settings`：
  - `endpoint_url: str`（如 `https://minio.gqt.plus`）
  - `bucket: str`
  - `region: str = "us-east-1"`
  - `path_style: bool = True`
  - `public_base_url: str`（如 `https://minio.gqt.plus`，用于拼公网 URL）
  - `access_key_secret: str = "PPT_MINIO_ACCESS_KEY"`（LangSmith Workspace Secret 名称，非密钥本身）
  - `secret_key_secret: str = "PPT_MINIO_SECRET_KEY"`
- [x] 在 `.env.example` 追加非敏感 MINIO 配置示例（不写任何真实密钥）：

  ```dotenv
  MINIO_ENDPOINT_URL=https://minio.gqt.plus
  MINIO_BUCKET=ppt-deepagent
  MINIO_REGION=us-east-1
  MINIO_PATH_STYLE=true
  MINIO_PUBLIC_BASE_URL=https://minio.gqt.plus
  MINIO_ACCESS_KEY_SECRET=PPT_MINIO_ACCESS_KEY
  MINIO_SECRET_KEY_SECRET=PPT_MINIO_SECRET_KEY
  ```

- [x] 在 `agent/sandbox.py` 新增 `_mount_config(thread_id: str)`：
  - `auth=[aws_auth(access_key_id=workspace_secret(minio_settings.access_key_secret), secret_access_key=workspace_secret(minio_settings.secret_key_secret))]`
  - 四个 `s3_mount`：`skills`→`/skills`（prefix `skills`，read_only=True）、`threads/<id>/input`→`/workspace/input`（read_only=True）、`threads/<id>/work`→`/workspace/work`（read_only=False）、`threads/<id>/output`→`/workspace/output`（read_only=False）；全部传 `bucket`、`region`、`endpoint_url`、`path_style`。
- [x] `_get_or_create_thread_sandbox` 在 `create_sandbox(...)` 时传入 `mount_config=_mount_config(thread_id)`。
- [x] 更新 `tests/test_sandbox.py`：
  - `_TEST_ENV` 增加 `MINIO_ENDPOINT_URL`、`MINIO_BUCKET`、`MINIO_PUBLIC_BASE_URL`（secret 名称用默认值可不设）。
  - `sandbox_settings_stub` 同样 stub `minio_settings`（SimpleNamespace）。
  - 断言 `create_sandbox` 收到的 `mount_config` 包含 4 个挂载且读写属性、prefix、mount_path 正确（断言结构体字段而非整对象相等，避免耦合 SDK 类型）。
  - 保留现有「snapshot 不匹配即拒绝」测试。
- [x] 运行 `uv run ruff check . && uv run pytest tests/test_sandbox.py`。
- [x] 对本阶段代码进行自查，确认 100% 符合本阶段要求后将任务标记为完成。
- [x] 停止并等待人工审查 #1

### Phase 2: Skills 同步脚本

- [x] 在 `pyproject.toml` 的 `dependency-groups.dev` 增加 boto3（用于脚本与冒烟测试；遵循仓库现有版本风格）。
- [x] 新增 `scripts/sync_skills_to_minio.py`：
  - 独立 `BaseSettings`（`env_prefix="MINIO_"`）：复用 endpoint/bucket/region/path_style，另加本地脚本专用 `access_key: str`、`secret_key: str`（来自本地环境或 `.env`，密钥不入库）。
  - boto3 S3 client：`endpoint_url`、凭证、region，`Config(s3={"addressing_style": "path"})`。
  - 递归扫描 `agent/skills/`（跳过符号链接、`__pycache__`、`.DS_Store`），映射为 `skills/<相对路径>`（如 `agent/skills/pptx/SKILL.md` → `skills/pptx/SKILL.md`）。
  - 默认上传全部文件（`put_object`）；`--delete` 时删除 MinIO `skills/` 前缀下本地已不存在的对象；`--dry-run` 只打印计划动作。
  - 同步后校验：`list_objects_v2` 获取远端全量 Key 集合，与本地集合比对必须一致；锚点文件必须存在（`skills/pptx/SKILL.md`、`skills/pptx/LICENSE.txt`、`skills/pptx/scripts/office/validate.py`、`skills/pptx/scripts/office/schemas/` 下至少一个对象）。校验失败以非零退出码结束。
  - 输出上传/删除/跳过摘要。
- [x] 新增 `tests/test_sync_skills_to_minio.py`（Mock boto3 client，不联网）：
  - 本地文件到 Key 的映射正确（含子目录）。
  - 默认不删除远端多余对象；`--delete` 只删除 `skills/` 前缀下的过期对象。
  - 校验发现远端缺失文件时报错（非零退出）。
  - 符号链接与 `__pycache__` 不上传。
- [x] 运行 `uv run ruff check . && uv run pytest tests/test_sync_skills_to_minio.py`。
- [x] 人工步骤（记录在脚本 docstring 或 README）：配置本地 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` 后执行脚本，确认 MinIO `skills/pptx/` 完整（后续阶段依赖此结果）。
- [x] 对本阶段代码进行自查，确认 100% 符合本阶段要求后将任务标记为完成。
- [x] 停止并等待人工审查 #2

### Phase 3: `save_output` 重写为校验发布工具

- [x] 重写 `agent/tools/output.py`：
  - 工厂签名改为 `create_save_output_tool(backend, thread_id, *, now=datetime.now, public_base_url=None)`；`public_base_url` 缺省时读 `minio_settings.public_base_url`。
  - 流程：
    1. `aglob("**/*", "/workspace/work")` 收集文件（保留现有 casefold 冲突检测、路径合法性校验、truncated 拒绝）。
    2. 至少存在一个 `.pptx`，否则 `ToolException`。
    3. `find /workspace/work -type l -print -quit` 拒绝符号链接（沿用现有实现）。
    4. 逐个运行 `python /skills/pptx/scripts/office/validate.py <path>`（沿用现有实现）。
    5. 生成 `output-id`：UTC 时间 `strftime("%Y%m%dT%H%M%SZ")` + `-` + `uuid4().hex[:6]`。
    6. 远程执行发布命令（逐文件，`shlex.quote` 转义）：

       ```sh
       mkdir -p -- '/workspace/output/<output-id>/<父目录>'
       cp -- '/workspace/work/<相对路径>' '/workspace/output/<output-id>/<相对路径>'
       ```

    7. 发布后逐文件 `cmp -s -- <src> <dst>` 校验副本一致、`test -s` 非空，失败即 `ToolException`。
    8. 返回消息包含 `output-id` 与每个 PPTX 的公网 URL（每行一个）：`{public_base_url}/{bucket}/threads/{thread_id}/output/{output-id}/{quote(相对路径)}`（用 `urllib.parse.quote` 处理空格与中文等字符）。
  - 删除全部本地下载/staging/原子替换/`shutil` 逻辑（`_collect_remote_files` 保留改造、`_validate_downloads`、`_prepare_staging_tree`、`_commit_staging_tree`、`_save_downloads_locally`、`_next_output_path` 删除）。
  - 工具 description 更新为：校验 `/workspace/work/` 中全部 PPTX，发布到 `/workspace/output/<output-id>/` 并返回公网下载链接。
  - 保留 `handle_tool_error = True`。
- [x] 更新 `agent/agent.py`：删除 `thread_workspace`/`initialize_thread_workspace` 导入与调用；`create_save_output_tool(backend, str(thread_id))`。
- [x] 更新 `agent/prompts/presentation_planner.py` 第 29 行附近对 `save_output` 的描述（改为「发布到 /workspace/output 并返回公网链接」语义）。
- [x] 重写 `tests/test_output.py`（FakeBackend 提供 `aglob`/`aexecute`，无需下载桩）：
  - 无 PPTX → error，且不执行任何发布命令。
  - 校验失败 → error，不执行发布命令。
  - 符号链接 / glob 截断 → error。
  - 成功：消息含 `output-id` 与全部 URL（含嵌套目录 PPTX 的 URL 路径）；发布命令包含 `mkdir -p`/`cp` 且目标路径含相对结构；`cmp` 校验命令被执行；固定 `now` 时 `output-id` 时间部分确定、后缀为 6 位十六进制。
  - 用 `blockbuster_ctx(scanned_modules=[output_module])` 防止工具内出现本地文件 I/O。
  - 按现有 `_TEST_ENV` 模式补充模块级环境 fixture（导入 `agent.settings` 需要 MINIO_* 变量）。
- [x] 更新 `tests/test_agent.py`：删除 workspace 相关 patch 与 `test_graph_does_not_create_agent_when_workspace_upload_fails`；断言 `create_save_output_tool` 以 `(backend, thread_id)` 调用；保留 `skills == ["/skills/"]` 断言。
- [x] 运行 `uv run ruff check . && uv run pytest tests/test_output.py tests/test_agent.py tests/test_prompt.py`。
- [x] 对本阶段代码进行自查，确认 100% 符合本阶段要求后将任务标记为完成。
- [x] 停止并等待人工审查 #3

### Phase 4: 删除本地同步逻辑

- [x] 用 `trash` 删除 `agent/workspace.py` 与 `tests/test_workspace.py`。
- [x] 全局搜索 `agent.workspace`、`thread_workspace`、`initialize_thread_workspace`、`WORKSPACE_ROOT` 引用并清理（`agent/`、`scripts/`、`tests/`）。
- [x] 改写 `scripts/smoke_pptx_e2e.py` 主流程（保留 `_tool_calls`、`_validate_editable_pptx`、`_wait_for_trace_runs` 及其测试）：
  - 用 boto3 把输入素材上传到 `threads/<thread-id>/input/`（替代 `_prepare_local_workspace`）。
  - 断言改为：运行结束后经 boto3 列举 `threads/<thread-id>/output/` 至少一个 `output-id` 且含 `.pptx`；`threads/<thread-id>/work/` 存在 `.js`/`.jpg` 产物。
  - 移除对本地 `workspace/` 输出目录的全部断言与打印。
- [x] 用 `trash` 删除本地 `workspace/` 目录前与用户确认（内含历史线程数据）。
- [x] 运行 `uv run ruff check . && uv run pytest`（除真实联网冒烟外的全部测试）。
- [x] 对本阶段代码进行自查，确认 100% 符合本阶段要求后将任务标记为完成。
- [x] 停止并等待人工审查 #4

### Phase 5: 端到端验证与 Snapshot 精简

- [ ] 人工步骤：在 LangSmith `Settings -> Secrets` 创建 `PPT_MINIO_ACCESS_KEY`、`PPT_MINIO_SECRET_KEY`。
- [ ] 人工步骤：删除现有 Thread Sandbox（无挂载，需重建才能生效）。
- [ ] 真实端到端验证（联网，人工触发）：
  - 运行改写后的 `scripts/smoke_pptx_e2e.py`，重点确认：MinIO `skills/` 挂载可读、`input` 只读挂载可见、`work` 上解包 OOXML 大量小文件可用、`save_output` 发布成功且 URL 可公开访问。
  - 若 `work` 挂载出现性能/一致性失败，停止并回到讨论（备选：work 不挂载，改 API 上传）。
- [x] 验证通过后精简 Snapshot：
  - `sandbox/Dockerfile` 删除 `COPY pptx /skills/pptx` 及对应 `test -f /skills/...` 校验（保留 `mkdir -p /workspace/input /workspace/work` 与 node_modules 软链）。
  - `scripts/sandbox_snapshot.py`：`prepare_build_context` 不再复制 `agent/skills/pptx`；`verify_snapshot` 删除 `/skills/pptx/...` 文件存在性检查（Skill 完整性改由同步脚本校验）。
  - 更新 `tests/test_sandbox_snapshot.py`（`test_copies_only_snapshot_assets_and_complete_pptx_skill` 等）。
  - 按 `scripts/sandbox_snapshot.py` 现有流程重建并验证 Snapshot；旧 Sandbox 因 snapshot 不匹配会被守卫拒绝，自动重建。
- [x] 运行 `uv run ruff check . && uv run pytest`。
- [x] 更新 README（如涉及 workspace 同步流程描述）。
- [x] 对本阶段代码进行自查，确认 100% 符合本阶段要求后将任务标记为完成。
- [x] 停止并等待人工审查 #5

## Important Considerations & Requirements

- [ ] 不过度设计：不引入 CompositeBackend、不新建抽象层，直接使用 SDK 的 `s3_mount`/`mount_config`。
- [ ] 不留占位或 TODO 代码。
- [ ] 密钥只存在于 LangSmith Workspace Secrets（挂载）与本地环境/`.env`（同步脚本）；任何密钥不得写入 Git 跟踪文件；`.env.example` 只放非敏感配置。
- [ ] 阶段顺序不可颠倒：Phase 2 完成 Skill 上传并确认 MinIO 内容完整之前，不得部署挂载改造（挂载会遮盖镜像内 `/skills`，空前缀会导致 Agent 失去 Skill）。
- [ ] 删除文件一律使用 `trash`，不用 `rm`；删除本地 `workspace/` 前必须征得用户确认。
- [ ] 测试不联网：Sandbox/Mount/boto3 一律 Mock/Fake；真实验证只在 Phase 5 人工执行。
- [ ] 输出 URL 中的对象 Key 必须做百分号编码（空格、中文文件名）。
- [ ] Agent 层对 input 的只读约束由两层保证：挂载 `read_only=True` + 现有提示词约定，无需新增代码。
- [ ] 保留 `agent/sandbox.py` 的 snapshot 一致性守卫，确保 Snapshot 重建后旧 Sandbox 不被复用。

## Technical Decisions

- **原生 S3 mount 而非 CompositeBackend**：CompositeBackend 只路由 Deep Agents 文件 API，远程命令（node/python/soffice）无法经其访问文件；只有真实文件系统挂载能让全部工具看到同一数据。
- **output 也挂载**：`save_output` 在 Sandbox 内直接 `cp` 到 `/workspace/output`，避免本地中转；MinIO 前缀 `threads/<id>/output` 即发布历史。
- **`output-id` = UTC 时间戳 + 6 位随机后缀**（如 `20260820T143052Z-a7f31c`）：可读且避免同秒冲突。
- **发布后 `cmp -s` 复核**：以同一挂载视角确认副本字节一致，防御复制中断或写回异常。
- **保留 casefold 路径冲突检测**：防止 MinIO（大小写敏感）中出现仅大小写不同的路径，为未来本地下载保留安全边际。
- **同步脚本用 boto3（仅 dev 依赖）**：与 `endpoint_url` + path-style 兼容，标准且可 Mock；不引入 `mc` 外部命令依赖。
- **Skill 先镜像后挂载的渐进策略**：Phase 1-4 保留 Dockerfile 内 `COPY pptx`（被挂载遮盖、无副作用），Phase 5 验证通过后才移除，保证任何时刻都有可用 Skill 来源。
- **`verify_snapshot` 移除 `/skills` 检查**：Snapshot 内不再打包 Skill，Skill 完整性由 `sync_skills_to_minio.py` 的同步后校验负责，职责分离。

## Testing Strategy

- 单元测试全部离线：`tests/test_sandbox.py`（Mock SandboxClient + settings stub）、`tests/test_sync_skills_to_minio.py`（Mock boto3）、`tests/test_output.py`（FakeBackend）、`tests/test_agent.py`（patch 工厂）。
- 沿用现有约定：blockbuster 检测异步路径中的本地阻塞 I/O；`_TEST_ENV` 环境字典模式；docstring 说明每个测试「catches」的回归风险。
- 命令：`uv run ruff check . && uv run pytest`。
- 真实联网验证（Phase 5 人工执行）：`scripts/smoke_pptx_e2e.py` + 浏览器/curl 访问返回的公网 URL。

## Debugging Protocol

- **测试失败**：分析根因后修复，不做绕过式修补。
- **挂载失败（Sandbox 创建/启动报错）**：先核对 LangSmith Secrets 名称与 MinIO 公网可达性，再核对 `endpoint_url`/`path_style`/`region`。
- **`work` 挂载性能/一致性问题**：先缩小范围（解包大量小文件 vs 单文件写回），记录现象；若确认不可用，停止实施并回到备选方案讨论。
- **需求不明**：停下来向用户确认，不自行假设。

## QA Checklist

- [ ] 全部用户指令落实（四挂载、save_output 新逻辑、同步脚本、删除本地同步）。
- [ ] `uv run ruff check .` 无告警。
- [ ] `uv run pytest` 全部通过（离线测试）。
- [ ] 挂载配置测试覆盖四个挂载的 prefix、mount_path、读写属性与认证 secret 引用。
- [ ] `save_output`：无 PPTX、校验失败、符号链接、glob 截断均有失败测试；成功路径断言发布命令与 URL。
- [ ] URL 格式正确且 Key 已百分号编码；`output-id` 含随机后缀。
- [ ] `agent/` 运行路径无本地工作文件写入（blockbuster 验证）。
- [ ] 仓库内无密钥（`.env.example` 仅非敏感配置）。
- [ ] README 与提示词中关于 save_output/workspace 的描述与实现一致。
- [ ] 删除操作均使用 `trash`；本地 `workspace/` 删除前经用户确认。
