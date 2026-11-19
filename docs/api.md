# 24 小时助手 HTTP / MCP 接口 v1

状态：v1。前端只依赖本文和 `openapi.json`。时间使用带时区 ISO 8601，服务端保存 UTC，今日按设置的时区（默认 Asia/Shanghai）计算。ID 为字符串。

## 连接与鉴权

HTTP 基址默认 `http://127.0.0.1:8765`。所有请求（包括 OpenAPI、MCP）必须带 `Authorization: Bearer <token>`；只有 `/dav/` 另接受 HTTP Basic（密码为设备令牌），见 [WebDAV 入口](webdav.md)。仅允许 loopback 或 Tailscale IP 绑定。公网接入不受支持。生产只启动一个 Uvicorn worker。

令牌分为主令牌、设备令牌和每次运行的临时令牌：

- **`ASSISTANT_TOKEN`**：Mac 本机的令牌，至少 32 字符，从环境或本机 `.env` 加载，有全部权限。不返回给客户端，也不直接传给后台 Agent；用户自运行 Agent 建议使用带 reader 的设备令牌。
- **设备令牌**：每台设备一个，在 Mac 上用 `uv run python -m scripts.tokens create <名称> --scope <权限> [--days N] [--reader "Claude Code"]` 创建、`list` 查看、`revoke <ID 或名称>` 作废。作废和过期在下一个请求就生效，不用重启后端。格式是 `a24_` 加 43 个字符；创建时只显示一次，数据库里只保存它的 SHA-256 哈希（`device_tokens` 表），列表里只显示开头 8 个字符。

| 权限 | 能访问 |
| --- | --- |
| `app` | App 的 HTTP 接口：`/api/v1/…`、`/openapi.json`。还可访问 `/dav/`；其他不属于 MCP 的路由（包括以后新增的）也算 App 接口 |
| `dav` | 只能访问 `/dav/`（Bearer / Basic）；不能调用 `/api/v1/…`、OpenAPI 或 MCP，详见 [webdav.md](webdav.md) |
| `mcp` | MCP：`/mcp`（Streamable HTTP）和 stdio 桥接用的 `/internal/tools…`；不能调用 App 接口 |
| `health_upload` | 只有 `POST /api/v1/health/samples`，给快捷指令这类只上传健康数据的自动化用 |

一个令牌可以有几个权限（例如 iPhone 用 `app`，学校电脑只用 `mcp`）。设备令牌可带 Agent `reader`，限制 MCP 记忆文件的区域；旧的无 reader 令牌仍未限定。Bot 对话和主题整理使用只存在内存、运行结束即撤销的临时 mcp 令牌；其他后台 Agent 同样每次尝试使用独立临时令牌，工具及记忆读取范围默认关闭、由任务显式声明，结束、回退或服务重启后即失效。App 权限的知识库接口始终代表用户本人，可读全部区域。reader 限定记忆目录；后台任务另有内置/插件工具白名单，见 [knowledge.md](knowledge.md)。

无 Token、错误、已作废或已过期的 Token 返回 401；Token 有效但没有这个路由的权限返回 403（`{"detail": "insufficient_scope"}`，响应头 `WWW-Authenticate: Bearer error="insufficient_scope"`）；不存在返回 404；状态冲突返回 409；字段错误返回 422；故障返回不包含聊天内容或密钥的错误码。分页使用 `limit`（1–200，默认 50）和 `offset`（默认 0），列表响应为 `{items: [...], total: n}`。

## HTTP 路由

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | /api/v1/status | 无 | Status |
| GET | /api/v1/today | 无 | Today |
| GET | /api/v1/todos | status?: pending/completed/deleted/cancelled; limit; offset | TodoPage |
| POST | /api/v1/todos | TodoNew | 201 Todo；命中未完成待办为 200 Todo |
| PATCH | /api/v1/todos/{id} | TodoPatch | Todo |
| GET | /api/v1/todos/{id}/sources | 无 | TodoSourceTexts |
| POST | /api/v1/inbox | InboxItem | 202 InboxAccepted |
| GET | /api/v1/stats/extraction | weeks?: 1–26，默认 4 | ExtractionStats |
| GET | /api/v1/digests | date?: YYYY-MM-DD; plugin?; before?; limit; offset | DigestPage |
| GET | /api/v1/runs | job?; status?: running/succeeded/failed/cancelled; since?; until?; limit; offset | RunPage，按 started_at、id 倒序 |
| POST | /api/v1/jobs/{job_id}/run | 无 | 202 JobAccepted；已运行返回 409 |
| GET | /api/v1/plugins | 无 | PluginView[] |
| PATCH | /api/v1/plugins/{id} | PluginPatch | PluginView |
| DELETE | /api/v1/plugins/{id} | 无 | 204；保留历史消息和待办 |
| GET | /api/v1/plugins/{id}/cards | 无 | Card[] |
| POST | /api/v1/plugins/{id}/open-chat | OpenChat | 202 OpenChatAccepted |
| POST | /api/v1/health/samples | HealthBatch | HealthAccepted |
| GET | /api/v1/health/reports | date?: YYYY-MM-DD; kind?: morning/weekly; status?: pending/ready/failed; before?; limit; offset | HealthReportPage |
| GET | /api/v1/app/bot-preferences | 无 | BotPreferences |
| PUT | /api/v1/app/bot-preferences | BotPreferencesUpdate | 200 BotPreferences；版本冲突返回 409 和当前 BotPreferences |
| GET | /api/v1/settings | 无 | RuntimeSettings |
| PATCH | /api/v1/settings | SettingsPatch | RuntimeSettings |
| GET | /api/v1/bots/{bot_id}/messages | before?; limit; offset | ChatPage，新的在前 |
| POST | /api/v1/bots/{bot_id}/messages | ChatSend | 202 ChatAccepted；这个 Bot 上一句还没回复完返回 409 |
| POST | /api/v1/videos | `{url,topic?}` | 200 Video；重复返回已有条目，失败的条目重新排队 |
| GET | /api/v1/videos | limit; offset | `{items: Video[], total}`，按入队时间倒序 |

稳定任务 ID：`knowledge_organize`（每晚资料整理，默认 03:00）、`extract_todos`、`apple_sync`、`backup`、`memory_index`、`daily_notes`（每日文件夹，见 [daily-folder.md](daily-folder.md)）、`watchdog`（数据看门狗，见 [watchdog.md](watchdog.md)）、`fetch:<plugin_id>`；插件可以提供 `plugin:<plugin_id>:<job_name>`。不存在/已停用的任务不能运行。202 表示进入执行，不表示成功；轮询 runs 查询结果。插件删除为持久化卸载标记，不删除历史数据或执行任意目录删除，重启不会自动恢复。

### Bot 侧栏与仪表盘偏好

这两个接口要求 `app` 作用域。GET 返回 `{version, entries}`，首次为 `{"version":0,"entries":{}}`。PUT 提交完整的 `{expected_version, entries}`。后端在同一事务内比对版本、替换整份偏好并将版本加一。版本过期时返回 HTTP 409，响应体与 GET 格式相同。

`entries` 最多 100 项，键符合插件 ID 格式 `^[a-z][a-z0-9_]{0,63}$`。每项含 `pinned`（是否收藏）、`custom_name`（最多 20 个 Unicode 码点，空串恢复原名）、`position`（非负 64 位有符号整数，可省略或为 null 表示默认顺序）、`visible`（是否在仪表盘显示）和 `updated_at`（带时区的修改时间，返回固定六位小数的 UTC 时间）。请求体和条目均拒绝未知字段，校验失败返回 422。

App 按 Bot 合并两端条目，保留仅出现在一端的项。同一 Bot 取修改时间较晚的一整条，时间相同采用服务端条目。恢复默认也保留带时间的记录，供离线设备合并。旧版本机偏好沿用，超过 20 个码点的旧名字取前 20 个码点。旧偏好缺少修改时间，首次同步时已带时间的修改优先。

### 保底入口、原文与对账

以下接口均要求 `app` 权限，沿用 401/403 鉴权及 422 `invalid_request` 格式；未知请求字段拒绝。202 只表示已接收，`job_started=false` 表示该任务正在运行，输入已持久化，由当前运行或下一次定时任务处理，不启动重叠任务。

- **TodoNew**：必填 `title`（与 TodoPatch 相同，1–500 字且不能全空白）；可选 `notes`（默认空串，最多 50000 字）、`due_at`（带时区或 null）、`has_time`（默认 false，为 true 必须有 due_at）、`missed`（默认 false）、`missed_source`（可空的 `{plugin: string, chat_name: string}`，只有 missed=true 时保存）。`POST /todos` 新建返回 201；规范标题、截止时间、has_time 命中已有 pending 待办的当前值时返回原条目和 200，不覆盖原字段。新建固定 `created_by=user`、`source_plugin=app`，并保护 title、due_at、has_time，Agent 无法改变这些字段；命中已有 Agent 待办时仍返回其原归属。提醒事项、日历和失败重试共用 MCP todos_add 的去重及 outbox。手动输入留一条已处理的 app 本地来源，兼容 Todo 的来源字段；不进入消息提炼。相同键的完成/删除/取消历史不阻止用户新建，旧 Agent 带原来源重试仍对应旧事项。
- **InboxItem**：必填 `text`（1–8000 字）；可选 `note`（最多 500 字）、`source_app`（最多 40 字）、`chat_name`（最多 80 字）、`missed`（默认 false）。`POST /inbox` 返回 **InboxAccepted** `{message_id: string, job_started: boolean}`；存成未处理的 `share` 消息，sender=我，未填或空的 chat_name 使用「转给助手」，原文 text 不拼入备注，备注和来源 App 另存，提炼工具同时提供给 Agent。同一原始 text 在首次提交后 10 分钟内（含边界）返回同一 message_id，备注等沿用首次提交；不延长窗口。share 不需要白名单、不注册插件、不出现在插件列表。收到后立即尝试启动 extract_todos，摘要使用 plugin=share，今日概览与每日文件夹也包含它。
- **TodoSourceTexts**：`{items: [{message_id,plugin,chat_name,sender,sent_at,text}]}`。`GET /todos/{id}/sources` 仅返回 source_refs 中仍存在的消息，顺序相同，每条 text 最多 4000 字，截断不加省略号；不返回截图路径或图片。待办不存在为 404 `not_found`，没有可用原文时 items=[]，旧数据缺失来源不报错。
- **Today.unsure**：数组，默认 []；从当天各摘要的 `**还不确定**` 节逐条生成，顺序同 digests（plugin 正序）及各摘要内的行顺序。每项为 `{id,text,plugin,chat_name,can_open}`，text 去掉列表前缀；id 为 `u-` 加日期和行文字的稳定 SHA-256，同一天相同文字的 ID 相同。日期全部已过的行不返回（规则同 summary）。旧摘要无此节时返回空数组。行内第一个中文或英文冒号前的会话名用屏幕插件的白名单匹配（沿用 OCR 名称归一化）；同名时优先摘要自己的插件，否则必须唯一匹配，无法匹配/消歧时 plugin、chat_name 均为 null。can_open 仅在插件已启用、会话属于 open_on_demand 且当天还有点开预算时为 true；未启用仍可识别配置内的会话但不能点开。旧 App 可忽略 unsure；新 App 对旧后端缺字段时可以回退解析 summary。
- **OpenChat**：`{chat_name: string}`。`POST /plugins/{id}/open-chat` 复用 MCP screen_open_chat 的固定脚本请求及每日预算；成功返回 **OpenChatAccepted** `{accepted: true, job_started: boolean}` 并立刻尝试启动 fetch:<id>。未消费的同会话请求幂等，不增加点开队列；实际点开才消耗预算，仍会把手机消息标为已读。插件不存在、未启用、或不是 qq_screen/wechat_screen 返回 404 `not_found`；不在白名单返回 403 `screen_open_not_whitelisted`；不在 open_on_demand 返回 403 `screen_open_not_allowed`；当天预算用尽返回 409 `screen_open_daily_limit`。
- **TodoPatch.reason**：可空枚举 `not_a_todo | done_elsewhere | duplicate | wrong_time`。只有同一请求实际将 status 改成 deleted 或 cancelled 时写入 todo_changes；其他情况接受并忽略，不改变原来的同步和状态规则。旧记录 reason=null，不猜测原删除原因；重复相同状态不重复记录。
- **ExtractionStats**：`{weeks: ExtractionWeek[]}`。`GET /stats/extraction?weeks=4` 包含本周到现在，最多 26 周，最新周在前；每周从设置时区的周一 00:00 到下一周一 00:00（不含）。HTTP 与本周回顾共用 core/extraction_stats.py 的同一计算。未来时间戳不计入。

