# PPTX Sandbox 生成流水线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建并验证包含完整 PPTX Skill 的 LangSmith Sandbox snapshot，让当前 Deep Agent 在大纲批准后完成 PPTX 生成、校验、渲染、最多三轮 Qwen 视觉检查，并把远程 Thread 工作目录和时间戳 PPTX 产物保存到本机。

**Architecture:** 保留单个 Deep Agent 主编排器和现有 `submit_outline` HITL。图工厂按 Thread 创建指定 snapshot 的远程 Sandbox，非破坏性上传本机 `input/` 与 `work/`，加载 `/skills/`，并绑定 `view` 与 `save_output`；所有生成文件位于远程 `/workspace/work/`，保存时完整下载到本机 `work/` 并额外保留全部 PPTX 到 `output/<时间戳>/`。

**Tech Stack:** Python 3.13、Deep Agents 0.7.0、LangGraph 1.2.11、LangSmith Sandbox 0.11.0、PptxGenJS 4.0.1、LibreOffice、Poppler、Qwen `qwen3.6-flash`、标准库 `unittest`、Ruff。

## Global Constraints

- Python 必须满足 `>=3.13,<3.15`，所有项目命令通过 `uv run` 执行。
- 目标运行环境是本地 LangGraph Agent Server + 远程 LangSmith Sandbox，不实现本机 bind mount 或对象存储。
- 本机路径固定为 `workspace/<thread_id>/input/`、`work/`、`output/<YYYYMMDD-HHMMSS>/`。
- 远程路径固定为 `/workspace/input/`、`/workspace/work/` 和 `/skills/pptx/`。
- 图初始化同时上传本机 `input/` 与 `work/`；同名文件覆盖，但不得删除远程独有文件。
- `agent/skills/pptx/` 必须原样复制到 snapshot，不修改、裁剪或格式化该目录。
- DeepSeek 的文件和 shell 权限全程保持现状；审批前不生成 PPTX 是提示词规则，不宣称为 backend 强制边界。
- Qwen 只通过 `view` 接收图片和文字，不获得 backend、文件写入或 shell 权限。
- 每个候选版本使用 150 DPI JPEG 渲染全部页面；一次成功 `view` 调用检查全部页面，视觉检查 1 至 3 轮。
- `save_output` 必须先校验全部 PPTX，失败时不得改动现有本机结果。
- 外网 Docker、npm、uv 或 Git 请求使用 `http://127.0.0.1:7897` Clash 代理；代理不可用时停止并告知用户。
- 当前用户对根目录架构文档的已有变更不属于本计划，不得撤销或擅自修改。
- 每个提交步骤仅在用户明确授权提交后执行；否则保留经验证的未提交改动。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `sandbox/Dockerfile` | 定义固定 digest 的 Python 3.13 PPTX Sandbox 镜像 |
| `sandbox/package.json` | 固定 PptxGenJS、React、图标和 Sharp 依赖 |
| `sandbox/package-lock.json` | npm 可复现安装锁文件 |
| `sandbox/requirements.txt` | 固定 Skill Python 运行依赖 |
| `agent/snapshot.py` | 提供构建脚本与运行时共用的精确 ready snapshot 解析 |
| `scripts/sandbox_snapshot.py` | 创建最小构建上下文、构建 snapshot、验证远程工具链 |
| `agent/settings.py` | 增加 `SANDBOX_SNAPSHOT_NAME` 配置 |
| `agent/sandbox.py` | 按配置解析 snapshot ID，创建或校验 Thread Sandbox |
| `agent/workspace.py` | 计算 Thread 本机路径并上传 `input/`、`work/` |
| `agent/tools/output.py` | `save_output` 的远程校验、下载、staging、回滚和时间戳保存 |
| `agent/prompts/presentation_planner.py` | 审批前规划和审批后 PPTX 生成规则 |
| `agent/tools/outline.py` | 把工具成功结果明确为“大纲已批准”阶段信号 |
| `agent/agent.py` | 接入 snapshot workspace、Skill 和 `save_output` |
| `scripts/smoke_pptx_e2e.py` | 真实 DeepSeek + Qwen + Sandbox + 本机产物 E2E |
| `.env.example` | 记录 snapshot 名称示例 |
| `tests/test_sandbox_snapshot.py` | snapshot 上下文与 CLI 辅助逻辑测试 |
| `tests/test_sandbox.py` | snapshot 解析、创建和不匹配复用测试 |
| `tests/test_workspace.py` | Thread 路径与双目录上传测试 |
| `tests/test_output.py` | `save_output` 成功、校验、路径安全和回滚测试 |
| `tests/test_prompt.py` | 两阶段生成提示词契约测试 |
| `tests/test_agent.py` | 图工厂完整接线测试 |
| `tests/test_smoke_pptx_e2e.py` | E2E 结果、消息和 OOXML 检查辅助函数测试 |

---

### Task 1: Snapshot 镜像与构建验证 CLI

**Files:**
- Create: `sandbox/Dockerfile`
- Create: `sandbox/package.json`
- Create: `sandbox/package-lock.json`
- Create: `sandbox/requirements.txt`
- Create: `agent/snapshot.py`
- Create: `scripts/sandbox_snapshot.py`
- Create: `tests/test_sandbox_snapshot.py`

