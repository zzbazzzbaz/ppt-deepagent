# Sandbox 镜像与 Snapshot 自动同步实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定 Node 22 与 npm 依赖，由 GitHub Actions 仅在 Sandbox 构建上下文变化时构建双 tag GHCR 镜像，并以候选验证、latest 切换和失败回滚的方式自动同步 LangSmith Snapshot。

**Architecture:** Dockerfile 从固定 digest 的官方 Node 22 Bookworm Slim 阶段复制 node/npm 到固定 digest 的 Python 3.13 最终镜像，并继续用 `package-lock.json` + `npm ci` 保证依赖可复现。Workflow 推送 UTC 时间戳与 latest 镜像 tag，把 Buildx digest 交给 `sandbox_snapshot.py sync-image`；脚本先验证临时 candidate，再替换唯一 latest Snapshot，并在切换失败时从旧 immutable 镜像恢复。

**Tech Stack:** Python 3.13、pytest、LangSmith Sandbox 0.11.0、GitHub Actions、Docker Buildx、GHCR、Node 22 Bookworm Slim、npm、PptxGenJS、Sharp。

## Global Constraints

- Node 基础阶段固定为 `node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436`。
- Python 最终阶段保持 `python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1`。
- `sandbox/package-lock.json` 必须保留，镜像安装必须使用 `npm ci`。
- GHCR 仓库保持 `ghcr.io/zzbazzzbaz/ppt-deepagent`。
- 镜像 tag 固定为 `ppt-deepagent-sandbox-<UTC YYYYMMDD-HHmmss>` 与 `ppt-deepagent-sandbox-latest`。
- GHCR 时间戳镜像永久保留。
- LangSmith 最终只保留 `ppt-deepagent-sandbox-latest`；candidate 仅在流水线执行期间存在。
- Snapshot 文件系统容量固定为 `2 * 1024**3` bytes。
- Thread Sandbox 与 verify Sandbox 内存固定为 `2 * 1024**3` bytes。
- Workflow 只使用 GitHub Secret `LANGSMITH_API_KEY`，不得读取或打印本机 `.env`。
- 所有项目命令使用 `uv run`；npm 锁文件更新使用 Clash 代理 `http://127.0.0.1:7897`。
- 不提交 `workspace/` 下的 E2E 产物。
- 每个提交步骤只在用户已授权实施和提交时执行，且只暂存该任务列出的文件。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `sandbox/Dockerfile` | 从固定 Node 22 阶段复制运行时，并构建最终 Python/PPTX 镜像 |
| `sandbox/package.json` | 固定 PptxGenJS、React、图标和 Sharp 顶层版本 |
| `sandbox/package-lock.json` | 固定全部 npm 传递依赖和跨平台 optional dependency 元数据 |
| `agent/settings.py` | 暴露 2 GiB Thread Sandbox 内存配置 |
| `agent/sandbox.py` | 创建 Thread Sandbox 时传入内存限制 |
| `.env.example` | 记录 latest Snapshot 名称和 2 GiB 内存配置 |
| `scripts/sandbox_snapshot.py` | Snapshot 创建、candidate 验证、latest 切换、回滚和独立 LangSmith 凭据加载 |
| `.github/workflows/build-sandbox-image.yml` | 按路径触发构建、推送双 tag、传递 digest 并执行 `sync-image` |
| `tests/test_sandbox_snapshot.py` | Dockerfile、容量、verify 内存、同步状态机单元测试 |
| `tests/test_sandbox.py` | Thread Sandbox 2 GiB 内存调用契约测试 |
| `tests/test_sandbox_workflow.py` | Workflow 触发、命名、并发、Secret 和同步调用静态契约测试 |

---

### Task 1: 固定 Node 22 与 npm 锁文件

**Files:**
- Modify: `sandbox/Dockerfile:1-42`
- Modify: `sandbox/package.json:5-11`
- Modify: `sandbox/package-lock.json`
- Modify: `tests/test_sandbox_snapshot.py:20-29`

