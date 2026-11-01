# 开发、部署和 Mac 接入

Windows 原生后端的安装、差别和校准见 [Windows 说明](windows.md)。

## Windows / WSL / Linux

安装 Python 3.12+、uv，进入 `backend/` 执行 README 命令。使用 `uv.lock` 固定依赖。若 PyPI 下载网络超时，可临时采用可信镜像执行 `uv sync --index-url https://pypi.tuna.tsinghua.edu.cn/simple`，这会更新锁文件的来源，应审查后再提交。

未安装 WSL 的 Windows 可原生运行假实现与测试。本项目当前 Windows 测试不代表已经执行 WSL 测试。WSL Ubuntu 上复验：

```bash
cd /path/to/assistant/backend
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run python -m scripts.demo
```

API 时区默认 Asia/Shanghai。`PATCH /api/v1/settings` 可以修改时区、cron、置信度、重试和任务优先 harness。禁用 scheduler 的环境变量为 `ASSISTANT_SCHEDULER_ENABLED=false`。数据库路径默认 `.state/assistant.sqlite3`，记忆可用 `ASSISTANT_VAULT_DIR` 指向已有 Obsidian vault。设置会持久化到数据库。

## 飞书 OAuth

1. 飞书开放平台创建自建应用，启用用户授权，申请并发布历史消息读取权限和离线授权。精确可申请的用户权限以该应用控制台为准，不能假定管理员已经授权。
2. 在本机 `.env` 设置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`，注册回调地址。不要把凭据放插件 config。
3. 运行 `uv run python -m scripts.feishu_oauth start --redirect-uri http://127.0.0.1:8766/callback`，在浏览器打开输出的官方授权地址。此版本采用手工复制浏览器最终 URL 的安装流程，不提供公网 OAuth 回调服务。
4. 授权后本机回调地址可能显示无法连接，这是预期；将地址栏完整 URL 仅在本机交给 `uv run python -m scripts.feishu_oauth finish --callback-url '<完整回调URL>'`。不要发送到聊天或日志。可在临时无历史 shell 执行，完成后清除该 shell 历史。
5. token 写入 `.state/secrets/feishu.json`，刷新时原子替换。Unix 文件权限 0600；Windows 应确认该目录 ACL 只允许本用户。state 十分钟过期且一次性使用。
6. `PATCH /api/v1/plugins/feishu`：`{"config":{"whitelist":["oc_..."],"initial_lookback_hours":24},"enabled":true}`。只读这几个 ID，不枚举私聊。首次大窗口超过页数上限会失败并保留游标，缩小 lookback 后重试。
7. `POST /api/v1/jobs/fetch:feishu/run` 后查看 runs 和消息提炼结果。SDK OAuth/消息请求结构经过离线测试，当前没有对真实应用或用户 token 发请求。

## Agent CLI

分别安装并以运行后端的同一用户登录 Claude Code、Codex。不要把模型 API key 填到 App。默认 Claude Code 失败后切 Codex，可在 settings 中换序或用 `job_harness.extract_todos` 指定优先项。`status.agents.available` 只检测 CLI 是否存在，不能表示额度足够。

Claude 使用 `-p`、严格 MCP 配置、关闭内置工具；Codex 使用 `exec`、`--ignore-user-config`、`--ephemeral`、只读 sandbox 和显式 MCP 配置。CLI 版本必须支持这些参数；旧版本不支持会在 runs 中记 agent_failed 并切换。没有使用跳过权限/全盘访问参数。建议先用合成消息验证安装和登录；本次没有运行真实模型提炼。

### DeepSeek

第三个 Agent 是 DeepSeek，直接用它的 API（`backend/harness/deepseek.py`），不是命令行工具：后端把助手的 MCP 工具当作函数交给模型，模型要调用哪个工具，后端就替它调用，再把结果交回，直到模型给出回答。模型只能用这些工具，没有 Shell、文件和网页。它默认不在 `harness_order` 里，只有指定时才用：天气的今日提醒默认先用它（`brief_agent`），App 的输入框也可以选它的模型。