**Interfaces:**
- Produces: `prepare_build_context(project_root: Path, destination: Path) -> None`
- Produces in `agent.snapshot`: `find_ready_snapshot(client: SandboxClient, name: str) -> Snapshot`
- Produces: `build_snapshot(client: SandboxClient, name: str, project_root: Path) -> Snapshot`
- Produces: `verify_snapshot(client: SandboxClient, name: str) -> None`
- Produces CLI: `uv run python -m scripts.sandbox_snapshot build --name <name>`
- Produces CLI: `uv run python -m scripts.sandbox_snapshot verify --name <name>`

- [ ] **Step 1: 写最小构建上下文失败测试**

```python
class SnapshotBuildContextTests(unittest.TestCase):
    def test_context_contains_only_sandbox_assets_and_complete_skill(self) -> None:
        with TemporaryDirectory() as temp:
            destination = Path(temp)
            prepare_build_context(PROJECT_ROOT, destination)

            self.assertEqual(
                {path.name for path in destination.iterdir()},
                {"Dockerfile", "package.json", "package-lock.json", "requirements.txt", "pptx"},
            )
            expected = sorted(
                path.relative_to(PROJECT_ROOT / "agent/skills/pptx")
                for path in (PROJECT_ROOT / "agent/skills/pptx").rglob("*")
                if path.is_file()
            )
            actual = sorted(
                path.relative_to(destination / "pptx")
                for path in (destination / "pptx").rglob("*")
                if path.is_file()
            )
            self.assertEqual(actual, expected)
            self.assertFalse((destination / ".env").exists())
            self.assertFalse((destination / "workspace").exists())
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `uv run python -m unittest tests.test_sandbox_snapshot -v`

Expected: FAIL，提示无法导入 `scripts.sandbox_snapshot`。

- [ ] **Step 3: 新增固定依赖清单**

`sandbox/package.json`：

```json
{
  "name": "ppt-deepagent-sandbox",
  "private": true,
  "version": "1.0.0",
  "dependencies": {
    "pptxgenjs": "4.0.1",
    "react": "19.2.8",
    "react-dom": "19.2.8",
    "react-icons": "5.7.0",
    "sharp": "0.33.5"
  }
}
```

`sandbox/requirements.txt`：

```text
markitdown[pptx]==0.1.4
Pillow==11.3.0
defusedxml==0.7.1
lxml==6.0.1
```

生成 npm 锁文件：

```bash
npm_config_proxy=http://127.0.0.1:7897 npm_config_https_proxy=http://127.0.0.1:7897 npm install --package-lock-only --ignore-scripts --prefix sandbox
```

- [ ] **Step 4: 新增固定 digest Dockerfile**

核心内容必须是：

```dockerfile
FROM python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_PATH=/opt/pptx/node_modules \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates gcc nodejs npm zip unzip \
    libreoffice-impress libreoffice-common poppler-utils fontconfig \
    fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
    fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/pptx-requirements.txt
RUN pip install --no-cache-dir -r /tmp/pptx-requirements.txt

WORKDIR /opt/pptx
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm rebuild sharp

COPY pptx /skills/pptx
RUN test -f /skills/pptx/SKILL.md \
    && test -f /skills/pptx/LICENSE.txt \
    && chmod -R a+rX /skills/pptx \
    && mkdir -p /workspace/input /workspace/work

WORKDIR /workspace/work
```

- [ ] **Step 5: 实现最小构建上下文和 snapshot 查找**

```python
BUILD_ASSETS = ("Dockerfile", "package.json", "package-lock.json", "requirements.txt")
FS_CAPACITY_BYTES = 32 * 1024**3
VCPUS = 2
MEM_BYTES = 8 * 1024**3
BUILD_TIMEOUT_SECONDS = 3600


def prepare_build_context(project_root: Path, destination: Path) -> None:
    sandbox_dir = project_root / "sandbox"
    for name in BUILD_ASSETS:
        shutil.copy2(sandbox_dir / name, destination / name)
    shutil.copytree(project_root / "agent/skills/pptx", destination / "pptx")


def find_ready_snapshot(client: SandboxClient, name: str) -> Snapshot:
    matches = [item for item in client.list_snapshots(name_contains=name) if item.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one snapshot named {name!r}, found {len(matches)}")
    snapshot = matches[0]
    if snapshot.status != "ready":
        raise RuntimeError(f"Snapshot {name!r} is not ready: {snapshot.status}")
    return snapshot
```

`build_snapshot()` 必须先确认不存在同名 snapshot，再在 `TemporaryDirectory` 中调用 `prepare_build_context()` 和 `create_snapshot_from_dockerfile()`；不得把仓库根目录直接作为 `context`。

- [ ] **Step 6: 写构建拒绝覆盖与验证清理测试**

```python
def test_build_refuses_existing_snapshot(self) -> None:
    client = Mock()
    client.list_snapshots.return_value = [SimpleNamespace(name="ppt-v1", status="ready")]
    with self.assertRaisesRegex(RuntimeError, "already exists"):
        build_snapshot(client, "ppt-v1", PROJECT_ROOT)


def test_verify_deletes_temporary_sandbox_after_command_failure(self) -> None:
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(id="snapshot-id", name="ppt-v1", status="ready")
    ]
    sandbox = SimpleNamespace(name="ppt-v1-verify", status="ready")
    client.create_sandbox.return_value = sandbox
    with patch("scripts.sandbox_snapshot.LangSmithSandbox") as backend_type:
        backend_type.return_value.execute.return_value = ExecuteResponse(
            output="missing dependency", exit_code=1, truncated=False
        )
        with self.assertRaisesRegex(RuntimeError, "missing dependency"):
            verify_snapshot(client, "ppt-v1")
    client.delete_sandbox.assert_called_once_with("ppt-v1-verify")