**Interfaces:**
- Produces runtime: `node --version` reports Node 22; `npm --version` is available.
- Preserves runtime: `/opt/pptx/node_modules` and `/workspace/node_modules` resolve all required packages.
- Preserves build command: `npm ci --omit=dev --ignore-scripts` followed by `npm rebuild sharp`.

- [x] **Step 1: Replace the current Docker/package assertions with failing Node 22 assertions**

Update `tests/test_sandbox_snapshot.py`:

```python
def test_dockerfile_uses_pinned_node_22_runtime() -> None:
    dockerfile = (PROJECT_ROOT / "sandbox" / "Dockerfile").read_text()

    assert (
        "FROM node:22-bookworm-slim@sha256:"
        "d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 "
        "AS node-runtime"
    ) in dockerfile
    assert "COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node" in dockerfile
    assert "COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules" in dockerfile
    assert "\n    nodejs \\" not in dockerfile
    assert "\n    npm \\" not in dockerfile
    assert "npm ci --omit=dev --ignore-scripts" in dockerfile
    assert "ln -s /opt/pptx/node_modules /workspace/node_modules" in dockerfile


def test_package_and_lock_pin_node_22_compatible_sharp() -> None:
    package = json.loads((PROJECT_ROOT / "sandbox" / "package.json").read_text())
    lock = json.loads((PROJECT_ROOT / "sandbox" / "package-lock.json").read_text())

    assert package["dependencies"]["sharp"] == "0.35.3"
    assert lock["packages"][""]["dependencies"]["sharp"] == "0.35.3"
    assert lock["packages"]["node_modules/sharp"]["version"] == "0.35.3"
```

- [x] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_sandbox_snapshot.py::test_dockerfile_uses_pinned_node_22_runtime tests/test_sandbox_snapshot.py::test_package_and_lock_pin_node_22_compatible_sharp -q
```

Expected: FAIL because the Dockerfile installs Debian Node 18 and Sharp is `0.33.5`.

- [x] **Step 3: Implement the pinned Node 22 multi-stage runtime**

Make the beginning and Node-copy portion of `sandbox/Dockerfile` exactly follow this structure:

```dockerfile
FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS node-runtime

FROM python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_PATH=/opt/pptx/node_modules \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fontconfig \
    fonts-crosextra-caladea \
    fonts-crosextra-carlito \
    fonts-liberation \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-noto-core \
    gcc \
    libreoffice-common \
    libreoffice-impress \
    poppler-utils \
    unzip \
    zip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