API key 只放在 Mac 上，二选一：

```bash
security add-generic-password -a "$USER" -s "AemeathBot DeepSeek API" -w
```

（运行后粘贴 key 回车，屏幕上不显示），或者在 `backend/.env` 里写 `ASSISTANT_DEEPSEEK_API_KEY=…`。改完重启后端。模型列表从 `https://api.deepseek.com/models` 读，默认用名字里带 flash 的那个（又快又便宜），可以用 `ASSISTANT_DEEPSEEK_MODEL` 指定。key 只发给 DeepSeek，不写日志；没有 key 时 `status.agents.deepseek.available` 为 false，用到它的任务会直接换下一个 Agent。

手工连接 stdio 时，从 backend 目录执行 `uv run python -m core.mcp_stdio`，父 Agent 必须在 MCP env 传递自己带 reader 的设备令牌（变量名 `ASSISTANT_TOKEN`）和 `ASSISTANT_BASE_URL`，不要使用 `.env` 中的主令牌；后端须已启动。远端 Streamable HTTP 位于 `/mcp`，用 `ASSISTANT_TOKEN` 或带 `mcp` 权限的设备令牌（`uv run python -m scripts.tokens create <名称> --scope mcp --reader "Claude Code"`）；从其他设备（经 Tailscale）接入时要把主机名写进 `ASSISTANT_MCP_ALLOWED_HOSTS`，见 [remote-mcp.md](remote-mcp.md)。

## EventKit helper（apple-sync）

`bash backend/scripts/install_mac.sh` 编译它，用固定的签名标识 `local.assistant.apple-sync` 做本机签名，复制到 `~/Library/Application Support/AemeathBot/bin/apple-sync`。没有改动时不重新复制：macOS 把提醒事项、日历权限记在这个程序上，换了程序就要重新允许。手工编译：

```bash
cd backend/helpers/apple-sync
swift build -c release -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PWD/Info.plist" -Xlinker -S
codesign -f -s - -i local.assistant.apple-sync .build/release/apple-sync
```

`-Xlinker -S` 不带调试信息，编出来的文件和所在目录无关：同样的代码在另一个目录编译，结果一模一样，已经给的权限不会失效。

由后端的 Python 启动时，macOS 默认把权限算在 Python 头上，而 Python 没有权限说明，会直接拒绝、不弹窗。所以 apple-sync 启动后会以「自己负责」的方式重新启动一次自己（放弃父进程的责任），弹窗写的是「Assistant Apple Sync」，授权也记在它自己身上，不管是终端还是 launchd 启动的。

首次运行时在 Mac 桌面上允许提醒事项和日历。**换了 apple-sync 可执行文件之后，要重新允许提醒事项和日历**；到「系统设置 → 隐私与安全性 → 提醒事项 / 日历」检查 Assistant Apple Sync，日历需要完整访问权限。可以先用测试内容 `reminders upsert`、`calendar upsert`，再 delete 清理。

helper 只修改专属「AI 待办」提醒事项列表，以及「AI 待办」「AI 课表」两个日历。第一次写入时创建，账号来源跟随系统新建项目的默认账户，要确认是和 iPhone 同步的 iCloud。默认账户换过时（比如先在「我的 Mac」、后来登录了 iCloud），旧账户里同名的专属列表/日历也照样查找：删除时两边都删掉；修改时在现在的默认账户里重建，再删掉旧账户里的那条，不会留下删不掉的旧条目。其他名字的日历会被拒绝，即使传入了其中的事件 ID，也不会修改它。

### 输入和闹钟

子命令仍是 `reminders upsert/delete`、`calendar upsert/delete`。从 stdin 读一个 JSON，成功输出 `{"id":"…"}`，删除成功输出 `{"id":null}`；删除也传相同结构。字段如下（Python 入口在 `core/apple.py`）：