**ExtractionWeek** 的字段与口径：

| 字段 | 类型 | 计算 |
| --- | --- | --- |
| week_start | YYYY-MM-DD | 本地周一 |
| caught | integer | 按 todo.created_at 归周的 Agent 消息待办数，合并多来源只算一条，含已完成、删除、取消历史；created_by 为 agent/claude_code/codex/deepseek，消息来源里至少有一个是助手自己读到的（qq_screen/wechat_screen/feishu/sms/mail）；只来自 share 的不算，那是用户转来之后才有的，算进来会在读取失败时反而抬高比例。排除 watchdog、goals、测试与用户手建 |
| with_due | integer | caught 中 due_at 非空的数量 |
| late | integer | with_due 中 created_at 严格晚于 due_at 的数量；恰好相等不算晚 |
| median_lead_hours | number 或 null | 非晚到且有截止的 (due_at − created_at) 小时数的中位数；用实际经过时长，跨时区/夏令时不按墙上时钟相减。没有则 null |
| missed | integer | 用户通过 TodoNew/InboxItem 标了 missed 的次数，按首次标记时间归周；同一待办或分享消息幂等计一次，未标记不推断遗漏 |
| not_a_todo | integer | 用户 reason=not_a_todo 删除/取消的 Agent 消息待办数，按变更时间归周；同一条在同一周反复取消只算一次，Agent 自己的变更不算 |
| recall | number 或 null | caught / (caught + missed)，分母为 0 时 null |

提前量依约直接比较保存的 due_at（包括只有日期时保存的本地午夜），不改动待办在其他页面「日期到当天结束才逾期」的显示规则。历史截止被修改后重算使用当前 due_at，旧数据没有原始截止快照；missed 和 not_a_todo 从上线后显式反馈起累计，历史空缺为 0，不推断补填。recall 是显式补漏下的对账比例，不是对全部真实遗漏的测量。

启动时自动增量创建 inbox_submissions、extraction_missed，并为 todo_changes 添加可空 reason；历史消息、待办和变更保留，重复启动幂等。部署前沿用日常备份，不需要手工搬数据。

数据库 v10 根据现存来源消息补齐历史待办的 `source_plugin`，保留原引用顺序，把发生修正的待办重新放入同步队列。新建时一次引用多个来源也保存完整插件集合。

显式恢复取消项时，若旧提醒已删除而回执丢失，确认旧副本缺失后按原待办 ID 重建。单项回读失败只打断该项的缺失计数。手动新建同键的新一期待办后，已完成的旧项仍可编辑备注。手机取消勾选旧项若与当前事项重名同期限，回读报冲突；修改标题或期限消除冲突后可以恢复。

### 历史分页与运行计数

Bot 时间线的对话、摘要、健康简报分别来自下面三个列表。原有 `limit`（默认 50，1–200）、`offset`（默认 0）、筛选条件和排序均保留；响应新增三个字段，旧 App 可忽略。未传 `before` 时仍返回最近一页，不会改成整天返回。

| 接口 | `before` 的时间边界写法（不含边界本身） | 原有排序 |
| --- | --- | --- |
| `GET /api/v1/bots/{bot_id}/messages` | 带时区 ISO 8601，如 `2026-11-04T00:00:00+08:00`，按 `created_at` 筛选 | `created_at` 倒序；同一时间按插入顺序倒序，回复在用户这句前面 |
| `GET /api/v1/digests` | `YYYY-MM-DD`，如 `2026-11-04`，按摘要的 `date` 筛选 | `date` 倒序、`plugin` 正序 |
| `GET /api/v1/health/reports` | `YYYY-MM-DD`，如 `2026-11-04`，按报告的 `date` 筛选 | `date` 倒序、`kind` 正序 |

三个接口都另外接受响应里的不透明 `next_before` 作为 `before`。游标包含时间和同一时间内的位置；客户端原样传回，不能自己拿最后一条的时间代替，否则同一时间的记录可能遗漏。后续请求保留原 Bot 和原筛选条件（`date/plugin/kind/status`），可调整 `limit`。游标不能跨接口、Bot 或筛选条件使用。`before` 与非零 `offset` 同时传、空/损坏/不匹配的游标、聊天边界缺少时区，均返回 422 `invalid_request`。`before` 最长 2048 字符。GET 聊天历史与发送消息使用同一 Bot 可用性检查；插件停用或删除后返回 404，已保存的对话记录保留。可用 Bot 没有记录时返回空页。

响应为 `{items, total, has_more, next_before, earlier_date}`：

- `total`：满足非分页筛选条件的全部记录数，不受 `limit/offset/before` 影响。
- `has_more`：本页之后是否还有更早的记录。
- `next_before`：继续上一页的游标；`has_more=false` 时为 null。
- `earlier_date`：下一条未返回记录所在的日期（`YYYY-MM-DD`），无更多时为 null。聊天按后端 `settings.timezone` 换算，摘要和简报直接取其 `date`。它可能与本页最早记录在同一天，不保证是前一天，也可能跨过无消息的数天。

例如首屏 `GET /api/v1/bots/assistant/messages?limit=100`；点「看更早」时带 `before=<next_before>&limit=100`，按 ID 合并结果。今天超过 100 条时先继续取完当天，再取历史，不能把 `earlier_date` 当作下一页的截止时间，否则会跳过那一天。若要直接查询今天之前的消息，聊天用今天在设置时区的零点作为 `before`，摘要/简报用今天的日期。时间线由多个列表合并时，分别保存各列表的游标；健康 Bot 也要翻健康简报列表。按天展示可用 `earlier_date` 判断某一天是否已取完整。轮询新回复仍查不带 `before` 的最新页，旧页中的 pending 回复可按 ID 用最新页结果更新。游标按创建时间分页，回复的 `updated_at` 仍用于展示回复写好的时间。

`GET /api/v1/runs` 新增可组合的筛选：

- `job`：完整任务 ID 精确匹配，例如 `extract_todos` 或 `chat:assistant`。
- `status`：`running/succeeded/failed/cancelled` 之一。
- `since` / `until`：带时区 ISO 8601，分别为 `started_at >= since` 和 `started_at < until`，均可单独使用。服务端换算为 UTC 后查询；同时提供时要求 `since < until`，不合法或没有时区返回 422。
- `RunPage` 仍是 `{items: Run[], total}`，`total` 为满足上述全部条件的记录数，与 `limit/offset` 无关。每个 Agent 尝试占一条，回退产生的失败尝试不会计入 `status=succeeded`。

显示「今天成功提炼 N 次」时只需请求 `/api/v1/runs`，传 `job=extract_todos`、`status=succeeded`、`since=今天零点`、`until=明天零点`、`limit=1`。两个零点按设置时区分别计算并保留偏移，使用 URL 查询编码（尤其 `+08:00` 的加号）。这组 job/status 筛选只统计有 harness 的成功提炼，排除调度外层的成功记录和空轮询。省略 status 可查看包含调度记录的完整作业历史。响应 `total` 即 N，`items[0]` 可给出最近开始的成功运行的 harness 和完成时间；空列表表示没有成功记录。不要再数已下载的 50 条。以 Asia/Shanghai 的 2026-11-04 为例：

```json
{
  "job": "extract_todos",
  "status": "succeeded",
  "since": "2026-11-04T00:00:00+08:00",
  "until": "2026-11-05T00:00:00+08:00",
  "limit": 1
}
```

分页查询只读取至多 `limit+1` 条正文，用额外一条判断下一页和日期；计数用 SQL `COUNT(*)`，同一次响应的条目和计数使用同一 SQLite 读快照。计数成本仍与匹配的索引条目数有关，不是常数时间。下列历史查询索引最初在数据库版本 6 引入，不重写历史；当前统一迁移版本为 9：

| 索引 | 原因 |
| --- | --- |
| 已有 `chat_messages_bot(bot_id,created_at)` | SQLite 索引隐含 rowid，可沿 Bot、时间和同刻插入顺序向前翻页，无需新聊天索引 |
| `digests_history(date DESC,plugin)` | 全来源摘要的混合排序，原 `(date,plugin)` 唯一索引无法完整满足该排序 |
| `digests_plugin_history(plugin,date DESC)` | 按来源查历史和计数，避免扫描其他来源 |
| `health_reports_history(date DESC,kind)` | 简报同日的 kind 正序，避免历史列表另建临时排序 |
| `runs_started(started_at DESC,id DESC)` | 全局运行列表和时间范围查询 |
| `runs_job_status_started(job,status,started_at DESC,id DESC)` | 成功提炼次数的索引范围计数，同时支持最近成功记录的排序；原 `runs_job` 保留供仅按 job 的查询 |

## 视频队列 HTTP（app 权限）

两个视频路由都要求 app 权限，mcp-only / health_upload-only 令牌返回 403。插件未启用或已删除时返回 409 `state_conflict`；URL/主题无效返回 422 `invalid_request`；未完成队列达到 50 条时返回 409，重复链接优先返回已有记录。已有记录为 failed 时，重复提交会将原条目重新入队、清除失败与检查点并从头处理，保留原主题；这种重试同样受 50 条上限约束，队列满时返回 409 并保留 failed 状态。主题省略或为 null 时用插件 default_topic（默认「视频」），新建主题采用知识库默认读者。只接受白名单 HTTPS 的单个 B站/YouTube 视频，详见 [video-notes.md](video-notes.md)。

Video 字段：`id,url,platform,video_id,topic,status,step,error,title,duration,created_at,updated_at,output_path`。platform 是 bilibili/youtube；尚未解析的 b23 短链 video_id 为 `short-短码`，夜间解析后更新为真实 ID，可能合并到已有记录。status 为 queued/processing/done/failed/needs_setup；step 为最后完成的阶段（queued/metadata/subtitles/audio/transcript/slides/output/cleanup）；error 为简短中文原因或 null；title、duration 在读到元数据前为 null，duration 单位秒；output_path 为资料的记忆目录相对路径，写出前为 null。时间带时区。返回值不包含原始字幕、文字稿或本机临时文件路径。

稳定任务 `plugin:video_notes:process` 默认 01:30，每轮最多 5 个、逐个处理。立即运行沿用 `POST /api/v1/jobs/plugin:video_notes:process/run`，返回 202 后看队列状态；runs 记录这一轮是否正常执行，单条视频的失败/安装原因以 Video 为准。中断后保存检查点，未处理完的条目下次继续，不另外启动整理 Agent。

## 个人知识库 HTTP（app 权限）