```

Keep the Python requirements, npm install, Skill copy, `/workspace/node_modules` link and final workdir blocks unchanged.

- [x] **Step 4: Update Sharp and regenerate the lockfile through the configured proxy**

Change `sandbox/package.json` to:

```json
"sharp": "0.35.3"
```

Run:

```bash
npm_config_proxy=http://127.0.0.1:7897 npm_config_https_proxy=http://127.0.0.1:7897 npm install --package-lock-only --ignore-scripts --prefix sandbox
```

Expected: exit 0; root and `node_modules/sharp` lock entries both report `0.35.3`.

- [x] **Step 5: Run focused tests and lockfile integrity checks**

Run:

```bash
uv run pytest tests/test_sandbox_snapshot.py -q
uv run ruff check tests/test_sandbox_snapshot.py
npm ci --package-lock-only --ignore-scripts --prefix sandbox
git diff --check
```

Expected: tests and Ruff pass; npm reports package and lock are in sync.

- [x] **Step 6: Commit the runtime update**

```bash
git add sandbox/Dockerfile sandbox/package.json sandbox/package-lock.json tests/test_sandbox_snapshot.py
git commit -m "build: pin sandbox Node runtime"
```

---

### Task 2: 固定 Snapshot 容量与运行 Sandbox 内存

**Files:**
- Modify: `agent/settings.py:41-51`
- Modify: `agent/sandbox.py:40-45`
- Modify: `.env.example:15-18`
- Modify: `scripts/sandbox_snapshot.py:22-24,107-114,182-206`
- Modify: `tests/test_sandbox.py:11-85`
- Modify: `tests/test_sandbox_snapshot.py:108-203`

**Interfaces:**
- Produces config: `SandboxSettings.mem_bytes: int` from `SANDBOX_MEM_BYTES`.
- Produces constant: `_FS_CAPACITY_BYTES = 2 * 1024**3`.
- Produces constant: `_SANDBOX_MEM_BYTES = 2 * 1024**3`.
- Changes `verify_snapshot(client: SandboxClient, name: str) -> None` to create verify Sandbox with `mem_bytes=_SANDBOX_MEM_BYTES`.

- [x] **Step 1: Write failing Thread Sandbox memory tests**

Add `"SANDBOX_MEM_BYTES": str(2 * 1024**3)` to `_TEST_ENV` and `mem_bytes=2 * 1024**3` to the `sandbox_settings` stub in `tests/test_sandbox.py`. Update the creation assertion:

```python
client.create_sandbox.assert_called_once_with(
    snapshot_id="snapshot-id",
    name=sandbox_module.sandbox_name_for_thread(THREAD_ID),
    idle_ttl_seconds=60,
    delete_after_stop_seconds=60,
    mem_bytes=2 * 1024**3,
)
```

- [x] **Step 2: Write failing 2 GiB Snapshot and verify Sandbox tests**

Update capacity assertions in `tests/test_sandbox_snapshot.py` from `8 * 1024**3` to `2 * 1024**3`. Extend the verify assertion:

```python
client.create_sandbox.assert_called_once_with(
    snapshot_id="snapshot-id",
    name=client.create_sandbox.call_args.kwargs["name"],
    wait_for_ready=True,
    mem_bytes=2 * 1024**3,
)
```

- [x] **Step 3: Run the focused tests and confirm resource assertions fail**

Run:

```bash
uv run pytest tests/test_sandbox.py tests/test_sandbox_snapshot.py -q
```

Expected: FAIL because `mem_bytes` is absent and snapshot capacity remains 8 GiB.

- [x] **Step 4: Implement the resource configuration**

Add to `SandboxSettings`:

```python
mem_bytes: int = Field(
    default=2 * 1024**3,
    ge=2 * 1024**3,
    le=2 * 1024**3,
)
```

Pass it from `agent/sandbox.py`:

```python
sandbox = _client.create_sandbox(
    snapshot_id=snapshot.id,
    name=name,
    idle_ttl_seconds=sandbox_settings.idle_ttl_seconds,
    delete_after_stop_seconds=sandbox_settings.delete_after_stop_seconds,
    mem_bytes=sandbox_settings.mem_bytes,
)
```

Update script constants and verify creation:

```python
_FS_CAPACITY_BYTES = 2 * 1024**3
_SANDBOX_MEM_BYTES = 2 * 1024**3

