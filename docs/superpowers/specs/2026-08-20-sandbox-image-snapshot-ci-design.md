# Sandbox 镜像与 Snapshot 自动同步设计

## 状态

已实施，验收通过（2026-08-20）。

## 验收记录

- Workflow 运行：<https://github.com/zzbazzzbaz/ppt-deepagent/actions/runs/32320809357>（push 自动触发，9m34s，全部步骤成功）
- 时间戳镜像：`ghcr.io/zzbazzzbaz/ppt-deepagent:ppt-deepagent-sandbox-20260820-012314`
- immutable digest：`sha256:b3ec852eeb0ff306511820ddb882ebc3345dbda382bdc0ca70e5f5eaeaceb6dd`
- latest Snapshot：`ppt-deepagent-sandbox-latest`（ID `105284c8-3be4-4303-b6f3-67e7b42e64a4`，ready，fs 容量 2147483648 = 2 GiB）
- 同步动作：`created`（首次运行，无既有 latest）；本地完整验证矩阵复验通过
- E2E LangSmith trace：`01a01cd2-4e9f-7c81-bd7d-411206e1e00d`
- 本地 PPTX 产物：`workspace/01a01cd1-b2d6-7f03-97e8-b967ccdd471d/output/20260820-094212/editable-deck.pptx`
- 清理确认：遗留 Snapshot `ppt-deepagent-pptx-v1` 已删除（释放占用 Sandbox 后）；candidate Snapshot 已删除；所有 verify Sandbox 与 E2E thread Sandbox 均已删除；LangSmith 仅保留 `ppt-deepagent-sandbox-latest`；日志未泄露 API key（`LANGSMITH_API_KEY` 显示为 `***`）
- 本地回归：54 tests passed、`ruff check .` 通过、`langgraph validate` 1 graph

实施偏差（均已通过测试契约锁定）：

1. Task 5 workflow 双 tag 使用 `${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:ppt-deepagent-sandbox-${{ steps.metadata.outputs.timestamp }}` 表达式构造（而非计划 Step 4 示例的 `timestamp_image` 输出），以落实 Step 1 契约测试要求的字面量。
2. Task 4 追加 `test_sync_deletes_unverified_latest_before_rollback`，覆盖本设计"失败与回滚"中"删除失败的新 latest Snapshot"分支。


## 目标

将 PPTX Sandbox 的 Node 运行时、npm 依赖和镜像构建固定为可复现配置，并由 GitHub Actions 在构建上下文变化时自动构建 GHCR 镜像、验证候选 LangSmith Snapshot、切换 latest Snapshot。最终 LangSmith 只保留一个 latest Snapshot，GHCR 保留全部时间戳镜像用于追溯和回滚。

## 非目标

- 不删除 `sandbox/package-lock.json`。
- 不为普通 Agent 业务代码变更构建 Sandbox 镜像。
- 不在 GitHub Actions 中配置 DeepSeek、Qwen 或其他与 Snapshot 同步无关的密钥。
- 不长期保留 candidate 或时间戳 Snapshot。
- Snapshot 文件系统容量固定为 2 GiB。

## 命名

镜像仓库保持 `ghcr.io/zzbazzzbaz/ppt-deepagent`。每次构建使用 UTC 时间戳 `YYYYMMDD-HHmmss` 生成两个 tag：

- `ppt-deepagent-sandbox-<时间戳>`
- `ppt-deepagent-sandbox-latest`

时间戳镜像永久保留。latest tag 指向最近一次成功构建的 digest。

LangSmith 最终只保留：

- `ppt-deepagent-sandbox-latest`

流水线执行期间允许临时存在：

- `ppt-deepagent-sandbox-candidate-<时间戳>`

应用配置使用 `SANDBOX_SNAPSHOT_NAME=ppt-deepagent-sandbox-latest`。

## Docker 运行时

`sandbox/Dockerfile` 使用两个固定 digest 的官方基础镜像：

- Node 22 Bookworm Slim 构建阶段提供 node 与 npm。
- Python 3.13 Slim Bookworm 保持最终运行基础。

Node 运行文件从 Node 构建阶段复制到 Python 最终阶段。apt 安装列表删除 `nodejs` 和 `npm`，避免 Debian apt 仓库变化导致 Node 版本漂移。`package-lock.json` 必须保留，Dockerfile 继续使用 `npm ci`。

Sharp 更新为与 Node 22 兼容的固定版本，`package.json` 与 `package-lock.json` 必须一致。`/workspace/node_modules` 继续链接到 `/opt/pptx/node_modules`，避免 LangSmith 从镜像生成 Snapshot 时丢失 `NODE_PATH` 后无法解析 npm 模块。

## 资源配置

- Snapshot 文件系统容量：2 GiB。
- 所有 Thread Sandbox 内存：2 GiB。
- 所有 Snapshot verify 临时 Sandbox 内存：2 GiB。
- Thread Sandbox 的 vCPU 和文件系统配置继续使用 LangSmith provider 默认值，本次不新增覆盖。