字段 snake_case，path 为相对记忆目录的 `/` 路径，时间 ISO 8601 带时区偏移（可以是 `+00:00`）。所有接口需要 app 权限，mcp-only / health_upload-only / dav-only 令牌返回 403。精确 schema 同步到 OpenAPI。

| 方法 | 路径 | 请求 | 返回 |
| --- | --- | --- | --- |
| GET | /api/v1/knowledge/inputs | 无 | GoodNotes / 课堂照片状态，见下文 |
| GET | /api/v1/knowledge | 无 | Knowledge Overview，见下例 |
| PATCH | /api/v1/knowledge | `{"default_readers":[...],"agents":["Claude Code","Codex","Gemini CLI"]}`，字段均可省略 | 同 GET；未知读者 422 |
| PATCH | /api/v1/knowledge/access | `{"path":"知识库/课程","readers":["助理","作业"]}` | Area；未知区域 404，未知读者 422；系统 locked 总会保留 |
| POST | /api/v1/knowledge/topics | `{"name":"课程","readers":["助理"]}`，readers 省略取 default_readers | 201 Area；已存在 409 |
| GET | /api/v1/knowledge/files | `path=知识库/课程` | `{path,area,readers,entries}`，递归最多 1000 文件，按修改时间倒序，不列文件夹 |
| GET | /api/v1/knowledge/file | `path=知识库/课程/整理/最短路径.md` | File，见下例 |
| GET | /api/v1/knowledge/raw | `path=知识库/课程/第5章.pdf` | 原字节、正确 Content-Type，仅知识库下文件，PDF 最多 500 MiB，其他格式 50 MiB；支持 CSV/XLSX/ZIP 原件，未知类型用 application/octet-stream |
| GET | /api/v1/knowledge/search | `q=最短路径&limit=20` | `{results:[{path,name,title,kind,area,excerpt}]}`；搜全部索引（含系统区和提取文字），短查询 LIKE 兜底 |
| POST | /api/v1/knowledge/files | `path=知识库/课程/第6章.pdf`；body 是原始字节，Content-Type 按文件 | 201 Entry，通常 status=pending，后台安排本地提取；私人主题 csv/xlsx/zip 为 skipped；同名 409，绝不覆盖，超出 50 MiB 返回 413 |

主题名 1–40 字符，不能含 `/`、`\`、`:`，不能以 `.` 开头，不能是「整理」。Agent 名 1–40 字符、不能和 Bot 重名、不能重复。删 Agent 会从全部区域和默认读者移除，该 reader 的旧设备令牌仍有效但无法读任何文件。区域读者和默认值存 SQLite，目录改名算新主题（旧路径设置保留）。

文件路径越界或任一路径组件为符号链接时 422，不存在时 404。上传只能进入已存在的主题及其普通子文件夹，不能进入主题的 `整理/`；扩展名通常限 md/txt/pdf/png/jpg/jpeg/heic/docx；没有读者的私人主题额外允许 csv/xlsx/zip，返回 status=skipped、error=null，知识库不提取这些格式。有读者主题上传 csv/xlsx/zip 返回 422，说明只允许私人主题，防止账单送模型。未支持的磁盘现有文件也会列出：有读者主题 format=other、status=unreadable、error=「不支持这种格式」；无读者主题所有格式均 status=skipped、error=null。q 1–500 字符，limit 1–50。

Overview 示例（readers 实际始终先列八个 Bot，按 CHARACTERS 顺序，再列 Agent 设置顺序）：

```json
{
  "readers": [
    {"id":"助理","kind":"bot","sticker":"aemeath"},
    {"id":"成绩","kind":"bot","sticker":"red_ribbon"},
    {"id":"校园","kind":"bot","sticker":"luosela"},
    {"id":"作业","kind":"bot","sticker":"moning"},
    {"id":"用量","kind":"bot","sticker":"shouanren"},
    {"id":"健康","kind":"bot","sticker":"lily_veil"},
    {"id":"知识库","kind":"bot","sticker":"orange_daisy"},
    {"id":"记账","kind":"bot","sticker":"carlotta"},
    {"id":"Claude Code","kind":"agent","sticker":null},
    {"id":"Codex","kind":"agent","sticker":null}
  ],
  "default_readers":["助理"],
  "areas":[
    {"path":"知识库/课程","name":"课程","kind":"topic","readers":["助理","作业","Claude Code"],"locked":[],"notes":12,"sources":5,"files":17,"pending":1,"failed":0,"updated_at":"2026-10-29T03:10:00+08:00"},
    {"path":"Bots","name":"Bots","kind":"system","readers":["助理","成绩","校园","作业","用量","健康","知识库","记账"],"locked":["助理","成绩","校园","作业","用量","健康","知识库","记账"],"notes":0,"sources":0,"files":13,"pending":0,"failed":0,"updated_at":"2026-10-29T13:40:00+08:00"}
  ],
  "loose_files":0,
  "organize":{"last_run_at":"2026-10-29T03:00:05+08:00","status":"succeeded","next_run_at":"2026-10-30T03:00:00+08:00"}
}
```

areas 先主题（按名字排序）、再系统区。每个主题的 notes 为 `整理/` 下 .md 数，其余为 sources；files=notes+sources。系统区 notes/sources/pending/failed 恒为 0，files 为全部文件数。pending/failed 来自资料状态，failed 包括 unreadable。updated_at 是区域内最新文件修改时间，空区域 null。loose_files 是知识库第一层散文件数。organize 没跑过时三个字段均为 null；调度关闭时 next_run_at 可以为 null。

Files 示例：

```json
{
  "path":"知识库/课程","area":"知识库/课程","readers":["助理","作业","Claude Code"],
  "entries":[
    {"path":"知识库/课程/整理/最短路径.md","name":"最短路径.md","title":"最短路径：Dijkstra","kind":"note","format":"md","size":2048,"modified_at":"2026-10-29T03:10:00+08:00","status":null,"error":null},
    {"path":"知识库/课程/第5章 图.pdf","name":"第5章 图.pdf","title":"第5章 图","kind":"source","format":"pdf","size":1234567,"modified_at":"2026-10-28T21:00:00+08:00","status":"pending","error":null}
  ]
}
```

kind 为 note（主题整理目录内的 .md）、source（主题其余文件或知识库散文件）、file（系统区）。format 为 md/txt/pdf/image/docx/other。笔记 title 取 frontmatter.title，再取第一个 `# ` 标题，最后取不带扩展名的文件名。只有 source 有 status/error，其余为 null。status 为 pending/organized/failed/skipped/unreadable；运行失败保持 pending、写 error，便于重试。`path=知识库` 仅列第一层散文件，area=null、readers=[]；`path=.` 仅列根目录散文件。

File 在 Entry 上新增字段，笔记示例：

```json
{
  "path":"知识库/课程/整理/最短路径.md","name":"最短路径.md","title":"最短路径：Dijkstra","kind":"note","format":"md","size":2048,"modified_at":"2026-10-29T03:10:00+08:00",
  "area":"知识库/课程","readers":["助理","作业","Claude Code"],
  "content":"正文 Markdown（不含 frontmatter）","extracted":false,"truncated":false,
  "sources":[{"path":"知识库/课程/第5章 图.pdf","label":"第 18 页"}],
  "notes":[],"organized_by":"codex","organized_at":"2026-10-29T03:10:00+08:00","status":null,"error":null
}
```

笔记 sources/organized_by/organized_at 来自 frontmatter，notes=[]。资料 sources=[]，notes 是实际由它整理出的笔记路径；PDF/图片/docx 的 content 为本机提取文字且 extracted=true，尚未提取或失败时 content=""、看 status/error。macOS 提取 docx 前还检查 ZIP 成员总展开大小不超过 50 MiB、成员不超过 10,000 个；超出时提取失败，原资料保留。content 最多 200,000 字符，超出 truncated=true；提取缓存本身超过 2,000,000 字节而截断时也标 true。来源与「我的批注」由后端组装，更新整理笔记不会修改旧批注。目录、权限、令牌配置和任务流程见 [knowledge.md](knowledge.md)。

### 知识库入口状态

`GET /api/v1/knowledge/inputs`，必须用 app scope 的 Bearer 令牌，200：

```json
{
  "goodnotes": {
    "enabled": true,
    "address_path": "/dav/goodnotes/",
    "last_backup_at": "2026-10-30T21:14:00+08:00",
    "notebooks": [
      {"path":"数据结构/第5章.pdf","topic":"数据结构","status":"copied","updated_at":"2026-10-30T21:14:00+08:00","message":null},
      {"path":"随手记.pdf","topic":null,"status":"unmapped","updated_at":"2026-10-30T21:12:00+08:00","message":"在 GoodNotes 里放进和主题同名的文件夹，或者在设置里指定"}
    ]
  },
  "class_photos": {
    "enabled": true,
    "permission": "authorized",
    "last_run_at": "2026-10-30T21:00:00+08:00",
    "recent": [{"day":"2026-10-30","course":"数据结构","topic":"数据结构","count":6}],
    "skipped_not_slides": 14
  }
}
```

每个插件未启用、已删除或平台不支持时，对应项严格是 `{"enabled":false}`，不返回其他字段。启用时所有示例字段必有，时间未有记录为 null，列表无数据为 []。goodnotes 的 last_backup_at 为扫描曾见到的最新备份文件修改时间（含 waiting/wrong_format），不是同步成功时间；notebooks 按相对路径排序，topic 可空。status 枚举为 copied/unmapped/conflict/missing/wrong_format/waiting，含义见 [webdav.md](webdav.md)。updated_at 为该备份最后见到的修改时间；missing 保留最后见到的时间；message 可空。

class_photos 的 permission 为 authorized/limited/denied/restricted/not_determined/unavailable；GET 只查询权限，不弹窗。last_run_at 是最近扫描尝试的开始时间（权限不足也记录）；recent 是已入库照片按拍摄日、原课程名和主题汇总，最多 30 组，日期倒序，count 是累计入库数，不随用户删除文件减少；skipped_not_slides 是累计判为「不像课件」的数量。推测依据保存在插件表中，课程名已去掉「（推测）」后缀。

App 页面通过现有插件 PATCH 配置启用和映射；表单字段、默认值、完整配置示例和用户操作步骤集中在 [webdav.md](webdav.md)：建 dav 令牌 → iPad 登录同一 Tailscale → GoodNotes 设置自动备份 WebDAV（密码填令牌、格式 PDF）→ Mac 打开 iCloud 照片 → 首次运行 photo-export authorize 允许访问照片。服务端不返回完整服务器地址或令牌，App 用当前服务器基址拼上 address_path。App 普通上传仍限 50 MiB，大笔记本走 DAV；DAV 方法单独成文且不进入 OpenAPI。

## 数据结构

精确字段、必填项、枚举及界限由同目录 `openapi.json` 定义；测试将其与 FastAPI 生成的 OpenAPI 全量比较。