sandbox = client.create_sandbox(
    snapshot_id=snapshot.id,
    name=sandbox_name,
    wait_for_ready=True,
    mem_bytes=_SANDBOX_MEM_BYTES,
)
```

Update `.env.example`:

```text
SANDBOX_SNAPSHOT_NAME=ppt-deepagent-sandbox-latest
SANDBOX_MEM_BYTES=2147483648
```

- [x] **Step 5: Run resource tests and static checks**

Run:

```bash
uv run pytest tests/test_sandbox.py tests/test_sandbox_snapshot.py -q
uv run ruff check agent/settings.py agent/sandbox.py scripts/sandbox_snapshot.py tests/test_sandbox.py tests/test_sandbox_snapshot.py
uv run langgraph validate
git diff --check
```

Expected: all tests pass and LangGraph finds one graph.

- [x] **Step 6: Commit the resource limits**

```bash
git add .env.example agent/settings.py agent/sandbox.py scripts/sandbox_snapshot.py tests/test_sandbox.py tests/test_sandbox_snapshot.py
git commit -m "feat: limit sandbox runtime resources"
```

---

### Task 3: 增加独立凭据加载与 Snapshot 同步基础

**Files:**
- Modify: `scripts/sandbox_snapshot.py:1-27,182-247`
- Modify: `tests/test_sandbox_snapshot.py`

**Interfaces:**
- Produces dataclass: `SnapshotSyncResult(action: Literal["created", "updated", "skipped"], snapshot: Snapshot)`.
- Produces: `_find_snapshot(client: SandboxClient, name: str) -> Snapshot | None`.
- Produces: `_snapshot_api_key(*, _env_file: str | None = ".env") -> str` reading only `LANGSMITH_API_KEY` from environment or `.env`.
- Produces: `sync_snapshot_from_image(client: SandboxClient, latest_name: str, candidate_name: str, docker_image: str, image_digest: str) -> SnapshotSyncResult`.
- Preserves: `build`, `from-image`, and `verify` CLI commands.

- [x] **Step 1: Write failing exact lookup, credential isolation, skip and first-create tests**

Add imports and tests to `tests/test_sandbox_snapshot.py`:

```python
from scripts.sandbox_snapshot import (
    SnapshotSyncResult,
    _find_snapshot,
    _snapshot_api_key,
    sync_snapshot_from_image,
)


def test_find_snapshot_requires_at_most_one_exact_name() -> None:
    client = Mock()
    client.list_snapshots.return_value = [
        SimpleNamespace(id="one", name="latest", status="ready"),
        SimpleNamespace(id="two", name="latest", status="ready"),
    ]

    with pytest.raises(RuntimeError, match="Expected at most one"):
        _find_snapshot(client, "latest")


def test_snapshot_api_key_reads_only_langsmith_configuration() -> None:
    with patch.dict("os.environ", {"LANGSMITH_API_KEY": "ci-key"}, clear=True):
        assert _snapshot_api_key(_env_file=None) == "ci-key"


def test_sync_skips_when_latest_digest_matches() -> None:
    existing = SimpleNamespace(
        id="latest-id",
        name="ppt-deepagent-sandbox-latest",
        status="ready",
        image_digest="sha256:new",
        docker_image="ghcr.io/zzbazzzbaz/ppt-deepagent:old",
    )
    client = Mock()
    client.list_snapshots.return_value = [existing]

    result = sync_snapshot_from_image(
        client,
        latest_name=existing.name,
        candidate_name="candidate",
        docker_image="ghcr.io/zzbazzzbaz/ppt-deepagent:ppt-deepagent-sandbox-20260820-010203",
        image_digest="sha256:new",
    )

    assert result == SnapshotSyncResult(action="skipped", snapshot=existing)
    client.create_snapshot.assert_not_called()
    client.delete_snapshot.assert_not_called()
```

For first-create behavior, mock `create_snapshot` to return candidate then latest, patch `verify_snapshot`, and assert result action is `created`, latest uses the timestamp image, and candidate is deleted only after latest verification.

- [x] **Step 2: Run the new tests and confirm imports or behavior fail**

Run:

```bash
uv run pytest tests/test_sandbox_snapshot.py -q
```

Expected: FAIL because the sync result, lookup, minimal credential loader and sync function do not exist.

- [x] **Step 3: Implement the sync result, exact lookup and isolated credential loader**

Add:

```python
import os
from dataclasses import dataclass
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class SnapshotSyncResult:
    action: Literal["created", "updated", "skipped"]
    snapshot: Snapshot


class _SnapshotCliSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LANGSMITH_", extra="ignore"
    )
    api_key: str


