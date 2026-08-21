# PPT Deep Agent

基于 [Deep Agents](https://github.com/langchain-ai/deepagents) 与 LangGraph 的演示文稿智能体：理解需求 → 提交大纲 → 人工审批 → 在云端沙箱中生成、校验并发布可编辑的 PPTX。

## 特性

- **双模型协作**：DeepSeek 主模型负责规划与生成，Qwen 视觉模型对渲染出的每一页幻灯片做设计/质量检查
- **人工审批工作流**：生成 PPTX 前先提交逐页大纲，支持 `approve` / `edit` / `reject` 三种审批结果，审批通过前不会触碰任何 PPTX
- **云端沙箱执行**：每个会话（thread_id）对应一个 LangSmith Sandbox（预置 LibreOffice、Node.js、中文字体等），代码在沙箱中运行，本地零依赖
- **确定性校验流水线**：markitdown 内容检查 → OOXML 校验 → PDF 转换 → 渲染检查 → 视觉模型审查，校验失败绝不发布
- **MinIO 对象存储**：素材输入、工作产物、最终 PPTX 全部落在 MinIO，`save_output` 返回公网下载链接

## 工作流

```
用户需求
   │
   ▼
sync(download) 拉取 MinIO 素材 → 生成逐页大纲
   │
   ▼
submit_outline ──► 人工审批中断（approve / edit / reject）
   │
   ▼（批准后）
读取 pptx 技能 → 在 Sandbox /workspace/work/ 生成候选 PPTX
   │
   ▼
校验：markitdown → OOXML validate → soffice PDF → pdftoppm 渲染 → view 视觉检查
   │
   ▼
save_output 上传 MinIO 并返回下载链接 → sync(upload) 回传工作产物
```

## 项目结构

```
agent/
├── agent.py          # graph() 入口：按 thread_id 组装 deep agent（langgraph.json 注册）
├── settings.py       # pydantic-settings 读取 .env（全局配置中心）
├── infra/            # 基础设施：外部服务访问，统一经 agent.infra 导入
│   ├── __init__.py   # 包级对外 API（__all__）：模型、沙箱、快照、存储
│   ├── model.py      # DeepSeek 主模型 + Qwen 视觉模型单例
│   ├── sandbox.py    # LangSmith Sandbox 的创建 / 复用 / 等待就绪
│   ├── snapshot.py   # 查找就绪的快照
│   └── storage.py    # MinIO（S3 协议）封装，工具层不直接操作 boto3
├── prompts/
│   └── presentation_planner.py   # 系统提示词：审批前出大纲 → 审批后生成/校验/保存
├── skills/pptx/      # 演示文稿生成技能（SKILL.md + scripts）
│   └── scripts/office/           # validate.py（OOXML 校验）、soffice.py（PDF 转换）
└── tools/            # 工具层，统一经 agent.tools 导入
    ├── __init__.py   # 包级对外 API（__all__）：submit_outline + 3 个工具工厂
    ├── outline.py    # submit_outline：提交大纲，触发人工审批中断
    ├── sync.py       # sync：MinIO ↔ Sandbox 双向同步
    ├── view.py       # view：图片交给 Qwen 视觉模型检查
    ├── output.py     # save_output：校验全部 PPTX 后上传 MinIO 并返回链接
    └── _workspace.py # 工具间共享实现（私有）：工作目录列举、符号链接防护
sandbox/              # Sandbox 镜像（Dockerfile、依赖）
scripts/              # 冒烟测试、快照同步等运维脚本
tests/                # pytest 测试
docs/deploy/          # docker-compose 部署（postgres + redis + langgraph-server）
```

## 部署

生产部署使用 `docs/deploy/docker-compose.yml`：Postgres + Redis + LangGraph Server 容器，镜像由 CI 构建发布。

# 常用命令

```shell
sudo docker compose pull
sudo docker compose up -d --force-recreate
```

```shell
# 启动langgraph server
uv run langgraph dev --no-browser --no-reload --port 2024
# 端到端测试
uv run python -m scripts.smoke_pptx_e2e
```

```shell
https://agentchat.vercel.app/?apiUrl=https://agent.gqt.plus&assistantId=agent&apiKey=<langsmith api key>
```