- **Todo**：`id,title,notes,due_at,has_time,source_plugin,source_refs,source_excerpt,confidence,status,dedup_key,reminder_id,calendar_event_id,created_by,created_at,updated_at,sync_status,sync_error,status_source,reminder_completed_at,user_modified_fields,sources`。`sources` 是只读的来源详情，按 `source_refs` 的顺序列出仍然存在的消息：`{message_id, plugin, chat_name, sender, sent_at}`（哪个插件、哪个会话、谁发的、什么时候），App 用它显示「来自哪个群、谁发的」；写入时（todos_add）不需要也不接受这个字段。`due_at` 可空；`has_time=false` 仅写提醒事项。`source_refs` 是本地 message ID 数组。去重会合并来源。同一天但没有时刻的截止日应按本地午夜发送、`has_time=false`。删除使用 PATCH `status=deleted`。`source_plugin=watchdog`（数据看门狗）的待办只在 App 里，不写提醒事项和日历：`reminder_id`、`calendar_event_id` 为空，`sync_status=synced` 只表示没有待同步的内容，App 应显示为「只在 App 里」。
  另有只读字段 `status_source`（`reminders` 或 null）和 `reminder_completed_at`（带时区时间或 null）：前者表示最近一次状态改变来自苹果提醒事项，后者保留苹果记录的完成时间；取消完成或删除时清空后者。App/Agent 主动修改状态会清空这两个字段。有 Apple 同步桥时，`apple_sync` 每 5 分钟批量回读未删除且有 `reminder_id` 的待办，包括已完成项；勾选、取消勾选会改变同一条待办的 `status`，不会写回提醒事项。已取消的同样不回读。连续两次有效轮询确认缺失（至少间隔 5 分钟）后才设为 `deleted` 并删除日历副本；已完成的待办在提醒事项里被清除时仍是已完成，只清空 `reminder_id`，日历副本保留。一次轮询里至少 3 条且不少于一半未完成项同时缺失时都不算删除，作业报 `apple_reminders_mass_missing`。首次缺失先暂停该项写回；读取失败、无授权或原列表不可见不算删除。机制见 [deployment.md](deployment.md#提醒事项回读)。
  用户在提醒事项里修改标题或截止日期/时刻，也会回读并更新日历副本。只读 `user_modified_fields` 是 `title`、`due_at`、`has_time` 的数组，Agent 新建时为 `[]`，App 手动新建时已保护这三个字段；改日期或时刻会同时保护 `due_at,has_time`。Agent 的 `todos_update` 若试图改变受保护字段，整个 patch 被拒绝，错误码为 `todo_fields_user_modified`；同值写入、修改其他字段或状态仍可用。App 的 HTTP PATCH 允许用户继续修改，保护标记保留。截止时间按同一瞬间比较，只有日期时按运行时区的日期比较，EventKit 的秒精度归一化不会误标为用户修改。
- **TodoPatch**：可选 `title,notes,due_at,has_time,status,source_refs,reason`，未知字段拒绝。`due_at=null` 清空截止时间并删除已有日历事件。`source_refs` 每次传 1–200 个已存在的消息 ID，只追加去重、不会替换原来源，核心据此合并 `source_plugin`；来源校验与其他修改在同一事务内完成。至少修改一项。成功写数据库后异步/即时尝试 Apple 同步，失败保留 `sync_status=pending` 和可重试的 outbox；HTTP 不谎报苹果已成功。
  即时写入已有提醒前也会回读；两端不同字段的修改合并，同一字段上尚未发送的 App 修改优先。Agent 对标题或期限的修改在重试时会与其回读到的原值比较；手机在此期间再次编辑该字段时，保留并保护手机上的值。Agent 更新前先回读用户修改并检查保护；读取失败时拒绝该次 Agent 更新，固定错误码 `apple_sync_failed`，可以重试。只有备注待发送时，手机上的完成、标题、日期仍能更新。移出「AI 待办」列表、原列表不可见或升级旧行尚不能确认原列表时暂停写回，不推断删除；移回或重新读到原项后恢复。回读改标题/日期保留旧去重键，重复提炼或别的消息再报原通知，都合并到原待办，不能重建已删除项。
- **待办状态**：`pending`（待完成）、`completed`（已完成）、`cancelled`（通知已取消，可明确恢复）、`deleted`（已删除，终态）。Todo 的 status 与筛选、PATCH 使用同一枚举，客户端须能解码全部四种值。
- **取消通知**：`status=cancelled` 表示原通知明确取消、撤回或作废，`notes` 写变更日期和原因；待办行和来源保留，提醒事项与日历副本通过现有 outbox 删除，失败后重试。不传状态的 HTTP / MCP 待办列表排除 `deleted` 和 `cancelled`，需要查历史时显式传 `status=cancelled`；`/today` 仅含 `pending`，不会列出取消项。直接将取消项改为 completed 返回 409，整个修改回滚。确认恢复时须显式 PATCH `status=pending`，仅改期限、地点或追加来源不恢复。恢复时如与其他待办的规范标题、期限和 has_time 冲突，返回 409，来源和其他修改一起回滚；已删除项仍为终态。
- **变更后的去重**：`dedup_key` 是不透明的服务端字段。核心保留历次标题、期限和 has_time 的去重键：旧提炼参数带同一消息来源重试，仍合并到原待办，不回退期限、不恢复取消项。同插件、同会话、同发送者转发完全相同的原文时，也可匹配取消历史；仅标题相似、期限不同或来源不明时不自动认定为同一件事。取消项使用独立的存储键，不占用新一期任务的普通去重键。跨来源或改写过的重复通知由 Agent 显式查取消历史，再用 todos_update 追加来源。无期限、同文同名且无法区分周期时只进摘要；已知新一期应在标题注明批次。升级只保留当时已有的键，无法重建升级前未记录的历次改期。
- **Card**：`{type,title,data,plugin}`，type 为 `list/stat/chart/photo_grid/text/todo_list`。`plugin` 是卡片所属插件的 id，由核心填写（插件自己不用给）；不同插件的卡片可能同类型同标题，客户端用 `plugin` 加类型和标题区分。旧 App 可忽略。媒体路径仅供本机解释，不提供截图下载接口。
- **Status**：`online,platform,apple_mode,apple_sync_error,agents,last_run,pending_sync`。`apple_sync_error` 正常为 null；Windows 为 `macos_required`（需要 Mac），此时不调度 Apple 同步，也不执行提醒事项回读；手动运行同步任务报 `macos_required`。待办和 Agent 编辑照常写入本地，待同步队列及旧 Apple ID 保留，不生成假同步 ID。Agent 的 `available` 仅表示找到 CLI；`authentication=unchecked` 表示尚未验证登录或额度。`models` 是和 Bot 对话时可以选的模型 `[{id, name, default}]`：Claude Code 固定是 `fable`、`opus`、`sonnet` 三个别名（各家族最新的模型）；Codex 的来自临时启动的 `codex app-server` 的 `model/list`（不含隐藏的）；DeepSeek（`deepseek`，直接调 API，没有 key 时 `available` 为 false）的来自 `https://api.deepseek.com/models`。Codex 和 DeepSeek 的列表在后端第一次被问到时在后台读，之后每小时最多读一次，还没读到时是空列表。
- **Today**：`date,summary,todos,cards,unsure`。`unsure` 结构与点开条件见上文「保底入口、原文与对账」。包含当天到期及逾期的未完成待办；summary 是当天各消息来源（provides 含 source 的插件，以及没有 manifest 的 share）的摘要按节合并的结果，其他插件的摘要（用量、月报）不在其中；当天没有时为空字符串。按节写的 summary 在返回前精简：条目里写的日期（「11月3日」「11月3日 23:59」「11月6日19点」「11/10」，不写年份时取离现在最近的一年，只写日期按当天结束算）全部已过的条目不返回；「新待办」按最近的日期排序（没写日期的排在后面），只返回前两条，其余的用一行「- 还有 N 件，在待办列表里」代替；全部条目都已过时返回「没有新的重要消息。」。存储的摘要和 `/digests` 不变。`cards` 是所有插件的卡片，各带 `plugin`，App 的仪表盘只用这一个请求就能按 Bot 分组显示；各插件的卡片同时计算，单个插件 10 秒内没算完或出错时只少它自己的卡片。
- **Digest**：`id,date,plugin,summary,created_at`。同一天、同来源占一条记录，MCP 写入按批次幂等追加。摘要按节写（一行加粗节名，下面每条一行、以「- 」开头）时，同一天的批次按节名合并、相同条目只留一条；某批只写「没有新的重要消息。」时，这一天有别的内容就不再加它。其他写法的摘要照旧换行追加。
- **Run**：`id,job,harness,started_at,finished_at,status,error,attempt`。status 为 running/succeeded/failed/cancelled；错误只保存分类，不保存 CLI 输出或原始聊天。每次 Agent 尝试单独记账。CLI 报告了 token 数时（Claude Code 的 `usage`、`total_cost_usd`，Codex 的 `turn.completed`），核心按 run 另存输入（含缓存）、缓存输入、输出 token 数和费用，只存数字；token_usage 插件读这些记录。
- **PluginView**：`id,name,version,icon,description,platforms,permissions,schedule,provides,config_schema,mascot,enabled,config,last_run_at,state,error`。`mascot` 为 `{sticker, accent}` 或 null，来自 manifest：`sticker` 是插件贴图的文件名（不含 `.png`，小写字母、数字、`_`、`-`，最长 64 个字符），对应 `frontend/Design/stickers/<sticker>.png`，可以为 null；`accent` 是强调色（`#RRGGBB`），用于状态圆点和徽章。sticker 为 null、或 App 里没有这张图时，App 显示统一的占位图。贴图流程见 [mascot.md](mascot.md)。state 为 disabled/enabled/unsupported/error。配置只能包含 schema 允许字段；密钥使用环境变量名称引用，不能内嵌 token。
- **PluginPatch**：`enabled?`、`config?`（完整替换配置）。修改后立即重载调度和实例，平台不匹配不能启用。
- **RuntimeSettings**：`timezone,source_cron,extraction_cron,knowledge_organize_cron,dav_max_file_bytes,dav_max_total_bytes,confidence_threshold,harness_order,job_harness,agent_timeout,retry_attempts,retry_base_seconds,todo_alarm_minutes,todo_date_alarm_minutes`。DAV 限额 `dav_max_file_bytes` 默认 524288000（500 MiB），`dav_max_total_bytes` 默认 21474836480（20 GiB），均为正整数字节数，PATCH 后立即生效；DAV 超出返回 507。默认整理 `knowledge_organize_cron="0 3 * * *"`，抓取 `*/30 * * * *`，提炼 `0 7-23/3 * * *`（07/10/13/16/19/22 点），失败指数退避。`job_harness` 可按任务配置优先适配器，其后使用另一个适配器。`todo_alarm_minutes` 默认 `[60,0]`，表示有时刻的待办提前 60 分钟和到点各提醒一次，只设在提醒事项上，它在「AI 待办」日历里的那一条不设闹钟，免得响两次；数组最多 5 项，每项为 `0–10080` 的整数，不可重复，`[]` 关闭这类闹钟。`todo_date_alarm_minutes` 默认 `480`，为只有日期的待办在当天午夜后多少分钟提醒，范围 `0–1439`，480 即运行设置时区的 08:00；此类待办只写提醒事项。已完成待办清空闹钟，已删除待办删除苹果实体，无截止日期的待办没有闹钟。
- **SettingsPatch**：RuntimeSettings 的可选字段，至少一项，不接受 null；cron、时区和闹钟数值立即校验。闹钟设置或时区改变时，已同步待办也会重新入 outbox，并立即尝试同步；失败保留 `pending` 状态，后续 `apple_sync` 作业重试。PATCH 成功表示设置已保存，不保证苹果已同步成功。课表的课前提醒独立使用插件配置 `class_alarm_minutes`（默认 15，0 关闭并清空课程事件），见 [timetable.md](timetable.md)。
- **HealthBatch**：可选 `samples` 数组（0–1000 项），每项 `id,type,value,unit,start_at,end_at,source`；可选 `deleted` 数组（0–1000 项），每项为已删除样本的 ID（1–128 字符）。两者默认空数组，但至少一项非空；同一 ID 不能同时新增和删除。同 ID 重传、重复删除或删除未知 ID 都幂等，结束时间不得早于开始，数值必须有限。删除会移除原始样本并重建上传来源的分析缓存，保留其他样本和 Apple 导出数据。启用 health 插件后，插件按下文「健康样本约定」解读这些样本（算法见 [health-methods.md](health-methods.md)）。
- **HealthAccepted**：`accepted` 新增条数，`duplicates` 已存在条数。
- **HealthReport**：`id,date,kind,status,title,body,error,harness,created_at,updated_at`。`kind` 为 morning（晨间简报）/ weekly（周报，`date` 为周报所在周的周日）；`status` 为 pending（生成中）/ ready / failed（`error` 为固定错误分类，如 `report_rejected`、`all_harnesses_failed`）。`title`、`body` 在 ready 之前可能为空，`body` 是 Markdown。每个日期、每种类型最多一条；按日期倒序。
- **HealthReportPage**：`{items: HealthReport[], total}`。
- **JobAccepted**：`job_id,status=accepted`。

### 插件的预设图

Bot 在聊天回复里除了附现有卡片，还可以附插件按参数现画的图（chat_reply 的 `cards` 写 `{plugin, chart, params}`）。插件实现 `charts()`，返回 `{名字: {title, description, params, build}}`（`backend/core/charts.py`）：`params` 每项是 `{"type": "integer", minimum, maximum, default}` 或 `{"type": "string", enum, default}`，可带 `description`；`build` 是异步函数，按选好的参数返回一张卡片（只用上表已有的类型），没有可画的数据时返回 null。数字全部由插件代码从自己的数据算出，模型只选预设和参数；参数不认识或超出范围时这一项丢掉。chat_cards 只把预设的名字、说明和参数范围给 Bot。能附哪些插件的图和卡片一样：助理能附所有运行中插件的（finance 除外），其他 Bot 只能附自己和同一角色插件的。画一张图最多 10 秒，出错记一条失败的 runs（`plugin:<id>:charts`）。预设图只出现在聊天回复里，不在插件卡片和 `/today` 里。

| 插件 | chart | 参数 | 画什么 |
| --- | --- | --- | --- |
| health | sleep_stages | nights 7–31，默认 14 | 最近几晚的睡眠分期，叠成柱子（和「最近 7 晚睡眠分期」同一种图） |
| health | trend | metric：sleep / resting_hr / hrv / steps / active_energy / respiratory_rate，默认 sleep；days 7–31，默认 30 | 一项指标最近几天的折线，没有可靠数据的日子跳过 |
| token_usage | daily | days 7–60，默认 30 | 每天的 token 消耗折线（有自己用的记录时两条线） |
| token_usage | agents | days 1–60，默认 30 | 最近几天各 Agent 用量的环形图 |
| homework | weekly | weeks 2–12，默认 8 | 每周要交几项作业的柱状图 |
| homework | upcoming | days 1–14，默认 7 | 接下来几天要交、还没交的作业列表 |
| finance | monthly | months 2–12，默认 6 | 最近几个月每月花销和生活费的柱状图（只算账单完整的月份） |
| finance | categories | months_ago 0–11，默认 0 | 某个月钱花在哪儿的环形图，0 是这个月 |

### 卡片 data 约定

`video_notes` 的「视频」卡片归知识库Bot（mascot.sticker=orange_daisy），使用 `list`，`data.items` 为 `{title, subtitle}`；title 为视频标题（尚未读取时为规范链接），subtitle 为「今晚处理」「处理中」「已存入知识库」、具体的安装/空间提示或「失败：原因」。存在 queued、processing、needs_setup 或最近 72 小时的 done/failed 条目时返回，否则为空数组。也通过现有插件卡片和 today 接口返回，不增加卡片类型。

`project_progress`（项目进度）的卡片归知识库Bot（mascot.sticker=orange_daisy），只用已有类型，顺序是：stat「开发天数」（`wide: true`，value 是第一次提交到今天的天数，label 写提交数、合并的 PR 数、状态为「进行中」的工作线，有仓库读不到时注明）、chart「最近 N 天的提交」（`kind: bar`，一个仓库时用 `points` 并在非零的柱子上写数，多个仓库时每个仓库一个 `series`）、list「工作线」（`{title, subtitle, detail?}`：名字、「状态 · 时间」、现在做到哪）、list「接下来」（没勾掉的事，字符串，最多 8 条）、list「最近的里程碑」（今天及以前的最近 5 条，`{title, subtitle: "11/12"}`）。提交数是配置里每个本机仓库所有本地分支上不重复的提交（没合并的也算），PR 数是本地和远程分支上「Merge pull request #N」合并提交里不重复的 N；git 只读，结果缓存 5 分钟。后三张读记忆目录里的 Markdown（默认 `Bots/知识库/项目进度.md`），每次请求重新读；文件不存在时这三张换成一张 text 卡片，说明在哪里建文件。格式见 [project-progress.md](project-progress.md)。不增加 HTTP 路由。

`Card.data` 按 `type` 取下面的字段。客户端遇到不认识的 type 或缺少必填字段时，只把这一张卡片显示为「暂不支持」，不会让整个响应解析失败。数字由后端按需要的精度给出，App 原样显示。

| type | data 字段 | 说明 |
| --- | --- | --- |
| `text` | `text`（必填）、`headline?`、`symbol?`、`table?`、`footnote?` | 一段文字，可以是 Markdown。有 `table: {columns, rows, highlight?}` 时 App 画表格代替 `text`（`text` 写同样的内容，给不会画表格的旧版 App），下面写 `footnote`：`columns` 是列名，第一列是每行的名字；`rows` 每行的格数和列数相同，空字符串是空格子；`highlight` 是要突出的列号（今天那一列） |
| `stat` | `value`（必填，数字或字符串）、`unit?`、`label?`、`delta?`、`trend?`: up/down/flat、`symbol?`、`wide?`、`bar?` | 只有一个数字字段的简单计数（如 `{"count": 3}`）也接受，字段名当作 label。`wide: true`：画成整行的大卡片，数字更大、用 Bot 的颜色，不和别的 stat 拼成方块。`bar: {max, segments: [{x, y}], tone?}`：数字下面一条分段的条，灰色是 `max`，各段按 donut 的颜色和顺序从左往右排（`x` 是名字，`y` 是数），合计超过 `max` 时画满；`tone` 是 `warn` 或 `alert` 时条的底色变橙、红 |
| `list` | `items`（必填）：字符串数组，或 `{title, subtitle?, detail?, progress?, state?}` 数组；`style?`: rows/timeline/rings（默认 rows） | `progress: {value, max, tone?}`：行下面画一条进度条，灰色是 `max`，Bot 的颜色是 `value`；`tone` 是 `warn` 或 `alert` 时画成橙色、红色。`style` 见下 |
| `chart` | `points: [{x, y, label?}]` 或 `series: [{name, points}]`（二选一，必填）、`kind?`: line/bar/donut（默认 line）、`unit?`、`target?`、`zero?`；donut 另有 `value?`、`label?` | `x` 是标签（字符串或数字），`y` 是数字，点的 `label` 是给人看的数（「344 元」）。donut 是圆环：`points` 的每个点是一段，环中间写 `value`（总数）和 `unit`，右边每段的名字、`label` 和占比，下面是 `label`。不认识 donut 的旧版 App 会画成折线。bar 只有一个系列时，点的 `label` 写在柱子上方。`target: {value, label?}`：一条虚线标出目标（比如生活费），超过它的柱子画成橙色。`zero: false`：纵轴贴着数据，不从 0 开始，线下不铺底色，用在远离 0 又变化不大的线上（静息心率） |
| `photo_grid` | `photos`（必填）：`[{id?, caption?, path?}]` | `path` 只是 Mac 本机路径，接口不提供图片下载，App 只显示占位和说明 |
| `todo_list` | `todos`（必填）：Todo 数组 | 与 `/api/v1/todos` 中的 Todo 结构相同 |

`style` 和 `symbol` 只影响画法，旧版 App 不认识时照常按普通卡片显示：

- `style: "timeline"`：一天里按时间排的事，比如课表。每项的 `subtitle` 是 `"08:00–09:35"`，左边写起止时间，中间一条带圆点的线。`state` 可以是 `done`（已经过去，变淡）或 `now`（正在进行，圆点填满并注明），只在当天的卡片上给。
- `style: "rings"`：每项一个圆环，两个一行，比如学业进度。每项都要有 `progress`；`detail` 写成 `"72 / 100"` 时，环中间大字写 72、小字写 / 100。有一项没有 `progress` 就照 rows 画。
- `symbol`：一个 SF Symbol 名字，比如天气的 `cloud.sun.fill`，App 把它画成卡片右上角的小插画。

任何卡片的 `data` 里都可以有 `first: true`：App 把它排在这个 Bot 页面所有卡片的最前面（比如今天的课排在作业前面），其余卡片按插件给的顺序。

App 另有三种卡片风格（素雅、点彩、柔光），在 App 的 设置 → 卡片风格 里选，只存在这台设备上，和接口无关。iPhone 上卡片按给出的顺序一张一行，`stat` 卡片两张一行、画成方块（`wide: true` 的独占一行）；Mac 上按高度分组排成多列。

### finance 插件的卡片与工具

默认停用，贴图 carlotta 对应记账，不增加 HTTP 路由。卡片顺序：stat「10月还能花」（标题是周期开始的月份；data.first=true、wide=true，value 是剩下的整数元，label 含已花/预算、剩余天数、每天可花和数据截至日期，`bar` 按类别分段）、text「记账的建议」（headline、几行建议和 symbol，按余额和花钱节奏由代码生成；缺周期开始的账单时不按本期外推）、chart「10月钱花在哪儿」（kind=donut，points 只有有钱的类别、带 label；本周期还没花销时是上个周期）、chart「每月花销」（kind=bar，各来源共同导入日期范围内开始的最近几个周期，不保证中间无缺口，target 是生活费）、stat「现有资产」、stat「今年额外收入」（label 分红包和分红等）、list「今年的大件」、list「账单」（截至日期、收件箱问题、重复归档记录、在家时花销和家里报销的合计）。在家时的花销和家里报销的支出都不逐笔列出，也不计入大件卡片和大件合计。数据未覆盖部分必须明确未导入；金额可以为负。详见 [finance.md](finance.md)。

工具为 finance_summary / finance_transactions / finance_add / finance_categorize / finance_reimburse / finance_reimbursed / finance_big / finance_split / finance_balance / finance_reread / finance_home / finance_unlock / finance_import_now / finance_report_input / finance_report_write，arguments 与返回口径见 [finance.md](finance.md)。汇总金额单位整分，无商户或对方名字；transactions.limit≤50 且隐藏 linked 重复项。归档重读前核对 SHA，内容改变时拒绝；改类别后立即重判未表态的在家报销规则。只有 reader=记账 且 kind=bot 的角色对话、MAIN、旧无 reader/kind 令牌可用明细或写工具，其他 Bot 仅 finance_summary；拒绝文案「只有记账能看明细」。月报运行使用额外的内部临时身份，只开放 finance_report_input/write，取本周期汇总并写本周期解读；写入拦截已知订单标识及长商品/备注原文，具体边界见 finance.md。财务主题有读者时插件 state=error、error=「财务主题必须没有读者」，卡片提示原因并拒绝操作。

### health 插件的卡片

`GET /api/v1/plugins/health/cards`（也出现在 `/api/v1/today`）按下面的顺序返回，只用上表已有的类型。所有数字都来自 `health_get_daily` / `health_get_sleep` 给 Agent 的同一份按天汇总（`backend/plugins/health/cards.py`），和简报里的数字一致；只和本人的历史基线比较，不是诊断。

| title | type | data |
| --- | --- | --- |
| 健康预警 | list | `items`：每条 `{title: "静息心率高于基线", subtitle: 预警全文, detail: "连续 3 天"}`；没有预警时是一条文字「目前没有需要关注的指标。」；`count`：预警条数。今天的值还没测到的指标沿用昨天的预警，全文里的日期写「昨天」 |
| 今日准备度 | stat | `value`：0–10 的综合准备度（一位小数），有分数的项目不够时是「数据不足」；`unit: / 10`；`label`：四项各自的分数或缺失原因，如「夜间 HRV 8.1 · 静息心率 6.0 · 睡眠 10.0 · 训练负荷 7.5」，当天手动记录过参照时后面加上「你记的健身 App 7.5 · 体感 4」；`delta`：「昨天 7.2」。算法和权重见 [health-methods.md](health-methods.md) 第 7 节；是工程估计，不是医学判断 |
| 昨晚睡眠 | stat | `value`：「7小时12分」，没有记录时是「没有记录」；`label`：入睡、醒来时刻（记录不完整、分成几段时注明）；`delta`：和近 30 天中位数相比多或少多少；`trend` |
| 最近 7 晚睡眠分期 | chart | `kind: bar`，`unit: 小时`，`series`：深睡、核心、REM、清醒（没有分期的夜晚记为「未分期」），每个系列 7 个点，`x` 是「10/12」这样的日期，没有记录的夜晚是 0。一个系列都没有时不返回这张卡片 |
| 作息规律 | stat | `value`：近 7 天睡眠规律性指数（SRI，越高越规律），数据不够时是「数据不足」；`unit: SRI`；`label`：入睡、醒来时间的波动（标准差，分钟）；`delta`：「近 30 天 84.1」 |
| 静息心率 | stat | `value`：次/分，最近 3 天里最近测到的一天；`label`：哪一天、近 30 天中位数（按睡眠心率估算时注明）；`delta`：和 30 天中位数的差，如「+3.2」 |
| HRV | stat | `value`：毫秒；`unit`：「毫秒（RMSSD）」或「毫秒（SDNN）」，即当天用于判断的那一种（`hrv_preferred`：RMSSD 有可靠的基线时用 RMSSD，否则 SDNN）；`label`：哪一天、同一种指标的近 30 天中位数、夜间或白天测量；`delta`：和 30 天中位数相比的百分比，如「-12%」 |
| 训练负荷 | stat | `value`：近 7 天 TRIMP 合计；`label`：近 28 天平均每周 TRIMP、急慢性负荷比（仅供参考），数据缺失时注明 |

`trend`（up / down / flat）只在基线可靠时给出：明显高于 30 天基线为 up，明显低于为 down，其余为 flat；基线不够时没有 `trend` 和 `delta`。之后是最新的晨间简报和周报（`text` 卡片，见 [health-reports.md](health-reports.md)）。完全没有健康数据时只返回一张 list 卡片「健康数据」，说明怎样上传。

### token_usage 插件的卡片

`GET /api/v1/plugins/token_usage/cards` 按下面的顺序返回。一次运行的消耗 = 输入 token（含缓存）+ 输出 token，两个 CLI 算法一致；按 RuntimeSettings 的时区分天。数字有两个来源：核心记下的助手每次 Agent 运行的 token 数（停用插件期间也照常记录），和插件从本机 Claude Code、Codex 会话记录里读到的你自己用的部分（`backend/plugins/token_usage/sessions.py`，见下文）。

| title | type | data |
| --- | --- | --- |
| Claude 用量限额 / Codex 用量限额 | list | 配置 `read_limits`（默认 true）打开时排在最前，标题后面带套餐（「Codex 用量限额 · Plus」）。每个时间窗一项：`{title: "5 小时", subtitle: "14:20 重置", detail: "已用 33%", progress: {value: 33, max: 100}}`，用到 80% 时 `progress.tone` 是 `warn`（橙色），95% 时是 `alert`（红色），然后是「本周」，Claude 还有按模型的「本周 · Opus」；重置时刻按今天、明天、一周内（「周四 08:00」）、更远（「11月3日 09:30」）来写，已经过了重置时刻的窗写 `detail: "已重置"`。没登录 Claude Code、登录过期、这次没读到、或者 Codex 的数来自会话记录时，最后多一项说明，读不到时保留上次读到的数并注明是什么时候的。来源见下文「5 小时和每周限额」 |
| 总共使用的 token | chart | `kind: donut`，所有时间的累计：助手任务、自己用的和自己填的 `manual_totals` 加在一起，按模型分段，顺序是 Claude、GPT（Codex）、DeepSeek（助手经 DeepSeek 跑的任务），然后是其他自己填的，没有用量的不显示。自己填的名字不分大小写，`deepseek` 和 `DeepSeek` 是同一段。`value`：「17.0B」；`unit: tokens`；`label`：「Claude 15.7B · GPT 71.8M · DeepSeek 1.2B」；`points`：`{x: "Claude", y: 15701861056}`，`y` 是 token 数 |
| Claude 余额 / Codex 余额 | stat | 只在配置了 `balances` 的 Agent 显示。`value`：`$12.40`，即 `amount_usd` 减去 `as_of` 当天起每次运行的费用（Claude Code 用 CLI 报告的费用；Codex 用 `prices` 里每百万 token 的单价估算，没填单价的不计费）；`label`：「按最近 7 天的用量还能用 9 天左右」；`delta`：「每天约 -$1.35」；`trend: down` |
| 本月总消耗 | stat | 助手任务加自己用的。`value`：「1.82M」（k、M、B 三级，最多三位有效数字）；`unit: tokens`；`label`：「Claude Code 1.21M · Codex 0.61M」；`delta`：「比上月同期 +14%」，上月同期没有记录时没有 `delta` 和 `trend` |
| 本月自己用的 | stat | 只在读到过会话记录时显示；字段同上，只算自己用的部分 |
| 最近 7 天消耗 | chart | `kind: line`，`unit: 千 tokens`（7 天里有一天到 1000 万时改为 `百万 tokens`），`points`：7 个点，`x` 是「10/17」这样的日期，没有运行的那天是 0。读到过会话记录时改为 `series`：「助手任务」「自己用的」两条线 |

`manual_totals` 是自己填的累计数，给这里读不到的模型用，例如 `[{"name": "DeepSeek", "tokens": 1200000000}]`，只计入「总共使用的 token」，不进其他卡片和摘要；用量变了就改这个数。`name` 是 `Claude`、`GPT` 或 `DeepSeek` 这种这里也在统计的模型时（比如 ChatGPT 个人资料页上的 Codex 累计，`{"name": "GPT", "tokens": 2820000000, "as_of": "2026-10-24"}`），它已经包含了以前统计到的部分，所以代替 `as_of` 那天以前统计到的数，只加上那天起统计到的；不填 `as_of` 就全部代替。

完全没有运行记录、也没有配置余额和 `manual_totals` 时只返回一张 list 卡片「Token 消耗」，说明还没有数据。插件每小时运行一次 `plugin:token_usage:check`：先读会话记录，再把当天的摘要写进 digests（plugin 为 `token_usage`）：昨天的总消耗（其中自己用的多少）和与前天的比较、昨天哪个 Agent 额度用完以及是否自动换成了另一个、余额低于 `low_balance_usd`（默认 15 美元）时的提醒；都没有时不写。

#### 5 小时和每周限额

插件启用时和之后每 10 分钟（`plugin:token_usage:limits`）读一次两家订阅的用量限额，存在插件自己的状态里，卡片读的是最后一次的结果（`backend/plugins/token_usage/limits.py`）：

- **Codex**：临时启动一个 `codex app-server`，用它的 `account/rateLimits/read` 实时读取 5 小时窗（`primary`，300 分钟）和每周窗（`secondary`，10080 分钟）的已用百分比和重置时刻，不消耗 token；Codex 桌面 App 和 CodexBar、iCodex 这类工具也这样读。20 秒内没回答（比如连不上 OpenAI）时，改用 `session_dirs` 里最新的 Codex 会话记录中 `token_count` 事件带的 `rate_limits`，卡片注明是什么时候的数。
- **Claude**：和 Claude Code 自己的 `/usage` 一样，`GET https://api.anthropic.com/api/oauth/usage`（请求头 `anthropic-beta: oauth-2025-04-20`），令牌是 Claude Code 登录后存在登录钥匙串里的「Claude Code-credentials」，用 `/usr/bin/security` 读取。返回的 `five_hour`、`seven_day`、`seven_day_<模型>` 各有 `utilization`（百分比）和 `resets_at`。令牌只发给 api.anthropic.com，不保存、不写日志，也不在这里刷新（刷新会替换 Claude Code 自己的登录）。Claude Code 每次运行都会自己续登录，所以令牌过期时（不常用命令行版本时大约一天），插件自动运行一次最小的请求让它续上：`claude -p ok`，一行系统提示、不带工具、MCP、设置和会话记录，只用几个 token，最多半小时一次，然后再读；配置 `renew_claude_login: false` 可以关掉。没登录 Claude Code 时卡片提示在终端运行 `claude` 登录。
- 这两个接口都不是公开约定，字段对不上的部分会跳过；读不到时保留上次的数。

#### 自己用的部分：读会话记录

配置 `read_sessions`（默认 true）打开时，插件在启用时和每小时读一次这些目录下所有 `.jsonl` 文件里的 token 数：`session_dirs`（默认 `~/.claude/projects`、`~/.codex/sessions`），以及数据目录下的 `token_usage/imports`（默认 `backend/.state/token_usage/imports`）。

- 只取数字：Claude Code 每一行回复记录的 `message.usage`，Codex `token_count` 事件里会话累计值的增量。不保存聊天内容，没变过的文件不会再读。
- Claude Code 和桌面 App 的统计面板一样逐行累加：一次回复拆成思考、文字、工具调用几行，每行都带同一份用量，每行都算。继续之前的会话时新文件会重复带上旧的记录，这些也再算一遍。所以数字和面板一致，但比实际计费的 token 多好几倍（复制过来的 Windows 记录约 4.3 倍）。改用这种算法之前按回复去重导入的记录，会在下一次读取时删掉重读。
- 同一个文件里的同一行（Claude Code 按文件名、行的 uuid 和它在文件里第几次出现）只算一次，所以同一份文件复制多次、或者在两个目录里各有一份，都不会重复计数。
- 助手自己的运行用 `--no-session-persistence` / `--ephemeral`，不写会话记录，不会和「助手任务」重复。
- 自己用的部分通常走订阅，不从余额里扣。
- 两个 CLI 的会话文件格式不是公开约定，升级后对不上的行会被跳过。

**加上另一台电脑（比如 Windows）的用量**：把那台电脑上的 `%USERPROFILE%\.claude\projects` 和 `%USERPROFILE%\.codex\sessions` 两个文件夹（在 WSL 里用的命令行版本则是 WSL 里的 `~/.claude/projects`、`~/.codex/sessions`，资源管理器里是 `\\wsl$\<发行版>\home\<用户名>\`）整个复制到 Mac 的 `backend/.state/token_usage/imports/` 下面（放在子文件夹里也行，比如 `imports/windows/claude`、`imports/windows/codex`），下一次 `plugin:token_usage:check` 就会读到；想马上看到就 `POST /api/v1/jobs/plugin:token_usage:check/run`。以后再复制一遍新的覆盖进去即可，旧记录不会重复计数。

### weekly_review 插件的卡片

`weekly_review` 默认停用，`platforms: [any]`、`mascot.sticker: aemeath`，归助理Bot。任务 `plugin:weekly_review:review` 默认每周日 21:00 用 DeepSeek 写回顾，失败沿用核心 Agent 回退。配置：`review_agent`（deepseek / claude_code / codex）、`review_cron`（默认 `0 21 * * 0`，crontab 星期 0、7 是周日）。

`GET /api/v1/plugins/weekly_review/cards` 在周日写好后到下周二结束返回一张 `text` 卡片，`title: 本周回顾`、`data: {text: Markdown}`，没有写好或过期返回空数组。周报留在记忆目录 `周报/<ISO年>-W<周>.md`，同周覆盖保留笔记，写好当天通过每日钩子提到 `今天.md`。统计口径、活动状态缺失和完成时间限制见 [weekly-review.md](weekly-review.md)。不增加 HTTP 路由。

### 健康样本约定（health 插件）

App 或快捷指令按天上传 HealthKit 样本，后端去重、按天汇总并计算基线。一次性导入的 Apple 健康导出文件会转换成同一种内部结构。

- `id`：稳定且唯一，建议用 HealthKit 样本的 UUID。同一 ID 重传会被忽略，所以不要用固定 ID 上传「到目前为止」的累计值。
- `source`：HealthKit 的来源名称（例如「某某的Apple Watch」「某某的iPhone」）。名称里含 watch/手表 的算手表、含 iPhone/手机 的算手机，其余算其他设备；不过导入过的健康导出（它记着设备型号）已经认出是手表或手机的来源名称，上传时也照样算（手表可以随便改名，比如「红线所系之处」）。同一时段有多个来源时优先采用手表。来源名称只用于去重，不会出现在给模型的摘要里。
- 时间带时区偏移；未识别的 `type` 或单位会被忽略并计数，超出合理范围的值或起止跨度超过 2 天的样本视为伪迹丢弃。

| type（任选其一） | value / unit | 说明 |
| --- | --- | --- |
| `heart_rate` 或 `HKQuantityTypeIdentifierHeartRate` | 次/分，`count/min` | 原始读数最好；也可以上传小时平均值（起止跨 1 小时），但小时平均值只用来算佩戴时长 |
| `resting_heart_rate` / `…RestingHeartRate` | `count/min` | Apple Watch 的每日静息心率 |
| `hrv_sdnn` / `…HeartRateVariabilitySDNN` | `ms` | Apple 一直记录的 HRV 是 SDNN |
| `hrv_rmssd` / `…HeartRateVariabilityRMSSD` | `ms`（也接受 `s`） | iOS / watchOS 27 新增；和 SDNN 分开保存、分开建基线，有可靠基线时优先使用 |
| `apple_fitness_readiness` | 0–10，`unit` 留空 | 手动记录：苹果「健身」App 的准备指数，只用于校准准备度。`start_at`、`end_at` 写记录的时刻，归入这个时刻所在的那一天；同一天多条时用时间最晚的一条。见 [readiness-calibration.md](readiness-calibration.md) |
| `perceived_readiness` | 1–5 的整数，`unit` 留空 | 手动记录：自己的体感（1 很累，5 状态很好），规则同上 |
| `respiratory_rate` / `…RespiratoryRate` | `count/min` | |
| `oxygen_saturation` / `…OxygenSaturation` | `%`，0–1 或 0–100 | |
| `wrist_temperature` / `…AppleSleepingWristTemperature` | `degC` 或 `degF` | 起止为当晚睡眠 |
| `step_count`（或 `steps`）/ `…StepCount` | `count` | 原始样本，或一条覆盖当地整天的当日合计（当天结束后上传一次） |
| `active_energy` / `…ActiveEnergyBurned` | `kcal`、`Cal`、`kJ` | 同上 |
| `vo2max` / `…VO2Max` | `mL/min·kg` | |
| `sleep_in_bed`、`sleep_asleep`、`sleep_awake`、`sleep_core`、`sleep_deep`、`sleep_rem` | value 填 1，unit 留空 | **推荐写法**：一条样本对应一段睡眠阶段 |
| `HKCategoryTypeIdentifierSleepAnalysis` | value 为 HealthKit 原始整数值 | 按 inBed=0、asleepUnspecified=1、awake=2、asleepCore=3、asleepDeep=4、asleepREM=5 解读（需以 SDK 头文件 `HKCategory.h` 人工核实；用上一行的别名可以不依赖它） |
| `workout:<运动类型>` 或 `HKWorkoutActivityType<运动类型>` | 时长，`min`、`s`、`h` | 起止为训练开始、结束 |
| `workout_heart_rate_average`、`workout_heart_rate_max` | `count/min` | 起止与训练相同；只在没有训练期心率读数时用于 TRIMP |

### 和 Bot 对话

Bot 是 App 左边栏里的角色：`bot_id` 为 `assistant`（助理Bot，总体待办）、`knowledge`（知识库Bot，管知识库；GoodNotes、课堂照片、视频插件都没启用时也能对话），或者一个**已启用、且不是消息来源**的插件 ID（如 `homework`、`campus`、`grades`、`health`、`finance`）。消息来源（provides 含 `source` 的插件，如 qq_screen）只读消息、不对话，和不存在、已停用的 Bot 一样返回 404。

聊天按 manifest 贴图带入对应角色的 `Bots/<角色名>/soul.md` 和 `记忆.md`（分别最多开头 8000、6000 字符），并可通过 MCP 查询用户授予该角色读取权限的只读知识库；映射、缺失处理和导入方式见[角色人设与知识库](bots.md)，HTTP 请求和返回结构不变。

- **ChatSend**：`text`（1–2000 字，去掉首尾空白后不能为空）、`bot_name?`（1–40 字，App 里这个 Bot 的名字，如「作业Bot」，只用来让 Agent 知道自己叫什么；没给时用插件名）、`harness?`（`claude_code` / `codex` / `deepseek`，这句先交给哪个 Agent；它失败时照常换另一个）、`model?`（这个 Agent 用哪个模型，要和 `harness` 一起给，取 Status 里那个 Agent 的 `models[].id`，如 Claude Code 的 `opus`；1–64 位字母、数字和 `._:-`）。两个都没给时按设置里的 harness 顺序和模型默认值。
- **ChatMessage**：`id,bot_id,author,text,status,error,created_at,updated_at,replies?,cards?`。`cards` 为可选（缺省或 null）的 ChatCard 数组，最多 2 张，只出现在 bot 的回复上，App 画在回复文字下面；旧客户端可忽略。`replies` 为可选（缺省或 null）短回答数组，最多 4 条，每条 1–30 字；旧客户端可忽略。只在最新聊天消息是 ready 的 bot 消息、属于今天且在当前可见时间线中、带 replies 时在该气泡正下方画胶囊，点击后立即收起并通过原 POST messages 发送所选文字。`author` 为 me / bot；`status` 为 ready / pending（Agent 正在回复，`text` 为空）/ failed（`error` 为固定分类：`all_harnesses_failed`、`interrupted` 后端重启时还没回复完、`cancelled`、`chat_failed` 提示词等准备步骤失败）。回复的 `updated_at` 是写好的时间。
- **ChatAccepted**：`message`（用户这句，ready）和 `reply`（pending）。App 之后轮询 GET，直到 reply 不再是 pending。
- **ChatCard**：Card 加 `taken_at`（`{type,title,data,plugin,taken_at}`，`plugin` 必有）。Bot 在 chat_reply 里点名附上的插件卡片在那一刻的样子：后端当时调用这个插件的卡片、按标题取出并存进这条回复，之后不再变化，所以翻旧对话时数字还是当时的。App 在卡片下注明「截至 taken_at」，看最新的去这个 Bot 的卡片面板或仪表盘。模型只给出插件和标题，卡片里的数字不经过模型；聊天历史（下一轮提示词）里只写「附了卡片：标题」。
- **ChatPage**：`{items: ChatMessage[], total, has_more, next_before, earlier_date}`，按创建时间倒序；翻页规则见「历史分页与运行计数」。DigestPage、HealthReportPage 同样新增这三个分页字段。

后端收到一句话后，按 `extract_todos` 同样的 harness 顺序和回退（给了 `harness` 时它排第一，`model` 只用在它身上）启动 Agent（提示词 `prompts/chat.md`，运行记录的 job 为 `chat:<bot_id>`），Agent 通过 MCP 查数据、按需新增或修改待办和学校插件的记录，最后调用 `chat_reply` 写回复；没写回复就算失败并换下一个 harness。用户说的每句话同时存成 `chat` 来源的一条消息（会话名「和<名字>的对话」，已标记为已处理，提炼任务不会再读），所以从对话里建立的待办和其他记录一样有来源可查。finance 的对话也照常保存，只有 finance_unlock 用到的密码会从这一轮用户说的话（两个消息表）和 chat_reply 中遮盖，详见 `docs/finance.md`。对话当前原文仍会发给 Agent 使用的云端模型，CLI/模型服务自己的留存不由后端保证。

## MCP

官方 MCP Python SDK；stdio：在 backend 目录执行 `uv run python -m core.mcp_stdio`，通过受鉴权的本机内部桥接访问同一服务，不能独立启动第二个调度器。Streamable HTTP 地址 `/mcp`；需要 `ASSISTANT_TOKEN` 或带 `mcp` 权限的设备令牌。

远程接入：Streamable HTTP 的 `/mcp` 默认只接受本机地址和 `ASSISTANT_HOST` 作为 Host；其他设备上的 Agent（经 Tailscale）要用的主机名写进 `ASSISTANT_MCP_ALLOWED_HOSTS`（逗号分隔，只能是主机名或「主机名:端口」，不支持通配符；不写端口时任何端口都接受）。Host 不在列表里返回 421；没有或写错 `Authorization: Bearer <token>` 返回 401，令牌没有 `mcp` 权限返回 403，远程和本机一样必须带 token；建议每台远程设备用自己的、带 `--reader` 且只有 `mcp` 权限的设备令牌。stdio 方式不变。配置步骤见 [remote-mcp.md](remote-mcp.md)。

后台 Agent 每次运行的临时 Grant 另带 `tools`（内置工具）、`plugin_tools`（插件 id 与原始 hook 名对）、`memory_read_paths`（精确文件或末尾 `/` 的目录前缀），默认全部为空。`tools/list` 和 `/internal/tools` 按当前 Grant 筛选；stdio 桥接与 DeepSeek 复用同一结果。硬调范围外工具返回 `isError=true`、固定原因 `tool_not_allowed`，不会执行 handler；范围外文件返回 `memory_not_allowed`，memory_search 在 limit 前过滤路径和原有 reader ACL。内部 HTTP 错误格式仍为 `tool_failed: <原因>`，MCP/stdio 使用 CallToolResult，不改变 App v1 schema。主令牌、设备令牌和 Bot 对话的现有权限不变。

提炼只获得消息（含 messages_search 查旧消息）、待办、摘要、按需打开已授权会话和 memory_read/search，以及 homework_mark、campus_event_add、campus_leave_record、campus_list、grades_record；只读 `关于我.md` 和本次本地日期的 `每日/<日期>/今天.md`，不列出也不能调用任何 finance_* 工具。主题整理只有 memory_read/search 和 knowledge_note_write，没有插件工具。财务月报只有本周期的 finance_report_input/write；其他后台任务的完整工具及记忆读写范围见 [knowledge.md 的任务表](knowledge.md#后端强制执行的边界)。

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| messages_get_unprocessed | plugin?: string, limit: 1–200 | message[]：`{id, plugin, chat_name, sender, sent_at, text, priority, flags, outgoing, preview, revised, note}`，不含截图路径/二进制或命中的身份。`flags` 是本机算出的 `self_listed` / `self_missing` / `self_joined` 数组，前两者 priority=2（最高）；配置的本人身份在正文/发送人/会话名中替换为 `[本人]`。`outgoing=true` 表示本人发出，预览一律 false。`preview=true` 是会话列表预览；`revised=true` 是已处理预览现换成全文；`note` 是对应说明或 null，见 [screen-reading.md](screen-reading.md) |
| messages_search | query: string, chat_name?: string, days: 1–90（默认 14）, limit: 1–200（默认 20） | 与 messages_get_unprocessed 相同的 message[]，已处理和未处理都查；仅查已入库的消息正文，query 按空白拆词、全部命中，`%`、`_` 按字面匹配。query 不能为空白，chat_name 精确匹配该来源脱敏后显示的会话名；按服务端当前时间向前 days × 24 小时（含边界，不含未来消息），按 sent_at 倒序、同刻按 id 排序，过滤后取 limit；空白 query 或越界参数报错。旧消息可供新消息补充背景并加入 source_refs；不因搜索命中重新建立旧待办。 |
| screen_open_chat | plugin: qq_screen / wechat_screen, chat_name: string | 请求固定脚本下一轮读一个群，必要时立即排抓取任务；只限已启用插件的 `open_on_demand ∩ whitelist` 且受每日上限限制。返回 `{status: "scheduled", message: "已安排"}` 或 `{status: "denied", reason}`；原因：`screen_plugin_not_allowed`、`plugin_disabled`、`screen_open_not_allowed`、`screen_open_daily_limit`。请求本身不点界面；点开会把群及手机标为已读。群名用配置中的完整名字 |
| messages_mark_processed | ids: string[] | `{updated}` |
| todos_add | todo: TodoCreate | `{disposition: added/merged/below_threshold, todo?: Todo}` |
| todos_list | status?: enum, limit, offset | TodoPage |
| todos_update | id: string, patch: TodoPatch | Todo |
| digest_write | date: YYYY-MM-DD, plugin: string, summary: string | Digest |
| memory_search | query: string, limit: 1–50 | `{path,excerpt}`[] |
| memory_read | path: 相对路径（md/txt/pdf/图片/docx） | `{path,content}`；提取资料另有 `extracted: true,truncated` |
| memory_write | path: 相对 .md 路径, content: string | `{path}` |
| knowledge_note_write | path,title,content,sources: `[{path,label}]` | `{path}`；仅当前主题整理临时 Grant 可调用，写本主题整理目录，保留批注 |
| video_notes_add | arguments: {url, topic?} | Video；仅 video_notes 启用时提供。三个入口共用 URL/主题校验，重复返回原记录；只排队，夜间本地处理，不索取登录信息 |
| chat_cards | reply_id: string | `{cards: [{plugin,type,title}], charts: [{plugin,chart,title,description,params}]}`：这一轮的 Bot 能附的卡片（只有名字）和能按参数现画的预设图（见「插件的预设图」）。助理能附所有运行中插件的卡片（finance 除外）；其他 Bot 只能附自己插件和同一角色（同一贴图）插件的卡片，所以财务卡片只有记账能附。临时身份只能查本轮 reply_id |
| chat_reply | reply_id: string, text: 1–4000 字, replies?: 回答选项（最多4条1–30字短回答）, cards?: 最多 2 项，卡片写 `{plugin,title}`，预设图写 `{plugin,chart,params?}`（都取自 chat_cards） | ChatMessage。cards 由后端在这一刻取出卡片或画好预设图，存成快照（ChatCard），格式不对、不能附、找不到或参数不在范围内的那项丢掉，文字照常保存。只用于 Bot 对话：写回 pending 的回复，每轮一次；replies 仅 goals 启用时的助理回复有效，不合格则丢掉选项、保留文字并记录无内容日志；临时身份只能写本轮 reply_id，其他受限身份拒绝，主令牌和旧无 reader/kind 令牌保持兼容 |

`memory_search` 索引 UTF-8 Markdown、文本和本地提取的 PDF/图片/docx，按 Grant 可读区域在 SQL 中过滤后再取 limit；`memory_read` 无权时报告「没有读取这个文件夹的权限」。`memory_write` 拒绝所有 `Bots/*/soul.md` 和 `知识库/` 路径；Bot 临时身份只能写自己的角色 Markdown 和每日文件，Agent reader 只能写可读系统区且不能写 Bots。知识库散文件对受限 Agent 不可见，MAIN 和旧无 reader 令牌保持全读兼容。详见 [knowledge.md](knowledge.md)。

启用 health 插件后提供以下工具。返回的 JSON 只包含按天汇总的指标、个人基线、偏离程度和预警标记，不包含原始采样、来源名称或个人信息；只描述数据和趋势，不是诊断：

| 工具 | arguments | 返回 |
| --- | --- | --- |
| health_get_daily | `date?`: YYYY-MM-DD（默认今天）, `days?`: 1–31（默认 2） | 每天的数据状态、各指标及 7/30 天偏离（`hrv_ln_sdnn`、`hrv_ln_rmssd` 各自对比自己的基线，`hrv_preferred` 指明当天用哪一种）、训练与 TRIMP、预警、综合准备度 `readiness`（0–10 和四项分数）、当天手动记录的参照 `checkin`（`apple_fitness_readiness`、`perceived_readiness`，没记时为 null）；训练负荷；`readiness_note` |
| health_get_sleep | `date?`, `days?`: 1–31（默认 7） | 每晚入睡/醒来、时长、各阶段、分段与小睡、偏离；睡眠规律性指数（SRI）与作息波动 |
| health_get_baselines | `date?` | 截至该日（不含当天）每项指标的 7/30 天中位数、MAD、天数与可靠性 |
| health_report_input | `kind`: morning/weekly, `date`: YYYY-MM-DD | 这份简报允许引用的全部数据：上面三个工具对该日的结果、代码换算好的写法（display）、周报的本周与上周对比（week）、长期记忆里的训练目标（goals）。任务开始时算好并保存；缓存中的 goals 仍检查调用者当前的原文件读权限，无权、文件删除或成为符号链接时返回 null |
| health_report_write | `kind`, `date`, `title`（1–80 字）, `body`（Markdown，1–6000 字） | `{accepted: true, report}`；或 `{accepted: false, errors: [{code, values, message}], attempts_left}`。code 为 unsupported_numbers（数字不在 health_report_input 里）、chinese_numerals、medical_terms、too_many_attempts。接受后报告为 ready，写入长期记忆，见 [health-reports.md](health-reports.md) |

启用 `weekly_review` 后另有以下工具，数字与缺数据规则见 [weekly-review.md](weekly-review.md)：

| 工具 | arguments | 返回 |
| --- | --- | --- |
| weekly_review_input | `date?`: YYYY-MM-DD（默认今天，取所在周） | 周一到周日的待办、作业、课程、活动、健康、Token 汇总，以及下周截止和考试标题；仅数字、日期、状态和标题，不含聊天原文 |
| weekly_review_write | `date`: YYYY-MM-DD，`text`: Markdown，1–6000 字 | `{accepted:true,week,path}`；写回周报与卡片，同周覆盖保留「## 笔记」 |

TodoCreate：`title` 必填；`notes=""`, `due_at=null`, `has_time=false`, `source_plugin`, `source_refs`（至少一个有效消息 ID）, `source_excerpt`（必须确实出现在所引用消息中）, `confidence`（0–1）, `created_by="agent"`。核心计算 dedup_key，Agent 不可绕过去重。低置信度仅写摘要，不入提醒事项。相似但非精确条目由 Agent 通过 todos_list 判断，并以已有待办的规范标题/时间再次 todos_add 合并来源。

改期、提前、延期和换地点先通过 `todos_list` 查原事项（含跨 QQ / 飞书来源），并用 `todos_list(status="cancelled")` 分页查取消历史，再 `todos_update` 修改期限或备注，追加新 `source_refs`，在备注保留原安排和变更日期。取消用上述 `cancelled` 状态；明确恢复须显式设 `pending`。已更新的事项不再 `todos_add`。仅凭相同标题、不同截止日期不会由核心自动合并，以免把不同批次的作业误合并。

消息、群公告和记忆均视为数据；其中的指令不得改变白名单、读取文件、发送消息或调用 shell。先成功写待办/摘要，最后标记本批消息；出错不标记，重试通过去重恢复。

插件 MCP 工具名已以 `<插件 id>_` 开头时直接使用（例如 health 插件的 `health_get_daily`），否则命名为 `plugin_<id>_<name>`；与内置工具或其他插件工具重名的不注册，并记一条 `plugin_tool_name_conflict` 运行记录。停用、删除即时不可调用。插件工具的参数统一放在 `arguments` 对象里，工具说明来自插件方法的 docstring。`/internal/tools` 与 `/internal/tools/{name}` 是 stdio 桥接用的内部协议，也必须鉴权（需要 `mcp` 权限），不属于 App v1 API。

stdio 桥接拒绝含路径、查询参数等字符的工具名，不能借工具名访问 App 路由；DeepSeek 仅派发本次工具列表里出现的名称。来源配置的 `self_names` 也用于脱敏待办的 MCP 返回、每日待办/作业文件，以及作业和周报工具中的待办标题；数据库与 App 中的本地原记录保留。


### 目标跟进与主动消息

`goals` 默认关闭。启用后并入助理，不新增 HTTP 路径或卡片类型。
今天这一步是 `source_plugin=goals` 的普通 Todo，只在 App 中，永不写提醒事项或日历。
合并其他来源后仍按 App-only 处理。次日未做的状态改为 `cancelled`；默认待办列表不返回它。
PATCH 完成记为 App 操作，MCP todos_update 完成记为 Agent 操作；只有前者可计作目标推进。
核心将状态变化与 from_agent 一起持久化，插件处理出错也能续读。

Bot 主动消息写入原 chat_messages，author=bot、status=ready，沿用未读点与历史分页。
核心按调用方提供的幂等键去重。所有 Bot 共用日额度，默认 2 次；其中目标最多 1 次。
默认 23:00–07:30、启用课表中的上课时间延后发送，逾期的旧问题不会集中补发。
勾选后的数字追问和用户消息的模板回复属于对操作的回应，不占主动额度。
额度与安静时段由 goals 插件配置调整；主动消息不产生系统推送或响铃。

插件只对已送达、未过期、尚未回答且仍为最后一条 ready Bot 消息的目标问题匹配完全相同的现成回答，
直接模板回复；其他文字（包括自由说「做了」「那次不算」「先歇一周」）照常由 Agent 结合上下文回复。
建目标最多四问；计划和修改先暂存，只有最新一条用户消息为「定下来」、且此前已显示确认选项才应用。
目标工具仅允许本轮助理对话身份调用；与后台提炼和周回顾的授权分开。
仅在紧接最新复述、用户回答「定下来」或「再改改」的这一轮收窄对话工具；其他输入保持普通授权并丢掉旧草稿。goals_propose 的 `{abandon:true}` 也可放弃草稿。
复述仅包含目标、做到就算成、节奏、这周。问法、现成回答、带类型的卡点、较小一步、期限称谓等由 Agent 按用户的话填入 profile，不作为确认表单展示。
MCP 工具为 `goals_get / goals_propose / goals_confirm / goals_record / goals_revoke`，详见 [goals.md](goals.md)。
问题过期、取消或已回答后，聊天 API 的 replies 清空；后续回复收掉旧选项。其他 Bot 或 goals 未启用时，chat_reply 忽略 replies；选项错误不影响文字保存。
目标支持 dropped（先放下）：日常修改需确认，结果未达标时可通过程序问题的现成回答直接放下。achieved 和 dropped 释放开放目标名额。
周回顾输入的可选 `goals.finished` 保存当周达成和放下的目标结局，即使开始新目标仍会返回。没有活动或当周结局时为 null，关闭时不加字段。
