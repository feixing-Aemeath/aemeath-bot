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

数据库启动时自动升级到 `PRAGMA user_version=9`：重算已有成绩的课程键，并把课表结束时间加入主键。升级在事务中完成，失败回滚，可重复启动；保留现有行、成绩发布时间和日历同步凭据。旧版因键冲突已经覆盖的成绩或课程无法从数据库恢复，需要重新导入成绩表或读取课表。

macOS/Linux 上，数据目录收紧为 `0700`，SQLite 数据库及 `-wal`、`-shm` 为 `0600`，已有文件也会收紧。启动还会收紧已有备份中的目录和文件（跳过符号链接）。Windows 跳过 chmod，使用系统 ACL。

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

### 提醒事项回读

`reminders read` 从 stdin 接收 `{"reminder_ids":["苹果实体 ID", "另一个 ID"],"client_ids":["待办 ID", "另一个待办 ID"]}`（可选 `timezone`，`client_ids` 与 `reminder_ids` 一一对应），一次读取所有账号里名为「AI 待办」的列表，包含已完成项。列表查询缺失的 ID 再在同一进程中按 ID 查找，识别移到其他列表的项目，不扫描其他列表的全文。它只检查已有授权，不弹授权框、不创建列表、不写入项目。成功响应为 `{"list_ids":["专用列表 ID"],"items":[...]}`；每个请求 ID 对应一个结果，字段为 `id,current_id,exists,list_id,completed,completed_at,title,due_at,has_time,last_modified_at`。按 ID 找不到时再按写入时留的 `assistant-todo://<待办 ID>` 标记找：找到说明苹果换了 ID（官方文档说全量同步时可能发生），`current_id` 给出新 ID，后端改绑；否则 `current_id` 为 null。时间使用带时区、毫秒精度的 ISO 8601；不存在的项目除 `id,exists` 外全部为 null。`exists=true` 的 `list_id` 可以不在专用列表中，表示项目移出了管理范围，后端暂停该项目的同步，直到它移回来。

无授权、无列表、读取失败分别向 stderr 输出 `apple_reminders_denied`、`apple_reminders_list_unavailable`、`apple_reminders_read_failed`，退出码为 1。Python 桥只保留这几个固定错误码，不记录原始错误或聊天内容。`apple_sync` 作业统一记录 `apple_sync_failed`，沿用 watchdog 的持续失败告警和恢复机制。

每 5 分钟的 `apple_sync` 先批量回读，再重试 outbox；没有变化时只有一次 helper 读取，没有逐条启动进程，也不写入苹果。完成和取消完成更新本地状态及来源，仅把必要的日历同步放进 outbox，不向提醒事项写回。已完成项目继续回读，已删除项目不再回读。即时 outbox 写入前也会批量回读待写的已有项目，首次缺失后只由定时轮询确认，不因 App/Agent 连续调用而加速删除。读取失败时暂停已有提醒事项的 upsert，独立的新建、删除和日历重试仍会尝试，作业统一报错交给 watchdog。

outbox 持久化本地实际修改的字段，同一字段上尚未发送的 App 修改优先；只改备注不阻止手机端标题、日期和完成状态回读，两端改不同字段会合并。Agent 修改前先回读并检查字段保护，检查、本地保存和写入共用同步锁，其他 App 修改不能插入检查之间。新的迁移保留旧行和 outbox revision；旧 outbox 无法确定改过哪些字段，保守地按标题、日期和状态都待发送处理，首轮写入后清空。每次成功读写保存苹果的最后修改时间，忽略更旧的 iCloud 快照，避免旧值重新打开任务或误加字段保护；苹果未提供修改时间时无法判定快照先后。

已有提醒事项的 upsert 另带 `require_existing=true` 和 `expected_modified_at`（上次回读的苹果修改时间，可空）。Swift 在取到当前项目后核对 ID 和版本；项目已删除、移走或版本变化就中止本次写入，保留 outbox，下一轮重新回读合并。这样回读完成后、helper 查找项目之前发生的手机操作也不会被旧 payload 覆盖。需要配套部署本次新版 helper。EventKit 和 iCloud 不提供跨设备的原子比较并写入，因此版本检查之后才到达的远端变更仍需真机验证。

标题、截止日期和具体时刻的修改也以提醒事项里的值更新本地待办，并同步日历副本；清除截止时间或改为只有日期时删除原日历副本。`user_modified_fields` 持久保存用户改过的字段，Agent 的 MCP `todos_update` 不再允许覆盖它们，返回固定错误码 `todo_fields_user_modified`，整次修改不部分生效；用户仍可从 App 修改。时间按 EventKit 的秒精度比较，只有日期时比较运行时区中的日期，避免把格式和时区表示变化误认成用户改期。回读遵守原有标题长度等校验；若字段不合法，或用户改出的标题和截止时间正好与另一待办的去重键相同，该项保留原有标题和时间并记 `apple_reminder_edit_rejected`，状态回读和其他项目继续同步，待用户调整标题或时间后自动恢复。