```

- [ ] **Step 7: 实现真实 snapshot 验证命令矩阵**

验证脚本上传一个固定 PptxGenJS 文件并按顺序执行：

```text
node /workspace/work/verify.js
markitdown /workspace/work/original.pptx > /workspace/work/original.md
python /skills/pptx/scripts/thumbnail.py /workspace/work/original.pptx /workspace/work/thumbs
python /skills/pptx/scripts/add_slide.py /workspace/work/original.pptx slide1.xml -o /workspace/work/edited.pptx
python /skills/pptx/scripts/office/validate.py /workspace/work/edited.pptx --original /workspace/work/original.pptx
unzip -q /workspace/work/edited.pptx -d /workspace/work/unpacked
python /skills/pptx/scripts/clean.py /workspace/work/unpacked
cd /workspace/work/unpacked && zip -qr /workspace/work/cleaned.pptx .
python /skills/pptx/scripts/office/validate.py /workspace/work/cleaned.pptx --original /workspace/work/original.pptx
python /skills/pptx/scripts/office/soffice.py --headless --convert-to pdf --outdir /workspace/work /workspace/work/cleaned.pptx
pdftoppm -jpeg -r 150 /workspace/work/cleaned.pdf /workspace/work/slide
test -s /workspace/work/original.md
test -s /workspace/work/thumbs.jpg
test -s /workspace/work/slide-1.jpg
```

每条命令都通过 `_require_success(response, label)` 检查 `exit_code == 0`；`finally` 始终删除临时 Sandbox。

- [ ] **Step 8: 运行 Task 1 测试和静态检查**

Run:

```bash
uv run python -m unittest tests.test_sandbox_snapshot -v
uv run ruff check scripts/sandbox_snapshot.py tests/test_sandbox_snapshot.py
git diff --check
```

Expected: 全部通过。

- [ ] **Step 9: 在获得用户提交授权后提交 Task 1**

```bash
git add sandbox agent/snapshot.py scripts/sandbox_snapshot.py tests/test_sandbox_snapshot.py
git commit -m "feat: add PPTX sandbox snapshot build"
```

---

### Task 2: Snapshot 感知的 Thread Sandbox 生命周期

**Files:**
- Modify: `.env.example`
- Modify: `agent/settings.py:41-56`
- Modify: `agent/sandbox.py:13-60`
- Create: `tests/test_sandbox.py`
- Modify: `tests/test_agent.py:8-23`

**Interfaces:**
- Produces: `SandboxSettings.snapshot_name: str`
- Consumes: `find_ready_snapshot(client: SandboxClient, name: str) -> Snapshot`
- Preserves: `sandbox_name_for_thread(thread_id: str) -> str`
- Preserves: `get_thread_sandbox_backend(thread_id: str) -> LangSmithSandbox`

- [ ] **Step 1: 写 snapshot 配置和创建行为失败测试**

```python
class SandboxLifecycleTests(unittest.TestCase):
    def test_creates_thread_sandbox_from_resolved_snapshot_id(self) -> None:
        snapshot = SimpleNamespace(id="snapshot-id", name="ppt-v1", status="ready")
        self.client.list_snapshots.return_value = [snapshot]
        self.client.get_sandbox.side_effect = ResourceNotFoundError("missing")
        created = SimpleNamespace(
            name="ppt-thread", status="ready", snapshot_id="snapshot-id"
        )
        self.client.create_sandbox.return_value = created

        actual = sandbox_module._get_or_create_thread_sandbox(THREAD_ID)

        self.assertIs(actual, created)
        self.client.create_sandbox.assert_called_once_with(
            snapshot_id="snapshot-id",
            name=sandbox_module.sandbox_name_for_thread(THREAD_ID),
            idle_ttl_seconds=60,
            delete_after_stop_seconds=60,
        )

    def test_rejects_existing_sandbox_from_different_snapshot(self) -> None:
        self.client.list_snapshots.return_value = [
            SimpleNamespace(id="expected", name="ppt-v1", status="ready")
        ]
        self.client.get_sandbox.return_value = SimpleNamespace(
            name="ppt-thread", status="ready", snapshot_id="old"
        )
        with self.assertRaisesRegex(RuntimeError, "snapshot mismatch"):
            sandbox_module._get_or_create_thread_sandbox(THREAD_ID)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m unittest tests.test_sandbox -v`

Expected: FAIL，缺少 `snapshot_name` 与 snapshot 解析。

- [ ] **Step 3: 增加必填 snapshot 配置**

```python
class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="SANDBOX_", extra="ignore"
    )

    name_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    snapshot_name: str = Field(min_length=1)
    idle_ttl_seconds: int = Field(ge=0, multiple_of=60)
    delete_after_stop_seconds: int = Field(ge=0, multiple_of=60)