def _snapshot_api_key(*, _env_file: str | None = ".env") -> str:
    return _SnapshotCliSettings(_env_file=_env_file).api_key


def _find_snapshot(client: SandboxClient, name: str) -> Snapshot | None:
    matches = [
        snapshot
        for snapshot in client.list_snapshots(name_contains=name)
        if snapshot.name == name
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Expected at most one snapshot named {name!r}, found {len(matches)}"
        )
    return matches[0] if matches else None
```

Remove the unused `os` import if the final implementation does not reference it directly.

- [x] **Step 4: Implement digest skip and candidate-first creation**

Implement `sync_snapshot_from_image()` with this ordering:

```python
existing = _find_snapshot(client, latest_name)
if existing is not None and existing.status == "ready" and existing.image_digest == image_digest:
    return SnapshotSyncResult("skipped", existing)

if _find_snapshot(client, candidate_name) is not None:
    raise RuntimeError(f"Candidate snapshot already exists: {candidate_name!r}")

candidate = create_snapshot_from_image(client, candidate_name, docker_image)
try:
    verify_snapshot(client, candidate_name)
except Exception:
    client.delete_snapshot(candidate.id)
    raise

# The latest cutover and rollback body is completed in Task 4.
```

For the first-create path, create latest from the same immutable timestamp image, verify it, delete candidate, and return `SnapshotSyncResult("created", latest)`.

- [x] **Step 5: Extend the CLI parser with `sync-image`**

Add command choice and arguments:

```python
parser.add_argument(
    "command", choices=("build", "verify", "from-image", "sync-image")
)
parser.add_argument("--candidate-name")
parser.add_argument("--image-digest")
```

For `sync-image`, require `--docker-image`, `--candidate-name`, and `--image-digest`, call `sync_snapshot_from_image()`, and print action, latest name, snapshot ID, source image and digest. Instantiate the client with:

```python
client = SandboxClient(api_key=_snapshot_api_key())
```

Do not import `agent.settings` in `main()`.

- [x] **Step 6: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/test_sandbox_snapshot.py -q
uv run ruff check scripts/sandbox_snapshot.py tests/test_sandbox_snapshot.py
git diff --check
```

Expected: skip and first-create tests pass; existing commands remain covered.

- [x] **Step 7: Commit the sync foundation**

```bash
git add scripts/sandbox_snapshot.py tests/test_sandbox_snapshot.py
git commit -m "feat: add snapshot image synchronization"
```

---

### Task 4: 实现 latest 切换与失败回滚

**Files:**
- Modify: `scripts/sandbox_snapshot.py`
- Modify: `tests/test_sandbox_snapshot.py`

**Interfaces:**
- Completes: `sync_snapshot_from_image(...) -> SnapshotSyncResult` for existing latest replacement.
- Preserves: old latest until candidate verification succeeds.
- Guarantees: successful run leaves only latest; candidate verification failure leaves old latest unchanged; cutover failure restores old latest when an old immutable image exists.

- [x] **Step 1: Write failing successful replacement test**

Create a ready old latest with `docker_image="ghcr.io/zzbazzzbaz/ppt-deepagent:ppt-deepagent-sandbox-20260819-010203"` and different digest. Mock candidate and new latest creation. Assert exact call order:

```python
assert events == [
    "create:candidate",
    "verify:candidate",
    "delete:old-latest-id",
    "create:ppt-deepagent-sandbox-latest",
    "verify:ppt-deepagent-sandbox-latest",
    "delete:candidate-id",
]
assert result.action == "updated"
```

- [x] **Step 2: Write failing candidate failure isolation test**

Patch `verify_snapshot` to fail for candidate. Assert candidate is deleted, old latest ID is never deleted, and latest is never recreated.

- [x] **Step 3: Write failing cutover rollback tests**