第一次找不到项目只持久化缺失记录并暂停它的 outbox 写回；下次成功轮询仍找不到，且距首次至少 5 分钟，才按用户删除处理，日历删除失败继续持久化重试，提醒事项不会被重建。失败读取会打断连续缺失计数；原列表不再可见时不删除。升级前的旧待办若没有原列表 ID，首次缺失只暂停同步，必须先成功看到该项目才能确认原列表，不能因另一个账号有同名列表而推断删除。缺失记录和日历单独重试标记均保存在 SQLite，重启不会丢失。回读改标题或日期时还会保存旧去重键，之后按原通知重新提炼仍合并到同一待办，已删除项不会因此重建。Linux/Windows 默认仍使用 `FakeAppleBridge`，真实桥在非 Mac 上报 `macos_required`；假桥提供 `change_reminder`、`delete_reminder` 以模拟手机端操作。

本次只编译检查 helper、使用假实现测试，未执行真实 EventKit 操作。真机验收由 Claude 在审查后执行：沿用固定签名的安装流程并检查权限；确认多账号同名列表、完成时间、取消勾选、日期和具体时刻的读取；删除单项后观察两轮同步及日历清理；撤销权限或暂时隐藏列表时确认待办保留、watchdog 只持续跟踪同一故障，恢复后能继续同步。任务 worktree 内不要签名、安装或执行刚编出的 helper，也不要重启线上后端。