```

`.env.example` 增加：

```text
SANDBOX_SNAPSHOT_NAME=ppt-deepagent-pptx-v1
```

所有测试环境字典增加 `"SANDBOX_SNAPSHOT_NAME": "test-snapshot"`。

- [ ] **Step 4: 接入共享 snapshot 解析并实现复用校验**

```python
snapshot = find_ready_snapshot(_client, sandbox_settings.snapshot_name)
```

`_get_or_create_thread_sandbox()` 先解析 snapshot；已有 Sandbox 的 `snapshot_id` 必须匹配。创建冲突后重新读取时也必须再次校验 snapshot ID。

- [ ] **Step 5: 补齐不存在、不唯一、非 ready、冲突重取测试**

每个分支使用 fake `SandboxClient` 明确断言错误文本和 `create_sandbox` 调用次数；不得调用删除 API。

- [ ] **Step 6: 运行 Task 2 回归**

Run:

```bash
uv run python -m unittest tests.test_sandbox tests.test_agent -v
uv run ruff check agent/settings.py agent/sandbox.py tests/test_sandbox.py tests/test_agent.py
uv run langgraph validate
```

Expected: 全部通过；`langgraph validate` 找到一个图。

- [ ] **Step 7: 在获得用户提交授权后提交 Task 2**

```bash
git add .env.example agent/settings.py agent/sandbox.py tests/test_sandbox.py tests/test_agent.py
git commit -m "feat: create sandboxes from configured snapshot"
```

---

### Task 3: Thread 工作区与初始化上传

**Files:**
- Create: `agent/workspace.py`
- Create: `tests/test_workspace.py`

**Interfaces:**
- Produces dataclass: `ThreadWorkspace(root: Path, input: Path, work: Path, output: Path)`
- Produces: `thread_workspace(thread_id: str) -> ThreadWorkspace`
- Produces: `collect_uploads(local_root: Path, remote_root: str) -> list[tuple[str, bytes]]`
- Produces async: `initialize_thread_workspace(backend: SandboxBackendProtocol, workspace: ThreadWorkspace) -> None`

- [ ] **Step 1: 写 UUID 路径与双目录收集失败测试**

```python
class ThreadWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def test_thread_workspace_uses_project_workspace_root(self) -> None:
        workspace = thread_workspace("4ef6e832-7c8d-4d15-9b28-0547bf2090b0")
        self.assertEqual(workspace.root.name, "4ef6e832-7c8d-4d15-9b28-0547bf2090b0")
        self.assertEqual(workspace.input, workspace.root / "input")
        self.assertEqual(workspace.work, workspace.root / "work")
        self.assertEqual(workspace.output, workspace.root / "output")

    async def test_initialization_uploads_input_and_work_without_delete(self) -> None:
        workspace = self.make_workspace()
        (workspace.input / "nested").mkdir()
        (workspace.input / "nested/brief.txt").write_bytes(b"brief")
        (workspace.work / "source.js").write_bytes(b"source")

        await initialize_thread_workspace(self.backend, workspace)

        uploaded = dict(self.backend.aupload_files.await_args.args[0])
        self.assertEqual(uploaded["/workspace/input/nested/brief.txt"], b"brief")
        self.assertEqual(uploaded["/workspace/work/source.js"], b"source")
        self.backend.adelete.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m unittest tests.test_workspace -v`

Expected: FAIL，无法导入 `agent.workspace`。

- [ ] **Step 3: 实现安全路径和文件收集**

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"


@dataclass(frozen=True)
class ThreadWorkspace:
    root: Path
    input: Path
    work: Path
    output: Path


def thread_workspace(thread_id: str) -> ThreadWorkspace:
    normalized = str(UUID(thread_id))
    root = WORKSPACE_ROOT / normalized
    return ThreadWorkspace(root, root / "input", root / "work", root / "output")
```

`collect_uploads()` 使用排序后的 `rglob("*")`，跳过 symlink 和非普通文件，并用 `relative_to()` + `PurePosixPath` 形成远程路径。

- [ ] **Step 4: 实现非破坏上传和响应校验**

```python
async def initialize_thread_workspace(
    backend: SandboxBackendProtocol,
    workspace: ThreadWorkspace,
) -> None:
    for directory in (workspace.input, workspace.work, workspace.output):
        directory.mkdir(parents=True, exist_ok=True)
    mkdir_result = await backend.aexecute(
        "mkdir -p -- /workspace/input /workspace/work"
    )
    if mkdir_result.exit_code != 0:
        raise RuntimeError(f"Failed to initialize remote workspace: {mkdir_result.output}")
    files = collect_uploads(workspace.input, "/workspace/input")
    files.extend(collect_uploads(workspace.work, "/workspace/work"))
    if not files:
        return
    responses = await backend.aupload_files(files)
    validate_upload_responses(files, responses)
```