| 字段 | 约定 |
| --- | --- |
| `client_id` | 必填 UUID；同一条记录始终使用同一个值，配合 URL 标记防止重试产生重复项 |
| `id` | 可选，之前返回的苹果实体 ID；找不到时仍用 UUID 标记查找 |
| `title`、`notes` | 必填字符串，标题和备注 |
| `due_at` | 可选，带时区的 ISO 8601 时间；提醒事项可为空，日历 upsert 必须有；在日历里作为开始时间 |
| `has_time`、`completed` | 必填布尔值；前者区分提醒事项的日期和具体时刻 |
| `alarms` | 可选整数数组，每项是提前分钟数，`0` 到点、负数在基准时间之后；传入时先清掉全部旧闹钟再重设，`[]` 清空，省略保留旧闹钟行为 |
| `calendar` | 可选，默认「AI 待办」；日历仅允许「AI 待办」「AI 课表」，提醒事项仅允许「AI 待办」 |
| `end_at` | 可选，日历结束时间，格式同 `due_at`，必须晚于开始；省略时为开始后 30 分钟 |
| `timezone` | 可选 IANA 时区名，省略时使用 Mac 系统时区；后端传运行设置里的时区，用于日期边界和日历显示 |

有具体时间时，提醒事项的绝对闹钟和日历相对开始时间的闹钟，都按 `due_at` 减去对应分钟数设置。**只有日期的提醒事项，基准是该日期在 `timezone` 中的午夜**，不把小时加进截止日期：`[-480]` 表示当天 08:00。没有截止日期或 `completed=true` 且传了 `alarms` 时，只清空闹钟。

例如，向「AI 待办」写一个 18:00 到期、提前一小时和到点都提醒的待办（同一份数据也可交给 `calendar upsert`）：

```json
{"client_id":"c56836b0-985c-4e5a-b67d-80b2a76887ad","id":null,"title":"提交报名表","notes":"来源：学校通知","due_at":"2026-10-30T18:00:00+08:00","has_time":true,"completed":false,"timezone":"Asia/Shanghai","alarms":[60,0]}
```

待办默认有时刻时用 `[60,0]`，只有日期时用 `[-480]`；完成后清空闹钟，删除时删除两端实体，无截止日期时不设闹钟。运行设置 `todo_alarm_minutes`（提前分钟数组）和 `todo_date_alarm_minutes`（午夜后分钟数，默认 480）可通过 `PATCH /api/v1/settings` 修改。修改这些设置或时区，会让已同步待办重新入队并尝试写入；失败保留 outbox，之后重试。升级后的第一次同步也会补上已有待办的闹钟。

这些是苹果提醒事项和日历自己的 EKAlarm，不需要 App 推送或付费开发者账号。要在 iPhone、Apple Watch 上验收，先确认两个 App 使用同一 iCloud 账户、打开日历和提醒事项同步与通知，并在 iPhone 的 Watch App 里检查对应的通知设置；专注模式、静音及设备的通知路由仍由系统决定。有时刻的待办只在提醒事项里响，它在日历「AI 待办」里的那一条不设闹钟（核心发给日历的 `alarms` 总是 `[]`），所以不会响两次。课前提醒写到单独的「AI 课表」，隐藏方法见 [timetable.md](timetable.md)。

在 Mac mini 上验证过（2026-10-24）：提醒事项和日历的创建、重复删除都成功。这条记录不包含本次新增闹钟和「AI 课表」的真机验收。之后在 `.env` 设 `ASSISTANT_APPLE_MODE=real` 和 `ASSISTANT_APPLE_BINARY=<上面的绝对路径>`。

## QQ / 微信屏幕 helper（screen-helper）

`backend/helpers/screen-helper` 是 Swift 写的只读截图助手（QQ、微信读取和课表共用），安装脚本把它装到 `~/Library/Application Support/AemeathBot/bin/screen-helper`（规则和 apple-sync 一样：没有改动不重新复制）。插件配置里的 `helper` 填这个绝对路径。它和 apple-sync 一样放弃父进程的责任，所以**屏幕录制**要授权给 screen-helper 本身，不是 Python；只有关闭预览模式、让它点开会话和滚动时，才需要**辅助功能**。

