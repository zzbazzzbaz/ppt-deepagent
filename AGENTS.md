## 通用约束

- 没有允许不能使用 agent-browser skill
- agent server 如果使用一定要重新启动，因为可能是旧进程，并且不要热更新：`uv run langgraph dev --no-browser --no-reload --port 2024`