- [ ] **Step 5: 补齐 symlink、空目录、数量不匹配和部分失败测试**

断言：symlink 不上传；空目录只执行 `mkdir`；响应数量不同抛 `RuntimeError`；任一 `FileUploadResponse.error` 包含路径并终止初始化。

- [ ] **Step 6: 运行 Task 3 测试**

Run:

```bash
uv run python -m unittest tests.test_workspace -v
uv run ruff check agent/workspace.py tests/test_workspace.py
git diff --check
```

Expected: 全部通过。

- [ ] **Step 7: 在获得用户提交授权后提交 Task 3**

```bash
git add agent/workspace.py tests/test_workspace.py
git commit -m "feat: sync thread workspace to sandbox"
```

---

### Task 4: `save_output` 保存工具

**Files:**
- Create: `agent/tools/output.py`
- Create: `tests/test_output.py`

**Interfaces:**
- Consumes: `ThreadWorkspace`
- Produces: `create_save_output_tool(backend: SandboxBackendProtocol, workspace: ThreadWorkspace) -> BaseTool`
- Tool contract: `save_output() -> str`
- Fixed source: `/workspace/work/`
- Fixed destinations: `workspace.work` and `workspace.output/<timestamp>/`

- [ ] **Step 1: 写远程空目录、无 PPTX 和校验失败测试**

```python
class SaveOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_work_directory_without_pptx(self) -> None:
        self.backend.aglob.return_value = GlobResult(
            error=None,
            matches=[FileInfo(path="/workspace/work/source.js", is_dir=False, size=6)],
            truncated=False,
        )
        result = await self.invoke_tool()
        self.assertEqual(result.status, "error")
        self.assertIn("PPTX", result.text)
        self.backend.adownload_files.assert_not_awaited()

    async def test_validation_failure_keeps_local_work_unchanged(self) -> None:
        (self.workspace.work / "existing.txt").write_text("keep")
        self.given_remote_files("/workspace/work/final.pptx")
        self.backend.aexecute.side_effect = [
            ExecuteResponse(output="", exit_code=0, truncated=False),
            ExecuteResponse(output="invalid", exit_code=1, truncated=False),
        ]
        result = await self.invoke_tool()
        self.assertEqual(result.status, "error")
        self.assertEqual((self.workspace.work / "existing.txt").read_text(), "keep")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m unittest tests.test_output -v`

Expected: FAIL，无法导入 `agent.tools.output`。

- [ ] **Step 3: 实现远程文件清单和路径安全检查**

```python
REMOTE_WORK = PurePosixPath("/workspace/work")


def normalize_remote_file(path: str) -> tuple[str, Path]:
    remote = PurePosixPath(path)
    relative = remote.relative_to(REMOTE_WORK)
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ToolException(f"Invalid remote work path: {path}")
    return str(remote), Path(*relative.parts)
```

调用 `find /workspace/work -type l -print` 拒绝远程 symlink；拒绝 `GlobResult.truncated`、目录条目、重复相对路径及 `casefold()` 后冲突路径。

- [ ] **Step 4: 实现全部 PPTX 的发布前校验**

对所有 `relative.suffix.lower() == ".pptx"` 文件执行：

```python
command = (
    "python /skills/pptx/scripts/office/validate.py "
    + shlex.quote(remote_path)
)
response = await backend.aexecute(command)
if response.exit_code != 0:
    raise ToolException(f"PPTX validation failed for {remote_path}: {response.output}")
```

校验前不下载，任一失败立即停止。

- [ ] **Step 5: 写成功下载、嵌套 PPTX 和时间戳冲突测试**

```python
async def test_saves_complete_work_tree_and_all_pptx_versions(self) -> None:
    self.given_remote_files(
        "/workspace/work/source.js",
        "/workspace/work/final/deck.pptx",
        "/workspace/work/archive/deck.pptx",
    )
    self.given_successful_downloads(
        b"source", b"final-pptx", b"archive-pptx"
    )

    result = await self.invoke_tool(now=datetime(2026, 8, 19, 12, 34, 56))

    self.assertEqual(result.status, "success")
    self.assertEqual((self.workspace.work / "source.js").read_bytes(), b"source")
    output = self.workspace.output / "20260819-123456"
    self.assertEqual((output / "final/deck.pptx").read_bytes(), b"final-pptx")
    self.assertEqual((output / "archive/deck.pptx").read_bytes(), b"archive-pptx")
```

再次用同一时间调用时应创建 `20260819-123456-01`。

- [ ] **Step 6: 实现 staging、备份和进程内回滚**

实现顺序固定为：下载到 Thread 根目录内的临时树；准备临时 `work/` 和临时时间戳 PPTX 树；把现有 `work/` 重命名为唯一 backup；把临时 `work/` 提交到正式路径；提交输出目录；失败时恢复 backup；成功后清理 backup。工具设置：

```python
save_output.handle_tool_error = True
```

返回文本包含本机 `work/`、时间戳输出目录和相对 PPTX 列表。