协议、怎样找会话列表和聊天区域、在 Mac 上怎样校准，见 [screen-reading.md](screen-reading.md)。操作集里没有输入、发送、加好友、点赞；Python 端也会拒绝约定以外的动作。截图只保存在插件的数据目录 `.state/screens/<插件>/`，保留 7 天。

## launchd 与网络

Mac 到货当天按 [mac-setup.md](mac-setup.md) 操作：`bash backend/scripts/install_mac.sh` 会安装依赖、生成并注册 LaunchAgent，最后运行 doctor。下面是脚本做的 launchd 部分的手工等价步骤。

先运行 `uv run python scripts/doctor.py`。doctor 不会为了自检而请求权限、操控聊天软件或更改系统设置；无法可靠自动判断的检查返回 manual，不冒报通过。

手工安装时，将 `backend/scripts/local.assistant.plist.example` 的绝对路径、用户名和 CLI PATH 改成真实值，在 Mac 复制至 `~/Library/LaunchAgents/local.assistant.plist`，再执行：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.assistant.plist
launchctl kickstart -k gui/$(id -u)/local.assistant
```

使用用户 LaunchAgent，因截图依赖已登录图形会话。不要启动第二个 worker。确认断电自动开机、自动登录、不锁屏、屏幕保持唤醒和显示器/HDMI 欺骗器；逐项验证屏幕录制、辅助功能、完全磁盘访问、提醒事项、日历、照片权限。QQ/微信保持登录状态。

默认只监听 loopback。需要跨设备时设置 `ASSISTANT_HOST` 为本机实际 Tailscale IP（100.64.0.0/10 或对应 IPv6 段），配好 Tailscale ACL，仅授权自己的设备。不要用 0.0.0.0、端口转发或公网代理。IP 地址范围校验不能代替确认它确实属于当前设备。

## 备份与恢复

每天本地 03:00 使用 SQLite 在线 backup API 和 Markdown vault 创建 `.state/backups/<时间>/`，完成后写 COMPLETE。没有自动删除历史备份。备份同样含私人资料，应由用户复制到独立受保护磁盘；本机磁盘坏掉时同盘备份不能救援。

恢复前停止后端，保留故障目录副本。从带 COMPLETE 的备份取数据库与 vault 放回指定位置，再启动；不要混用 WAL/SHM 旁文件。运行 `memory_index` 重建搜索索引。AppleSync 使用保存的 ID 与 client UUID 恢复幂等同步。当前备份不包含截图或 OAuth token，后者可重新授权。

## 资料文字提取 helper（doc-text）

`backend/helpers/doc-text` 独立于已有 screen-helper 和 apple-sync，源代码没有改动这两个已授权 helper。`bash backend/scripts/install_mac.sh` 编译安装到 `~/Library/Application Support/AemeathBot/bin/doc-text`，签名标识 local.assistant.doc-text；`--no-helper` 同时跳过三个 helper。doctor 检查 doc-text 是否安装。手工构建：

```bash
cd backend/helpers/doc-text
swift build -c release -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PWD/Info.plist" -Xlinker -S
codesign -f -s - -i local.assistant.doc-text .build/release/doc-text
```

doc-text 只从 JSON 指定的文件读取 PDF/图片，PDFKit 提取少字页后以 Vision OCR 兜底；图片直接 Vision。docx 使用系统 textutil。它不联网，不需要屏幕录制、辅助功能或 EventKit 权限，不必更换已有 helper。缓存放 ASSISTANT_DATA_DIR/knowledge_text，原资料不动。每晚整理计划、App 接口和带 reader 的本机/远程 MCP 配置见 [knowledge.md](knowledge.md)。本轮没有在沙盒编译 Swift 或验证真实资料提取效果。