写入成功的提醒事项响应还包含 `list_id,last_modified_at`，用于在第一次回读前记住原列表和已写入版本；之后原账号的列表消失，即使其他账号还存在同名列表，也不会被误判为用户删除单项。回读使用 EventKit 的批量提醒事项查询，见 [Apple 官方文档](https://developer.apple.com/documentation/eventkit/retrieving-events-and-reminders)。真机还需验证修改标题、增删截止日期和时刻后，App 与日历副本更新，Agent 覆盖被拒绝，App 主动修改仍成功；移出专用列表时不会误删，移回来后恢复。
已完成的待办在提醒事项里被清除（「清除已完成」）时不算删除：确认缺失后只清空 `reminder_id`，待办仍是已完成，日历副本保留，也不再补写新提醒；从 App 重新打开时才新建一条。

iCloud 全量同步可能让 ID 变化，上面的标记能把它们认回来；标记也丢了的话，一次轮询里至少 3 条且不少于一半未完成项同时缺失时，都只暂停写回、不算删除，作业报 `apple_reminders_mass_missing`，watchdog 超过 3 小时后在 App 里提示。确实是用户自己删的，在 App 里也删掉，剩下的低于阈值就恢复正常判断。只有定时的全量轮询才会把缺失确认成删除，单条写入和 Agent 修改前的回读不会。

回读改出的标题过长或与另一条待办完全相同时，作业报 `apple_reminder_edit_rejected`，在提醒事项里改一下那条就恢复。

### 部署前只读核对

先准备一份通过 SQLite backup API 生成的、没有进程继续写入的独立数据库副本。在 `backend/` 下运行：

```bash
uv run --locked python -m scripts.predeploy_check --db /path/to/backup.sqlite3
uv run --locked python -m scripts.predeploy_check --db /path/to/backup.sqlite3 --apple-sync /path/to/new/apple-sync
```

脚本不读取 `.env` 或 token，不启动后端、不迁移数据库。数据库以 `mode=ro&immutable=1` 打开，避免 WAL 模式的副本产生旁文件；有 `-wal`、`-shm` 或 `-journal` 的副本会报 `db_sidecars_present`，请重新生成独立备份，不要直接删除旁文件。指定 helper 时只执行一次 `reminders read`，检查已有授权，不弹授权框、不写入苹果数据。应指定支持回读的新版本 helper。

输出只有固定标签、计数和错误码，不含标题、备注、消息、人名、任何实体 ID 或文件路径。`db.user_version` 是原始版本；`db.integrity_errors`、`db.foreign_key_errors` 为检查发现的问题条数，正常均为 0。其余 `db.*` 统计各状态待办、非空提醒事项和日历 ID、outbox，以及 `apple_reminder_state` 是否存在（0/1）和缺少有效状态行的提醒事项数。状态行的待办及提醒事项 ID 都匹配才算有效。

数据库计数包含所有待办；苹果对比只检查核心会回读的未完成和已完成待办，跳过已删除、已取消和仅在 App 内展示的来源（如 watchdog）。`apple.requested` 是请求数，`apple.read` 是返回结果数（包含缺失项），`apple.found` 是实际找到的项目数，`apple.lists` 是可见的专用列表数。每条待办只归入下面一个分类，先判断缺失和移出列表，再比较标题、日期，最后比较完成状态：

| 输出分类 | 部署含义 |
| --- | --- |
| `apple.consistent` | 存在且标题、日期、完成状态一致 |
| `apple.status_only` | 只有完成状态不同，包括手机勾选或取消勾选；回读会合并状态 |
| `apple.title_or_date_changed` | 标题、截止日期或是否有具体时刻不同；回读接受后标记为「用户改过」，后续 Agent 不能再改这些字段 |
| `apple.missing` | 按旧 ID 和待办 marker 都找不到；升级前没有有效状态行的旧项目无法确认原列表，会一直挂起同步，需要人工决定如何处理 |
| `apple.other_list` | 项目存在但已移到其他列表，同步挂起，移回专用列表后才恢复 |

`apple.current_id_changed` 另外统计苹果换了 ID、由 marker 找回的数量，与上述分类重叠；核心会改绑新 ID，本身不阻止部署。比较复用 `core.apple.ReminderBatch` 和 `reminder_changes`，按运行设置的时区比较日期、按秒比较具体时刻。分类描述当前副本和苹果快照的差异；实际合并仍受未发送的 outbox 字段、旧快照和字段校验影响，因此不能据此断言每条差异都会立即应用。有有效历史状态行的缺失项目则沿用核心的连续轮询确认规则。

最后一行是 `可以部署`（退出码 0），或 `需要先看一下：<原因代码>`（退出码 1，参数错误为 2）。以下原因需要处理或审查：

- `reminders_user_modified_fields`：确认接受手机端字段和后续 Agent 字段保护。
- `reminders_missing_manual_decision`：缺失项目需要人工决定；脚本不会重建、解绑或删除。
- `reminders_other_list_sync_held`：移出专用列表的项目会挂起同步。
- `outbox_pending`：存在尚未发送的写入，先审查队列影响。
- `apple_check_skipped`：未提供 `--apple-sync`，只完成数据库检查，不能据此判定完整部署就绪。
- `apple_sync_not_found`、`apple_sync_unavailable`：helper 不存在或无法执行。
- `apple_reminders_denied`、`apple_reminders_list_unavailable`、`apple_reminders_read_failed`、`apple_reminders_timeout`：未授权、专用列表不可见、读取失败或超过 30 秒；原始错误不会输出。
- `db_*`、`arguments_invalid`：数据库打开、结构、完整性、外键、设置、待办日期或参数有问题；`db.todos.unknown` 只汇总未知状态，不打印状态原文。

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

每天按设置的时区在 03:00 使用 SQLite 在线 backup API 和 Markdown vault 创建 `.state/backups/<时间>/`，完成后写 COMPLETE。启用调度时，启动后若没有完整备份或最新 COMPLETE 已超过 24 小时，会立即补做一次。复制在工作线程运行；取消会等待线程结束，复制失败会清理本次未完成目录。没有自动删除历史备份。备份同样含私人资料，应由用户复制到独立受保护磁盘；本机磁盘坏掉时同盘备份不能救援。

恢复前停止后端，保留故障目录副本。从带 COMPLETE 的备份取数据库与 vault 放回指定位置，再启动；不要混用 WAL/SHM 旁文件。运行 `memory_index` 重建搜索索引。AppleSync 使用保存的 ID 与 client UUID 恢复幂等同步。当前备份不包含截图或 OAuth token，后者可重新授权。

## 资料文字提取 helper（doc-text）

`backend/helpers/doc-text` 独立于已有 screen-helper 和 apple-sync，源代码没有改动这两个已授权 helper。`bash backend/scripts/install_mac.sh` 编译安装到 `~/Library/Application Support/AemeathBot/bin/doc-text`，签名标识 local.assistant.doc-text；`--no-helper` 同时跳过三个 helper。doctor 检查 doc-text 是否安装。手工构建：

```bash
cd backend/helpers/doc-text
swift build -c release -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PWD/Info.plist" -Xlinker -S
codesign -f -s - -i local.assistant.doc-text .build/release/doc-text
```

doc-text 只从 JSON 指定的文件读取 PDF/图片，PDFKit 提取少字页后以 Vision OCR 兜底；图片直接 Vision。docx 使用系统 textutil。它不联网，不需要屏幕录制、辅助功能或 EventKit 权限，不必更换已有 helper。缓存放 ASSISTANT_DATA_DIR/knowledge_text，原资料不动。每晚整理计划、App 接口和带 reader 的本机/远程 MCP 配置见 [knowledge.md](knowledge.md)。本轮没有在沙盒编译 Swift 或验证真实资料提取效果。