Simulate candidate success, old latest deletion, and new latest creation failure. Assert the script recreates latest from the old timestamp image, verifies rollback, deletes candidate, and raises the original release failure. Add a second test where rollback also fails; assert candidate is retained and the raised error contains both release and rollback failure text.

- [x] **Step 4: Run rollback tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_sandbox_snapshot.py -q
```

Expected: FAIL because existing latest replacement and rollback are not implemented.

- [x] **Step 5: Implement replacement and rollback**

After candidate verification:

```python
old_image = existing.docker_image if existing is not None else None
if existing is not None:
    client.delete_snapshot(existing.id)

try:
    latest = create_snapshot_from_image(client, latest_name, docker_image)
    verify_snapshot(client, latest_name)
except Exception as release_error:
    failed_latest = _find_snapshot(client, latest_name)
    if failed_latest is not None:
        client.delete_snapshot(failed_latest.id)
    if old_image is None:
        release_error.add_note(f"Validated candidate retained: {candidate.name}")
        raise
    try:
        rollback = create_snapshot_from_image(client, latest_name, old_image)
        verify_snapshot(client, latest_name)
    except Exception as rollback_error:
        raise RuntimeError(
            f"Snapshot release failed: {release_error}; rollback failed: {rollback_error}; "
            f"candidate retained: {candidate.name}"
        ) from rollback_error
    client.delete_snapshot(candidate.id)
    raise
else:
    client.delete_snapshot(candidate.id)
    return SnapshotSyncResult("updated" if existing else "created", latest)
```

Use helper functions to record or assert call order in tests; do not add logging callbacks or a general event framework to production code.

- [x] **Step 6: Run all Snapshot tests and static checks**

Run:

```bash
uv run pytest tests/test_sandbox_snapshot.py -q
uv run ruff check scripts/sandbox_snapshot.py tests/test_sandbox_snapshot.py
git diff --check
```

Expected: all candidate, replacement and rollback branches pass.

- [x] **Step 7: Commit the cutover state machine**

```bash
git add scripts/sandbox_snapshot.py tests/test_sandbox_snapshot.py
git commit -m "feat: safely replace latest snapshot"
```

---

### Task 5: 条件构建与 Snapshot 同步 Workflow

**Files:**
- Modify: `.github/workflows/build-sandbox-image.yml`
- Create: `tests/test_sandbox_workflow.py`

**Interfaces:**
- Consumes: CLI `sync-image --name --candidate-name --docker-image --image-digest`.
- Consumes Secret: `LANGSMITH_API_KEY`.
- Produces image tags: `ppt-deepagent-sandbox-<timestamp>` and `ppt-deepagent-sandbox-latest`.
- Produces concurrency group: `ppt-deepagent-sandbox-snapshot-sync` with `cancel-in-progress: false`.

- [x] **Step 1: Write failing workflow contract tests**

Create `tests/test_sandbox_workflow.py`:

```python
from pathlib import Path


WORKFLOW = Path(".github/workflows/build-sandbox-image.yml")


def test_workflow_only_auto_runs_for_sandbox_context_changes() -> None:
    text = WORKFLOW.read_text()
    for required in (
        "push:",
        "branches: [main]",
        "sandbox/**",
        "agent/skills/pptx/**",
        ".github/workflows/build-sandbox-image.yml",
        "scripts/sandbox_snapshot.py",
        "workflow_dispatch:",
    ):
        assert required in text


def test_workflow_serializes_latest_snapshot_updates() -> None:
    text = WORKFLOW.read_text()
    assert "group: ppt-deepagent-sandbox-snapshot-sync" in text
    assert "cancel-in-progress: false" in text


def test_workflow_pushes_dual_tags_and_syncs_digest() -> None:
    text = WORKFLOW.read_text()
    for required in (
        "ppt-deepagent-sandbox-${{ steps.metadata.outputs.timestamp }}",
        "ppt-deepagent-sandbox-latest",
        "id: build",
        "${{ steps.build.outputs.digest }}",
        "python -m scripts.sandbox_snapshot sync-image",
        "--name ppt-deepagent-sandbox-latest",
        "--candidate-name ppt-deepagent-sandbox-candidate-${{ steps.metadata.outputs.timestamp }}",
        "LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}",
    ):
        assert required in text