- [ ] **Step 7: 补齐下载失败、大小写冲突、symlink 和提交回滚测试**

使用 fake backend 与 `patch.object(Path, "rename", side_effect=OSError("commit failed"))` 验证错误 ToolMessage 和原有文件保留。测试不创建真实远程 Sandbox。

- [ ] **Step 8: 运行 Task 4 测试**

Run:

```bash
uv run python -m unittest tests.test_output -v
uv run ruff check agent/tools/output.py tests/test_output.py
git diff --check
```

Expected: 全部通过。

- [ ] **Step 9: 在获得用户提交授权后提交 Task 4**

```bash
git add agent/tools/output.py tests/test_output.py
git commit -m "feat: save sandbox PPTX outputs locally"
```

---

### Task 5: 两阶段生成提示词

**Files:**
- Modify: `agent/prompts/presentation_planner.py:1-12`
- Modify: `agent/tools/outline.py:64-70`
- Create: `tests/test_prompt.py`

**Interfaces:**
- Preserves: `PRESENTATION_PLANNER_SYSTEM_PROMPT: str`
- Changes success signal: `submit_outline(...) -> "大纲已批准，可以开始生成演示文稿。"`

- [ ] **Step 1: 写审批前后规则失败测试**

```python
class PresentationPromptTests(unittest.TestCase):
    def test_prompt_contains_approved_generation_pipeline(self) -> None:
        prompt = PRESENTATION_PLANNER_SYSTEM_PROMPT
        required = [
            "/skills/pptx/SKILL.md",
            "/workspace/input/",
            "/workspace/work/",
            "markitdown",
            "validate.py",
            "soffice.py",
            "pdftoppm",
            "150 DPI",
            "最多 3 轮",
            "save_output",
        ]
        for text in required:
            self.assertIn(text, prompt)

    def test_prompt_forbids_generation_before_outline_tool_success(self) -> None:
        self.assertIn(
            "submit_outline 成功执行前不得生成或修改 PPTX",
            PRESENTATION_PLANNER_SYSTEM_PROMPT,
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m unittest tests.test_prompt -v`

Expected: FAIL，当前提示词没有审批后生成步骤。

- [ ] **Step 3: 重写系统提示词为两个阶段**

提示词必须逐项写明：审批前只收集需求和提交大纲；`submit_outline` 成功表示已批准；审批后读取完整 Skill；输入只作为原件；所有工作写入 `/workspace/work/`；按内容检查、基础校验、模板 `--original` 校验、PDF、150 DPI JPEG、全部页面 `view` 的顺序执行；视觉迭代 1 至 3 轮；删除或改名无效 `.pptx`；最终调用 `save_output`。

- [ ] **Step 4: 更新大纲工具成功文本并增加直接测试**

```python
def test_submit_outline_success_marks_approval(self) -> None:
    result = submit_outline.func(
        title="Demo",
        markdown="# Demo",
        slides=[SlideOutline(index=1, title="One", key_points=["A", "B"], markdown="## One")],
    )
    self.assertEqual(result, "大纲已批准，可以开始生成演示文稿。")
```

- [ ] **Step 5: 运行 Task 5 测试**

Run:

```bash
uv run python -m unittest tests.test_prompt -v
uv run ruff check agent/prompts/presentation_planner.py agent/tools/outline.py tests/test_prompt.py
```

Expected: 全部通过。

- [ ] **Step 6: 在获得用户提交授权后提交 Task 5**

```bash
git add agent/prompts/presentation_planner.py agent/tools/outline.py tests/test_prompt.py
git commit -m "feat: add approved PPTX generation workflow"
```

---

### Task 6: 图工厂接入 workspace、Skill 和保存工具

**Files:**
- Modify: `agent/agent.py:20-40`
- Modify: `tests/test_agent.py:29-59`

**Interfaces:**
- Consumes: `thread_workspace(thread_id) -> ThreadWorkspace`
- Consumes async: `initialize_thread_workspace(backend, workspace) -> None`
- Consumes: `create_save_output_tool(backend, workspace) -> BaseTool`
- Produces Deep Agent tools: `submit_outline`, `view`, `save_output`
- Produces Deep Agent skills: `skills=["/skills/"]`

- [ ] **Step 1: 扩展图接线失败测试**

```python
async def test_graph_syncs_workspace_loads_skill_and_registers_save_output(self) -> None:
    backend = Mock()
    workspace = Mock()
    save_output_tool = Mock(name="save_output")
    save_output_tool.name = "save_output"
    with (
        patch.object(agent_module, "get_thread_sandbox_backend", return_value=backend),
        patch.object(agent_module, "thread_workspace", return_value=workspace),
        patch.object(agent_module, "initialize_thread_workspace", AsyncMock()) as initialize,
        patch.object(agent_module, "create_save_output_tool", return_value=save_output_tool),
        patch.object(agent_module, "create_deep_agent", return_value=object()) as create_agent,
        patch.object(agent_module, "create_view_tool") as create_view,
    ):
        create_view.return_value.name = "view"
        await agent_module.graph({"configurable": {"thread_id": THREAD_ID}})

    initialize.assert_awaited_once_with(backend, workspace)
    self.assertEqual(create_agent.call_args.kwargs["skills"], ["/skills/"])
    self.assertEqual(
        [tool.name for tool in create_agent.call_args.kwargs["tools"]],
        ["submit_outline", "view", "save_output"],
    )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m unittest tests.test_agent -v`