Snapshot 本身不支持内存参数；2 GiB 通过 `SandboxClient.create_sandbox(mem_bytes=...)` 应用到运行中的 Sandbox。

## Workflow 触发

`.github/workflows/build-sandbox-image.yml` 支持两类触发：

1. `push` 到 `main` 且以下路径变化：
   - `sandbox/**`
   - `agent/skills/pptx/**`
   - `.github/workflows/build-sandbox-image.yml`
   - `scripts/sandbox_snapshot.py`
2. `workflow_dispatch` 手动触发，始终执行构建。

Workflow 设置固定 concurrency group，`cancel-in-progress: false`，保证两个 latest 切换任务不会并发执行。

## 构建与同步流程

1. Checkout 仓库。
2. 生成 UTC 时间戳和两个镜像 tag。
3. 从 `sandbox/` 与 `agent/skills/pptx/` 组装最小构建上下文。
4. 使用 Buildx 构建并推送时间戳 tag 和 latest tag。
5. 读取 `docker/build-push-action` 输出的 immutable image digest。
6. 安装项目锁定的 Python 依赖。
7. 从 GitHub Secret `LANGSMITH_API_KEY` 注入 LangSmith 凭据。
8. 调用 `scripts.sandbox_snapshot sync-image`，传入时间戳镜像、digest、latest Snapshot 名称和 candidate 名称。
9. 若现有 latest Snapshot 的 `image_digest` 与新 digest 相同，报告 skip，不创建或删除 Snapshot。
10. 若 digest 不同，创建 candidate Snapshot，并启动 2 GiB verify Sandbox 执行完整 PPTX 工具链验证。
11. candidate 验证通过后，记录旧 latest Snapshot 的镜像引用和 digest，删除旧 latest。
12. 从同一时间戳镜像创建 `ppt-deepagent-sandbox-latest`，再次执行完整 verify。
13. 新 latest 验证通过后删除 candidate。

## 失败与回滚

candidate 创建或验证失败时：

- 删除失败的 candidate Snapshot。
- 不删除或修改现有 latest Snapshot。
- Workflow 失败并输出非敏感诊断信息。

latest 切换失败时：

- 删除失败的新 latest Snapshot。
- 使用切换前记录的旧 immutable 镜像引用重建 latest。
- 对恢复后的 latest 执行完整 verify。
- 回滚成功后删除 candidate，并让 Workflow 保持失败，以暴露新版本发布失败。
- 回滚失败时保留 candidate 供排障，并输出 candidate 名称、旧镜像引用和失败阶段。

所有 verify Sandbox 都必须在 `finally` 中删除。不得在日志中打印 API key、Authorization header 或 `.env` 内容。

## Snapshot CLI

`scripts/sandbox_snapshot.py` 增加 `sync-image` 命令，负责：

- 精确查找 latest 与 candidate Snapshot。
- 比较 image digest 并返回 created、updated 或 skipped 结果。
- 创建和验证 candidate。
- 切换 latest。
- 在切换失败时恢复旧 latest。
- 清理失败 Snapshot 与 verify Sandbox。

CLI 不再通过导入 `agent.settings` 加载全部应用配置。它只从进程环境或本机 `.env` 读取 `LANGSMITH_API_KEY`，使 GitHub Actions 无需配置模型密钥和 Sandbox 应用配置。

现有 `build`、`from-image` 和 `verify` 命令保持可用。

## 测试策略

### Docker 与依赖

- Node 22 来源包含固定 digest。
- apt 安装列表不包含 `nodejs` 或 `npm`。
- Dockerfile 继续执行 `npm ci`。
- `/workspace/node_modules` 链接仍存在。
- `package.json` 与 `package-lock.json` 的 Sharp 版本一致。

### Snapshot 同步

- digest 相同直接跳过。
- 不存在 latest 时首次创建。
- candidate 失败不删除 latest。
- candidate 成功后替换 latest。
- latest 创建失败时恢复旧 latest。
- candidate 与 verify Sandbox 在成功和失败路径中按约定清理。
- verify Sandbox 使用 2 GiB 内存。
- Thread Sandbox 使用 2 GiB 内存。

### Workflow

- 只监听指定路径。
- 手动触发始终可用。
- 设置互斥 concurrency。
- 推送时间戳与 latest 双 tag。
- 将构建 digest、镜像引用、Snapshot 名称传给 `sync-image`。
- 仅使用 `LANGSMITH_API_KEY` 作为 LangSmith 凭据。

### 真实验收

流水线成功日志必须输出：

- 时间戳镜像引用。
- immutable image digest。
- latest Snapshot ID 和状态。
- Snapshot 文件系统容量 2 GiB。
- candidate 与 verify Sandbox 已清理。

随后运行现有 Snapshot 完整验证矩阵和 PPTX E2E smoke，确认 PptxGenJS、Sharp、Python 依赖、LibreOffice、Poppler、视觉检查和 `save_output` 均可用。
