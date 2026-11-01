# 24 小时助手后端 v0.1

Python 3.12+ / uv / FastAPI / SQLite FTS5 / APScheduler / 官方 MCP SDK。只修改 `backend/` 和 `docs/`，预留未来 `frontend/`。

已实现核心、HTTP v1、stdio/Streamable HTTP MCP、动态插件、双 CLI 适配器、飞书官方 SDK 接入、QQ / 微信屏幕读取（Swift 截图与 Vision 识别助手）、Swift EventKit 助手、持久化同步队列、备份和环境诊断；App 里每个角色对应一个插件：作业、请假与学校活动、学分成绩（[说明](../docs/school-plugins.md)）、Token 余额与消耗、健康，都可以和 Bot 对话。阶段三中健康与睡眠分析的算法部分已实现为 health 插件（[方法说明](../docs/health-methods.md)），token_usage 插件根据每次 Agent 运行报告的 token 数显示余额和消耗（见 [api.md](../docs/api.md)「token_usage 插件的卡片」）。其余阶段三插件按任务说明暂缓。

**已部署在 Mac mini 上（2026-10-24）**：LaunchAgent 常驻，真实提醒事项和日历经 apple-sync 写进 iCloud「AI 待办」，Codex 通过 MCP 完成待办提炼和 Bot 对话，屏幕读取助手 screen-helper 已在 QQ 7 上校准了预览模式。还没验收的：真实 QQ 白名单群的读取（等屏幕录制授权和白名单）、关闭预览模式后点开聊天、飞书真实账号、Claude Code 登录后的回退。详细状态见 [验收记录](../docs/acceptance.md)。

## 本地启动

在本目录执行（Linux/WSL/macOS 相同）；Windows 原生部署见 [Windows 说明](../docs/windows.md)，包含计划任务、OCR 校准和功能差别：

```bash
uv sync --locked
cp .env.example .env
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
# 将输出填入 .env 的 ASSISTANT_TOKEN；不要复制到聊天或 GitHub
uv run python -m core
```

Windows 用 `Copy-Item .env.example .env`。默认仅监听 `127.0.0.1:8765`、所有插件停用。Mac/Linux 默认使用假苹果同步；Windows 的正常服务明确报告 `macos_required`，待办仍保留在 App。数据库、假提醒事项、Markdown 记忆、备份和 OAuth 凭据位于被 Git 忽略的 `.state/`。

```bash
uv run python -m pytest -q
uv run ruff check .
uv run python -m scripts.demo
uv run python scripts/doctor.py
```

持续集成：`.github/workflows/backend.yml` 在每次推送和每个 PR 上，用 Ubuntu 和 Python 3.12、3.13 依次运行 `uv sync --locked`、`ruff check`、`ruff format --check` 和全部 pytest；另有 `windows-latest` / Python 3.12 任务运行 PowerShell 语法检查及同样的测试和 Ruff。

在本机导入 Apple 健康导出文件，并打印最近 14 天的指标和预警（只在本机计算；`--dry-run` 不写入数据目录）：

```bash
uv run python -m scripts.health_import ~/Downloads/export.zip --dry-run
uv run python -m scripts.health_synthetic .cache/synthetic/export.xml   # 没有真实数据时先用合成数据试
uv run python -m scripts.readiness_calibration   # 记满 3 周后：对比准备度和手动记录，只给权重建议
```

demo 使用合成消息与脚本化 Agent，演示跨来源去重、额度回退、纯通知摘要和假提醒事项；不会请求云模型、读取真实聊天或写苹果账号。

API 需要 Bearer Token，包括 `/openapi.json`。`.env` 里的 `ASSISTANT_TOKEN` 是本机令牌；其他设备每台一个设备令牌，分 `app`、`mcp`、`health_upload` 权限，可以单独作废（见 [api.md](../docs/api.md)「连接与鉴权」）：

```bash
uv run python -m scripts.tokens create 学校电脑 --scope mcp --days 180   # 只显示一次
uv run python -m scripts.tokens list
uv run python -m scripts.tokens revoke 学校电脑
```

请求示例：

```http
GET /api/v1/status HTTP/1.1
Host: 127.0.0.1:8765
Authorization: Bearer <your-local-token>
```

- [API v1 契约](../docs/api.md) / [OpenAPI](../docs/openapi.json)
- [Mac mini 到货当天的部署清单](../docs/mac-setup.md)（一键安装：`bash scripts/install_mac.sh`）
- [从其他设备连接记忆中心（远程 MCP）](../docs/remote-mcp.md)
- [部署、OAuth 和 Mac 接入](../docs/deployment.md)
- [架构与扩展](../docs/architecture.md)
- [验收记录](../docs/acceptance.md)
- [待办提炼评测集](../docs/extract-eval.md)（`uv run python -m evals.run_extract --harness claude_code --mock`）

生产只运行一个核心进程；OS 文件锁阻止重复实例。stdio MCP 是连接该进程的桥接，不会另开数据库服务或定时任务。