Expected: FAIL，缺少 workspace 和 `save_output` 接线。

- [ ] **Step 3: 实现图工厂接线**

```python
workspace = thread_workspace(str(thread_id))
backend = await asyncio.to_thread(get_thread_sandbox_backend, str(thread_id))
await initialize_thread_workspace(backend, workspace)
view_tool = create_view_tool(backend, qwen_model)
save_output_tool = create_save_output_tool(backend, workspace)
return create_deep_agent(
    model=deepseek_model,
    tools=[submit_outline, view_tool, save_output_tool],
    skills=["/skills/"],
    system_prompt=PRESENTATION_PLANNER_SYSTEM_PROMPT,
    backend=backend,
    interrupt_on={"submit_outline": _OUTLINE_INTERRUPT_CONFIG},
    name="ppt_agent",
)
```

- [ ] **Step 4: 增加上传失败时不创建 Agent 的测试**

`initialize_thread_workspace` 抛出 `RuntimeError` 后，断言 `create_deep_agent`、`create_view_tool` 和 `create_save_output_tool` 均未调用。

- [ ] **Step 5: 运行 Task 6 和完整本地回归**

Run:

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run langgraph validate
git diff --check
```

Expected: 全部通过；PPTX Skill 仍被 Ruff 排除且 `git diff -- agent/skills/pptx` 无输出。

- [ ] **Step 6: 在获得用户提交授权后提交 Task 6**

```bash
git add agent/agent.py tests/test_agent.py
git commit -m "feat: load PPTX skill in thread sandbox"
```

---

### Task 7: 真实双模型 PPTX E2E Smoke

**Files:**
- Create: `scripts/smoke_pptx_e2e.py`
- Create: `tests/test_smoke_pptx_e2e.py`

**Interfaces:**
- Consumes: `thread_workspace()`、`sandbox_name_for_thread()`、Agent Server assistant `ppt_agent`
- Produces CLI: `uv run python -m scripts.smoke_pptx_e2e`
- Produces helpers: `_validate_interrupt(result)`, `_tool_calls(messages, name)`, `_validate_editable_pptx(path)`, `_latest_output(workspace)`

- [ ] **Step 1: 写中断、工具次数和可编辑 OOXML 失败测试**

```python
class PptxE2EHelpersTests(unittest.TestCase):
    def test_requires_one_to_three_post_approval_view_calls(self) -> None:
        messages = make_messages_with_tool_calls("view", "view", "save_output")
        calls = _tool_calls(messages, "view")
        self.assertEqual(len(calls), 2)

    def test_rejects_flattened_full_slide_image_deck(self) -> None:
        path = make_flattened_three_slide_pptx(self.temp_path)
        with self.assertRaisesRegex(RuntimeError, "editable"):
            _validate_editable_pptx(path, required_text="可编辑演示")
```

测试 fixture 使用标准库 `zipfile` 写最小 OOXML entries，不依赖 PowerPoint 或 LibreOffice。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m unittest tests.test_smoke_pptx_e2e -v`

Expected: FAIL，无法导入 E2E helper。

- [ ] **Step 3: 实现消息和产物检查 helpers**

`_validate_editable_pptx()` 必须：

- ZIP 可打开；
- 存在 `[Content_Types].xml`、`ppt/presentation.xml` 和三个 `ppt/slides/slideN.xml`；
- 合并 XML 包含固定请求文本 `可编辑演示`；
- 至少一个 slide XML 含 `<p:sp>` 或 `<c:chart>`；
- 不允许每页只包含单个覆盖整页的图片关系而没有文字/shape。

- [ ] **Step 4: 实现真实 E2E 主流程**

脚本执行：

1. 要求 `LANGSMITH_TRACING=true`。
2. 创建 Agent Server Thread 和 `ThreadWorkspace`。
3. 写入 `input/nested/brief.txt` 与 `work/local-only.txt`。
4. 预先创建同一 Thread Sandbox，并上传 `/workspace/work/remote-only.txt`。
5. 用 metadata `{"smoke_id": smoke_id}` 发起 Run，要求固定三页、包含文本“可编辑演示”和至少一个原生图表。
6. 验证唯一 `submit_outline` 中断和三种决策。
7. 用 `Command(resume={"decisions": [{"type": "approve"}]})` 恢复。
8. 验证不存在第二次中断。
9. 从最终消息确认 `view` 成功调用 1 至 3 次、最后存在成功 `save_output` ToolMessage。
10. 检查本机 `work/` 同时存在两个标记、源码、JPEG 和 PPTX。
11. 检查最新时间戳输出中的全部 PPTX，并运行 `_validate_editable_pptx()`。
12. 查询 LangSmith project 中 `metadata.smoke_id == smoke_id` 的 trace，确认 DeepSeek、Qwen、`view`、`save_output` 子运行存在。
13. `finally` 删除 Agent Server Thread 和远程 Sandbox；保留本机目录。