```

- [x] **Step 2: Run workflow tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_sandbox_workflow.py -q
```

Expected: FAIL because the current workflow is manual-only and accepts a free-form version input.

- [x] **Step 3: Add push paths and concurrency**

Use this trigger/concurrency structure:

```yaml
on:
  push:
    branches: [main]
    paths:
      - "sandbox/**"
      - "agent/skills/pptx/**"
      - ".github/workflows/build-sandbox-image.yml"
      - "scripts/sandbox_snapshot.py"
  workflow_dispatch:

concurrency:
  group: ppt-deepagent-sandbox-snapshot-sync
  cancel-in-progress: false
```

Remove the `version` workflow input.

- [x] **Step 4: Generate timestamp metadata and dual image tags**

Add after checkout:

```yaml
- name: Generate image metadata
  id: metadata
  shell: bash
  run: |
    timestamp="$(date -u +%Y%m%d-%H%M%S)"
    image="${REGISTRY}/${IMAGE_NAME}"
    echo "timestamp=${timestamp}" >> "$GITHUB_OUTPUT"
    echo "timestamp_image=${image}:ppt-deepagent-sandbox-${timestamp}" >> "$GITHUB_OUTPUT"
```

Give the build step `id: build` and use tags:

```yaml
tags: |
  ${{ steps.metadata.outputs.timestamp_image }}
  ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:ppt-deepagent-sandbox-latest
```

- [x] **Step 5: Install uv dependencies and call snapshot synchronization**

Add after image push:

```yaml
- name: Set up uv
  uses: astral-sh/setup-uv@v6
  with:
    enable-cache: true

- name: Install Python dependencies
  run: uv sync --frozen

- name: Synchronize LangSmith snapshot
  env:
    LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
  run: |
    uv run --frozen python -m scripts.sandbox_snapshot sync-image \
      --name ppt-deepagent-sandbox-latest \
      --candidate-name ppt-deepagent-sandbox-candidate-${{ steps.metadata.outputs.timestamp }} \
      --docker-image "${{ steps.metadata.outputs.timestamp_image }}" \
      --image-digest "${{ steps.build.outputs.digest }}"
```

- [x] **Step 6: Run workflow tests, full local regression and YAML whitespace checks**

Run:

```bash
uv run pytest tests/test_sandbox_workflow.py tests/test_sandbox_snapshot.py tests/test_sandbox.py -q
uv run pytest -q
uv run ruff check .
uv run langgraph validate
git diff --check
```

Expected: all tests pass; LangGraph validates one graph.

- [x] **Step 7: Commit the workflow**

```bash
git add .github/workflows/build-sandbox-image.yml tests/test_sandbox_workflow.py
git commit -m "ci: sync latest sandbox snapshot"
```

---

### Task 6: 真实流水线、迁移与端到端验收

**Files:**
- Modify after success: `docs/superpowers/specs/2026-08-20-sandbox-image-snapshot-ci-design.md`
- Modify after success: `docs/superpowers/plans/2026-08-20-sandbox-image-snapshot-ci.md`
- Local-only: `.env`

**Interfaces:**
- Consumes workflow: `Build Sandbox Image`.
- Produces GHCR tags: timestamp and latest.
- Produces ready Snapshot: `ppt-deepagent-sandbox-latest` with 2 GiB filesystem capacity.
- Produces local E2E PPTX under `workspace/<thread_id>/output/<timestamp>/`.

- [x] **Step 1: Verify GitHub Secret and push implementation commits**

Run:

```bash
gh secret list | grep '^LANGSMITH_API_KEY'
git status --short
git log --oneline -10
git push origin main
```

Expected: Secret exists; only untracked `workspace/` artifacts remain; push succeeds without force.

- [x] **Step 2: Observe the automatically triggered workflow**

Run:

```bash
gh run list --workflow build-sandbox-image.yml --limit 1
gh run watch "$(gh run list --workflow build-sandbox-image.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

Expected: build, push, candidate verify, latest switch and candidate cleanup all succeed.

- [x] **Step 3: Inspect workflow logs and assert immutable outputs**

Run:

```bash
run_id="$(gh run list --workflow build-sandbox-image.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run view "$run_id" --log
```

Confirm logs contain a UTC timestamp image, a `sha256:` digest, `ppt-deepagent-sandbox-latest`, a ready Snapshot ID and successful verification. Confirm logs do not contain the LangSmith API key.

- [x] **Step 4: Verify the latest Snapshot and 2 GiB resource contract from LangSmith**

Run:

```bash
uv run python -m scripts.sandbox_snapshot verify --name ppt-deepagent-sandbox-latest
uv run python - <<'PY'
from langsmith.sandbox import SandboxClient
from agent.settings import langsmith_settings
from agent.snapshot import find_ready_snapshot

client = SandboxClient(api_key=langsmith_settings.api_key)
try:
    snapshot = find_ready_snapshot(client, "ppt-deepagent-sandbox-latest")
    assert snapshot.fs_capacity_bytes == 2 * 1024**3
    print(snapshot.id, snapshot.name, snapshot.image_digest, snapshot.fs_capacity_bytes)
finally:
    client.close()
PY
```

Expected: full PPTX toolchain passes and capacity prints `2147483648`.

- [x] **Step 5: Update local runtime configuration and perform one-time legacy cleanup**

Change local `.env` without committing:

```text
SANDBOX_SNAPSHOT_NAME=ppt-deepagent-sandbox-latest
SANDBOX_MEM_BYTES=2147483648
```

After verifying the new latest Snapshot, delete the legacy exact-name Snapshot `ppt-deepagent-pptx-v1` through `SandboxClient.delete_snapshot()`. Refuse deletion unless exactly one matching legacy Snapshot exists and the new latest Snapshot is ready.

- [x] **Step 6: Run the real PPTX E2E smoke against a restarted server**

Start in a persistent terminal:

```bash
uv run langgraph dev --no-browser --no-reload --port 2024
```

Then run:

```bash
uv run python -m scripts.smoke_pptx_e2e
```

Expected: outline approval, three-page editable PPTX generation, 1-3 successful `view` calls, successful `save_output`, a LangSmith trace ID, and a local timestamped PPTX.

- [x] **Step 7: Verify final remote resource and local artifact state**

Run:

```bash
uv run python -c 'from pathlib import Path; files=sorted(Path("workspace").glob("*/output/*/**/*.pptx")); assert files; print("\n".join(map(str, files)))'
uv run pytest -q
uv run ruff check .
uv run langgraph validate
git diff --check
git status --short
```

Expected: at least one PPTX path prints; tests, Ruff and LangGraph pass; `git status` contains only expected document changes and untracked E2E workspace artifacts.

- [x] **Step 8: Record real acceptance evidence**

Update the design status to implemented and append:

- workflow run URL and validation date;
- timestamp image reference and digest;
- latest Snapshot ID;
- E2E LangSmith trace ID;
- local PPTX output path;
- confirmation that legacy/candidate Snapshots and verify Sandboxes were removed.

Mark completed plan checkboxes based on executed evidence; do not mark skipped or failed steps complete.

- [ ] **Step 9: Commit acceptance documentation after user authorization**

```bash
git add docs/superpowers/specs/2026-08-20-sandbox-image-snapshot-ci-design.md docs/superpowers/plans/2026-08-20-sandbox-image-snapshot-ci.md
git commit -m "docs: record sandbox snapshot CI acceptance"
```