- [ ] **Step 5: 写 cleanup 和错误诊断测试**

模拟 Run 失败，断言 Thread 与 Sandbox 删除均尝试执行；主异常附加 cleanup 错误；最终异常文本包含 `thread_id`、Sandbox 名称、`smoke_id` 和本机目录。

- [ ] **Step 6: 运行 Task 7 本地 helper 测试**

Run:

```bash
uv run python -m unittest tests.test_smoke_pptx_e2e -v
uv run ruff check scripts/smoke_pptx_e2e.py tests/test_smoke_pptx_e2e.py
git diff --check
```

Expected: 全部通过；此步骤不调用真实模型。

- [ ] **Step 7: 在获得用户提交授权后提交 Task 7**

```bash
git add scripts/smoke_pptx_e2e.py tests/test_smoke_pptx_e2e.py
git commit -m "test: add real PPTX generation smoke"
```

---

### Task 8: 构建真实 Snapshot 并完成端到端验收

**Files:**
- Modify: `docs/superpowers/specs/2026-08-19-pptx-sandbox-pipeline-design.md`
- Modify: `docs/superpowers/plans/2026-08-19-pptx-sandbox-pipeline.md`

**Interfaces:**
- Consumes: Tasks 1-7 的全部接口
- Produces: ready snapshot `ppt-deepagent-pptx-v1` 或用户明确选择的后续版本名
- Produces: 本机 `workspace/<smoke-thread-id>/work/` 和 `output/<timestamp>/` 验收产物

- [ ] **Step 1: 运行完整本地验证**

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run langgraph validate
git diff --check
git diff -- agent/skills/pptx
```

Expected: 测试、Ruff、LangGraph 配置与 diff 检查通过；最后一条无输出。

- [ ] **Step 2: 检查 Clash 与 LangSmith 配置**

```bash
curl --proxy http://127.0.0.1:7897 --silent --output /dev/null --write-out '%{http_code}\n' https://registry-1.docker.io/v2/
uv run python -c 'from agent.settings import langsmith_settings, sandbox_settings; assert langsmith_settings.api_key; assert sandbox_settings.snapshot_name; print(sandbox_settings.snapshot_name)'
```

Expected: 第一条打印 `401`，证明代理连通；第二条打印非空 snapshot 名称。Clash 未运行时停止，不尝试直连被墙域名。

- [ ] **Step 3: 构建真实 LangSmith snapshot**

```bash
uv run python -m scripts.sandbox_snapshot build --name ppt-deepagent-pptx-v1
```

Expected: 输出 ready snapshot ID、名称、镜像 digest 和文件系统容量。同名已存在时不覆盖：先运行 verify；只有现有内容不符合当前 Dockerfile 时才经用户确认改用 `ppt-deepagent-pptx-v2`。

- [ ] **Step 4: 验证真实 snapshot 工具链**

```bash
uv run python -m scripts.sandbox_snapshot verify --name ppt-deepagent-pptx-v1
```

Expected: Skill、npm/Python 依赖、PptxGenJS、markitdown、thumbnail、add/clean、`--original` validate、LibreOffice 和 Poppler 全部通过；临时 Sandbox 已删除。

- [ ] **Step 5: 启动 Agent Server**

Run in a persistent terminal:

```bash
uv run langgraph dev --no-browser --no-reload --port 2024
```

Expected: `ppt_agent` 图成功加载，服务监听 `http://127.0.0.1:2024`。

- [ ] **Step 6: 运行真实双模型 E2E**

```bash
uv run python -m scripts.smoke_pptx_e2e
```

Expected: 一个大纲中断被批准；DeepSeek 生成三页 PPTX；Qwen 检查 1 至 3 轮；`save_output` 成功；脚本打印 LangSmith trace ID、本机 `work/` 与时间戳输出路径。

- [ ] **Step 7: 检查最终产物和远程资源清理**

```bash
uv run python -c 'from pathlib import Path; files=sorted(Path("workspace").glob("*/output/*/**/*.pptx")); assert files, "no timestamped PPTX output"; print("\n".join(map(str, files)))'
```

Expected: 打印至少一个时间戳目录中的 PPTX。E2E 脚本自身在删除后再次查询临时 Sandbox，并在仍存在时失败；不得删除本机 smoke 产物。

- [ ] **Step 8: 更新规格和计划状态**

把规格状态改为“已实施并通过真实 E2E”，在规格末尾记录 snapshot 名称、验证日期、E2E trace ID 和本机产物路径；把本计划已完成步骤勾选。不得修改用户已有的根目录架构文档变更。

- [ ] **Step 9: 再次运行完成前验证**

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run langgraph validate
git diff --check
git status --short
```

Expected: 所有验证通过；状态只包含本计划文件和用户原有的根目录架构文档变更。

- [ ] **Step 10: 在获得用户提交授权后提交 Task 8 文档状态**

```bash
git add docs/superpowers/specs/2026-08-19-pptx-sandbox-pipeline-design.md docs/superpowers/plans/2026-08-19-pptx-sandbox-pipeline.md
git commit -m "docs: record PPTX sandbox E2E verification"
```
