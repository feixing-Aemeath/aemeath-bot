# 24 小时助手 HTTP / MCP 接口 v1

状态：v1。前端只依赖本文和 `openapi.json`。时间使用带时区 ISO 8601，服务端保存 UTC，今日按设置的时区（默认 Asia/Shanghai）计算。ID 为字符串。

## 连接与鉴权

HTTP 基址默认 `http://127.0.0.1:8765`。所有请求（包括 OpenAPI、MCP）必须带 `Authorization: Bearer <token>`；只有 `/dav/` 另接受 HTTP Basic（密码为设备令牌），见 [WebDAV 入口](webdav.md)。仅允许 loopback 或 Tailscale IP 绑定。公网接入不受支持。生产只启动一个 Uvicorn worker。

令牌分为主令牌、设备令牌和每次运行的临时令牌：

- **`ASSISTANT_TOKEN`**：Mac 本机的令牌，至少 32 字符，从环境或本机 `.env` 加载，有全部权限，供后台任务使用。不返回给客户端；用户自运行 Agent 建议使用带 reader 的设备令牌。
- **设备令牌**：每台设备一个，在 Mac 上用 `uv run python -m scripts.tokens create <名称> --scope <权限> [--days N] [--reader "Claude Code"]` 创建、`list` 查看、`revoke <ID 或名称>` 作废。作废和过期在下一个请求就生效，不用重启后端。格式是 `a24_` 加 43 个字符；创建时只显示一次，数据库里只保存它的 SHA-256 哈希（`device_tokens` 表），列表里只显示开头 8 个字符。

| 权限 | 能访问 |
| --- | --- |
| `app` | App 的 HTTP 接口：`/api/v1/…`、`/openapi.json`。还可访问 `/dav/`；其他不属于 MCP 的路由（包括以后新增的）也算 App 接口 |
| `dav` | 只能访问 `/dav/`（Bearer / Basic）；不能调用 `/api/v1/…`、OpenAPI 或 MCP，详见 [webdav.md](webdav.md) |
| `mcp` | MCP：`/mcp`（Streamable HTTP）和 stdio 桥接用的 `/internal/tools…`；不能调用 App 接口 |
| `health_upload` | 只有 `POST /api/v1/health/samples`，给快捷指令这类只上传健康数据的自动化用 |

一个令牌可以有几个权限（例如 iPhone 用 `app`，学校电脑只用 `mcp`）。设备令牌可带 Agent `reader`，限制 MCP 记忆文件的区域；旧的无 reader 令牌仍未限定。Bot 对话和主题整理使用只存在内存、运行结束即撤销的临时 mcp 令牌。App 权限的知识库接口始终代表用户本人，可读全部区域。范围不限制待办、消息和插件工具，见 [knowledge.md](knowledge.md)。

无 Token、错误、已作废或已过期的 Token 返回 401；Token 有效但没有这个路由的权限返回 403（`{"detail": "insufficient_scope"}`，响应头 `WWW-Authenticate: Bearer error="insufficient_scope"`）；不存在返回 404；状态冲突返回 409；字段错误返回 422；故障返回不包含聊天内容或密钥的错误码。分页使用 `limit`（1–200，默认 50）和 `offset`（默认 0），列表响应为 `{items: [...], total: n}`。

## HTTP 路由

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | /api/v1/status | 无 | Status |
| GET | /api/v1/today | 无 | Today |
| GET | /api/v1/todos | status?: pending/completed/deleted; limit; offset | TodoPage |
| PATCH | /api/v1/todos/{id} | TodoPatch | Todo |
| GET | /api/v1/digests | date?: YYYY-MM-DD; plugin?; limit; offset | DigestPage |
| GET | /api/v1/runs | limit; offset | RunPage |
| POST | /api/v1/jobs/{job_id}/run | 无 | 202 JobAccepted；已运行返回 409 |
| GET | /api/v1/plugins | 无 | PluginView[] |
| PATCH | /api/v1/plugins/{id} | PluginPatch | PluginView |
| DELETE | /api/v1/plugins/{id} | 无 | 204；保留历史消息和待办 |
| GET | /api/v1/plugins/{id}/cards | 无 | Card[] |
| POST | /api/v1/health/samples | HealthBatch | HealthAccepted |
| GET | /api/v1/health/reports | date?: YYYY-MM-DD; kind?: morning/weekly; status?: pending/ready/failed; limit; offset | HealthReportPage |
| GET | /api/v1/settings | 无 | RuntimeSettings |
| PATCH | /api/v1/settings | SettingsPatch | RuntimeSettings |
| GET | /api/v1/bots/{bot_id}/messages | limit; offset | ChatPage，新的在前 |
| POST | /api/v1/bots/{bot_id}/messages | ChatSend | 202 ChatAccepted；这个 Bot 上一句还没回复完返回 409 |
| POST | /api/v1/videos | `{url,topic?}` | 200 Video；重复返回已有条目，失败的条目重新排队 |
| GET | /api/v1/videos | limit; offset | `{items: Video[], total}`，按入队时间倒序 |

稳定任务 ID：`knowledge_organize`（每晚资料整理，默认 03:00）、`extract_todos`、`apple_sync`、`backup`、`memory_index`、`daily_notes`（每日文件夹，见 [daily-folder.md](daily-folder.md)）、`watchdog`（数据看门狗，见 [watchdog.md](watchdog.md)）、`fetch:<plugin_id>`；插件可以提供 `plugin:<plugin_id>:<job_name>`。不存在/已停用的任务不能运行。202 表示进入执行，不表示成功；轮询 runs 查询结果。插件删除为持久化卸载标记，不删除历史数据或执行任意目录删除，重启不会自动恢复。

## 视频队列 HTTP（app 权限）

两个视频路由都要求 app 权限，mcp-only / health_upload-only 令牌返回 403。插件未启用或已删除时返回 409 `state_conflict`；URL/主题无效返回 422 `invalid_request`；未完成队列达到 50 条时返回 409，重复链接优先返回已有记录。主题省略或为 null 时用插件 default_topic（默认「视频」），新建主题采用知识库默认读者。只接受白名单 HTTPS 的单个 B站/YouTube 视频，详见 [video-notes.md](video-notes.md)。

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
| GET | /api/v1/knowledge/raw | `path=知识库/课程/第5章.pdf` | 原字节、正确 Content-Type，仅知识库下文件，PDF 最多 500 MiB，其他格式 50 MiB |
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

笔记 sources/organized_by/organized_at 来自 frontmatter，notes=[]。资料 sources=[]，notes 是实际由它整理出的笔记路径；PDF/图片/docx 的 content 为本机提取文字且 extracted=true，尚未提取或失败时 content=""、看 status/error。content 最多 200,000 字符，超出 truncated=true；提取缓存本身超过 2,000,000 字节而截断时也标 true。来源与「我的批注」由后端组装，更新整理笔记不会修改旧批注。目录、权限、令牌配置和任务流程见 [knowledge.md](knowledge.md)。

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

- **Todo**：`id,title,notes,due_at,has_time,source_plugin,source_refs,source_excerpt,confidence,status,dedup_key,reminder_id,calendar_event_id,created_by,created_at,updated_at,sync_status,sync_error,sources`。`sources` 是只读的来源详情，按 `source_refs` 的顺序列出仍然存在的消息：`{message_id, plugin, chat_name, sender, sent_at}`（哪个插件、哪个会话、谁发的、什么时候），App 用它显示「来自哪个群、谁发的」；写入时（todos_add）不需要也不接受这个字段。`due_at` 可空；`has_time=false` 仅写提醒事项。`source_refs` 是本地 message ID 数组。去重会合并来源。同一天但没有时刻的截止日应按本地午夜发送、`has_time=false`。删除使用 PATCH `status=deleted`。`source_plugin=watchdog`（数据看门狗）的待办只在 App 里，不写提醒事项和日历：`reminder_id`、`calendar_event_id` 为空，`sync_status=synced` 只表示没有待同步的内容，App 应显示为「只在 App 里」。
- **TodoPatch**：可选 `title,notes,due_at,has_time,status`，未知字段拒绝。`due_at=null` 清空截止时间并删除已有日历事件。至少修改一项。成功写数据库后异步/即时尝试 Apple 同步，失败保留 `sync_status=pending` 和可重试的 outbox；HTTP 不谎报苹果已成功。
- **Card**：`{type,title,data}`，type 为 `list/stat/chart/photo_grid/text/todo_list`。媒体路径仅供本机解释，不提供截图下载接口。
- **Status**：`online,platform,apple_mode,apple_sync_error,agents,last_run,pending_sync`。`apple_sync_error` 正常为 null；Windows 为 `macos_required`（需要 Mac），此时待办保留在 App 和待同步队列，不生成假同步 ID。Agent 的 `available` 仅表示找到 CLI；`authentication=unchecked` 表示尚未验证登录或额度。`models` 是和 Bot 对话时可以选的模型 `[{id, name, default}]`：Claude Code 固定是 `fable`、`opus`、`sonnet` 三个别名（各家族最新的模型）；Codex 的来自临时启动的 `codex app-server` 的 `model/list`（不含隐藏的）；DeepSeek（`deepseek`，直接调 API，没有 key 时 `available` 为 false）的来自 `https://api.deepseek.com/models`。Codex 和 DeepSeek 的列表在后端第一次被问到时在后台读，之后每小时最多读一次，还没读到时是空列表。
- **Today**：`date,summary,todos,cards`。包含当天到期及逾期的未完成待办；summary 来自当日摘要。
- **Digest**：`id,date,plugin,summary,created_at`。同一天、同来源占一条记录，MCP 写入按批次幂等追加。
- **Run**：`id,job,harness,started_at,finished_at,status,error,attempt`。status 为 running/succeeded/failed/cancelled；错误只保存分类，不保存 CLI 输出或原始聊天。每次 Agent 尝试单独记账。CLI 报告了 token 数时（Claude Code 的 `usage`、`total_cost_usd`，Codex 的 `turn.completed`），核心按 run 另存输入（含缓存）、缓存输入、输出 token 数和费用，只存数字；token_usage 插件读这些记录。
- **PluginView**：`id,name,version,icon,description,platforms,permissions,schedule,provides,config_schema,mascot,enabled,config,last_run_at,state,error`。`mascot` 为 `{sticker, accent}` 或 null，来自 manifest：`sticker` 是插件贴图的文件名（不含 `.png`，小写字母、数字、`_`、`-`，最长 64 个字符），对应 `frontend/Design/stickers/<sticker>.png`，可以为 null；`accent` 是强调色（`#RRGGBB`），用于状态圆点和徽章。sticker 为 null、或 App 里没有这张图时，App 显示统一的占位图。贴图流程见 [mascot.md](mascot.md)。state 为 disabled/enabled/unsupported/error。配置只能包含 schema 允许字段；密钥使用环境变量名称引用，不能内嵌 token。
- **PluginPatch**：`enabled?`、`config?`（完整替换配置）。修改后立即重载调度和实例，平台不匹配不能启用。
- **RuntimeSettings**：`timezone,source_cron,extraction_cron,knowledge_organize_cron,dav_max_file_bytes,dav_max_total_bytes,confidence_threshold,harness_order,job_harness,agent_timeout,retry_attempts,retry_base_seconds,todo_alarm_minutes,todo_date_alarm_minutes`。DAV 限额 `dav_max_file_bytes` 默认 524288000（500 MiB），`dav_max_total_bytes` 默认 21474836480（20 GiB），均为正整数字节数，PATCH 后立即生效；DAV 超出返回 507。默认整理 `knowledge_organize_cron="0 3 * * *"`，抓取 `*/30 * * * *`，提炼 `0 7-23/3 * * *`（07/10/13/16/19/22 点），失败指数退避。`job_harness` 可按任务配置优先适配器，其后使用另一个适配器。`todo_alarm_minutes` 默认 `[60,0]`，表示有时刻的待办提前 60 分钟和到点各提醒一次，只设在提醒事项上，它在「AI 待办」日历里的那一条不设闹钟，免得响两次；数组最多 5 项，每项为 `0–10080` 的整数，不可重复，`[]` 关闭这类闹钟。`todo_date_alarm_minutes` 默认 `480`，为只有日期的待办在当天午夜后多少分钟提醒，范围 `0–1439`，480 即运行设置时区的 08:00；此类待办只写提醒事项。已完成待办清空闹钟，已删除待办删除苹果实体，无截止日期的待办没有闹钟。
- **SettingsPatch**：RuntimeSettings 的可选字段，至少一项，不接受 null；cron、时区和闹钟数值立即校验。闹钟设置或时区改变时，已同步待办也会重新入 outbox，并立即尝试同步；失败保留 `pending` 状态，后续 `apple_sync` 作业重试。PATCH 成功表示设置已保存，不保证苹果已同步成功。课表的课前提醒独立使用插件配置 `class_alarm_minutes`（默认 15，0 关闭并清空课程事件），见 [timetable.md](timetable.md)。
- **HealthBatch**：`samples` 数组（1–1000 项），每项 `id,type,value,unit,start_at,end_at,source`。同 ID 重传幂等，结束时间不得早于开始，数值必须有限。接口本身只负责存储；启用 health 插件后，插件按下文「健康样本约定」解读这些样本（算法见 [health-methods.md](health-methods.md)）。
- **HealthAccepted**：`accepted` 新增条数，`duplicates` 已存在条数。
- **HealthReport**：`id,date,kind,status,title,body,error,harness,created_at,updated_at`。`kind` 为 morning（晨间简报）/ weekly（周报，`date` 为周报所在周的周日）；`status` 为 pending（生成中）/ ready / failed（`error` 为固定错误分类，如 `report_rejected`、`all_harnesses_failed`）。`title`、`body` 在 ready 之前可能为空，`body` 是 Markdown。每个日期、每种类型最多一条；按日期倒序。
- **HealthReportPage**：`{items: HealthReport[], total}`。
- **JobAccepted**：`job_id,status=accepted`。

### 卡片 data 约定

`video_notes` 的「视频」卡片归知识库Bot（mascot.sticker=orange_daisy），使用 `list`，`data.items` 为 `{title, subtitle}`；title 为视频标题（尚未读取时为规范链接），subtitle 为「今晚处理」「处理中」「已存入知识库」、具体的安装/空间提示或「失败：原因」。存在 queued、processing、needs_setup 或最近 72 小时的 done/failed 条目时返回，否则为空数组。也通过现有插件卡片和 today 接口返回，不增加卡片类型。

`Card.data` 按 `type` 取下面的字段。客户端遇到不认识的 type 或缺少必填字段时，只把这一张卡片显示为「暂不支持」，不会让整个响应解析失败。数字由后端按需要的精度给出，App 原样显示。

| type | data 字段 | 说明 |
| --- | --- | --- |
| `text` | `text`（必填）、`headline?`、`symbol?`、`table?`、`footnote?` | 一段文字，可以是 Markdown。有 `table: {columns, rows, highlight?}` 时 App 画表格代替 `text`（`text` 写同样的内容，给不会画表格的旧版 App），下面写 `footnote`：`columns` 是列名，第一列是每行的名字；`rows` 每行的格数和列数相同，空字符串是空格子；`highlight` 是要突出的列号（今天那一列） |
| `stat` | `value`（必填，数字或字符串）、`unit?`、`label?`、`delta?`、`trend?`: up/down/flat、`symbol?`、`wide?`、`bar?` | 只有一个数字字段的简单计数（如 `{"count": 3}`）也接受，字段名当作 label。`wide: true`：画成整行的大卡片，数字更大、用 Bot 的颜色，不和别的 stat 拼成方块。`bar: {max, segments: [{x, y}], tone?}`：数字下面一条分段的条，灰色是 `max`，各段按 donut 的颜色和顺序从左往右排（`x` 是名字，`y` 是数），合计超过 `max` 时画满；`tone` 是 `warn` 或 `alert` 时条的底色变橙、红 |
| `list` | `items`（必填）：字符串数组，或 `{title, subtitle?, detail?, progress?, state?}` 数组；`style?`: rows/timeline/rings（默认 rows） | `progress: {value, max, tone?}`：行下面画一条进度条，灰色是 `max`，Bot 的颜色是 `value`；`tone` 是 `warn` 或 `alert` 时画成橙色、红色。`style` 见下 |
| `chart` | `points: [{x, y, label?}]` 或 `series: [{name, points}]`（二选一，必填）、`kind?`: line/bar/donut（默认 line）、`unit?`、`target?`；donut 另有 `value?`、`label?` | `x` 是标签（字符串或数字），`y` 是数字，点的 `label` 是给人看的数（「344 元」）。donut 是圆环：`points` 的每个点是一段，环中间写 `value`（总数）和 `unit`，右边每段的名字、`label` 和占比，下面是 `label`。不认识 donut 的旧版 App 会画成折线。bar 只有一个系列时，点的 `label` 写在柱子上方。`target: {value, label?}`：一条虚线标出目标（比如生活费），超过它的柱子画成橙色 |
| `photo_grid` | `photos`（必填）：`[{id?, caption?, path?}]` | `path` 只是 Mac 本机路径，接口不提供图片下载，App 只显示占位和说明 |
| `todo_list` | `todos`（必填）：Todo 数组 | 与 `/api/v1/todos` 中的 Todo 结构相同 |

`style` 和 `symbol` 只影响画法，旧版 App 不认识时照常按普通卡片显示：

- `style: "timeline"`：一天里按时间排的事，比如课表。每项的 `subtitle` 是 `"08:00–09:35"`，左边写起止时间，中间一条带圆点的线。`state` 可以是 `done`（已经过去，变淡）或 `now`（正在进行，圆点填满并注明），只在当天的卡片上给。
- `style: "rings"`：每项一个圆环，两个一行，比如学业进度。每项都要有 `progress`；`detail` 写成 `"72 / 100"` 时，环中间大字写 72、小字写 / 100。有一项没有 `progress` 就照 rows 画。
- `symbol`：一个 SF Symbol 名字，比如天气的 `cloud.sun.fill`，App 把它画成卡片右上角的小插画。

任何卡片的 `data` 里都可以有 `first: true`：App 把它排在这个 Bot 页面所有卡片的最前面（比如今天的课排在作业前面），其余卡片按插件给的顺序。

App 另有三种卡片风格（素雅、点彩、柔光），在 App 的 设置 → 卡片风格 里选，只存在这台设备上，和接口无关。iPhone 上卡片按给出的顺序一张一行，`stat` 卡片两张一行、画成方块（`wide: true` 的独占一行）；Mac 上按高度分组排成多列。

### finance 插件的卡片与工具

默认停用，贴图 carlotta 对应记账，不增加 HTTP 路由。卡片顺序：stat「10月还能花」（标题是周期开始的月份；data.first=true、wide=true，value 是剩下的整数元，label 含已花/预算、剩余天数、每天可花和数据截至日期，`bar` 按类别分段）、text「记账的建议」（headline、几行建议和 symbol，按余额和花钱节奏由代码生成）、chart「10月钱花在哪儿」（kind=donut，points 只有有钱的类别、带 label；本周期还没花销时是上个周期）、chart「每月花销」（kind=bar，账单全覆盖的最近几个周期，target 是生活费）、stat「现有资产」、stat「今年额外收入」（label 分红包和分红等）、list「今年的大件」、list「账单」（截至日期、收件箱问题、重复归档记录、在家时花销和家里报销的合计）。在家时的花销和家里报销的支出都不逐笔列出。数据未覆盖部分必须明确未导入；金额可以为负。详见 [finance.md](finance.md)。

工具为 finance_summary / finance_transactions / finance_add / finance_categorize / finance_reimburse / finance_reimbursed / finance_unlock / finance_import_now / finance_report_input / finance_report_write，arguments 与返回口径见 [finance.md](finance.md)。汇总金额单位整分，无商户或对方名字；transactions.limit≤50 且隐藏 linked 重复项。只有 reader=记账 且 kind=bot 的角色对话、MAIN、旧无 reader/kind 令牌可用明细或写工具，其他 Bot 仅 finance_summary；拒绝文案「只有记账能看明细」。月报运行使用额外的内部临时身份，只能读汇总、取本周期输入并写本周期解读。财务主题有读者时插件 state=error、error=「财务主题必须没有读者」，卡片提示原因并拒绝操作。

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
- **ChatMessage**：`id,bot_id,author,text,status,error,created_at,updated_at`。`author` 为 me / bot；`status` 为 ready / pending（Agent 正在回复，`text` 为空）/ failed（`error` 为固定分类：`all_harnesses_failed`、`interrupted` 后端重启时还没回复完、`cancelled`）。回复的 `updated_at` 是写好的时间。
- **ChatAccepted**：`message`（用户这句，ready）和 `reply`（pending）。App 之后轮询 GET，直到 reply 不再是 pending。
- **ChatPage**：`{items: ChatMessage[], total}`，按时间倒序。

后端收到一句话后，按 `extract_todos` 同样的 harness 顺序和回退（给了 `harness` 时它排第一，`model` 只用在它身上）启动 Agent（提示词 `prompts/chat.md`，运行记录的 job 为 `chat:<bot_id>`），Agent 通过 MCP 查数据、按需新增或修改待办和学校插件的记录，最后调用 `chat_reply` 写回复；没写回复就算失败并换下一个 harness。用户说的每句话同时存成 `chat` 来源的一条消息（会话名「和<名字>的对话」，已标记为已处理，提炼任务不会再读），所以从对话里建立的待办和其他记录一样有来源可查。finance 是隐私例外：用户原文只用于当轮，两个消息表只保存占位说明，不进入后续历史；finance_unlock 登记的密码从当轮 chat_reply 中遮盖。对话当前原文仍会发给 Agent 使用的云端模型，CLI/模型服务自己的留存不由后端保证。

## MCP

官方 MCP Python SDK；stdio：在 backend 目录执行 `uv run python -m core.mcp_stdio`，通过受鉴权的本机内部桥接访问同一服务，不能独立启动第二个调度器。Streamable HTTP 地址 `/mcp`；需要 `ASSISTANT_TOKEN` 或带 `mcp` 权限的设备令牌。

远程接入：Streamable HTTP 的 `/mcp` 默认只接受本机地址和 `ASSISTANT_HOST` 作为 Host；其他设备上的 Agent（经 Tailscale）要用的主机名写进 `ASSISTANT_MCP_ALLOWED_HOSTS`（逗号分隔，只能是主机名或「主机名:端口」，不支持通配符；不写端口时任何端口都接受）。Host 不在列表里返回 421；没有或写错 `Authorization: Bearer <token>` 返回 401，令牌没有 `mcp` 权限返回 403，远程和本机一样必须带 token；建议每台远程设备用自己的、带 `--reader` 且只有 `mcp` 权限的设备令牌。stdio 方式不变。配置步骤见 [remote-mcp.md](remote-mcp.md)。

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| messages_get_unprocessed | plugin?: string, limit: 1–200 | message[]：`{id, plugin, chat_name, sender, sent_at, text, priority, flags, outgoing, preview, revised, note}`，不含截图路径/二进制或命中的身份。`flags` 是本机算出的 `self_listed` / `self_missing` / `self_joined` 数组，前两者 priority=2（最高）；配置的本人身份在正文/发送人/会话名中替换为 `[本人]`。`outgoing=true` 表示本人发出，预览一律 false。`preview=true` 是会话列表预览；`revised=true` 是已处理预览现换成全文；`note` 是对应说明或 null，见 [screen-reading.md](screen-reading.md) |
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
| chat_reply | reply_id: string, text: 1–4000 字 | ChatMessage。只用于 Bot 对话：写回 pending 的回复，每轮一次 |

`memory_search` 索引 UTF-8 Markdown、文本和本地提取的 PDF/图片/docx，按 Grant 可读区域在 SQL 中过滤后再取 limit；`memory_read` 无权时报告「没有读取这个文件夹的权限」。`memory_write` 拒绝所有 `Bots/*/soul.md` 和 `知识库/` 路径；Bot 临时身份只能写自己的角色 Markdown 和每日文件，Agent reader 只能写可读系统区且不能写 Bots。知识库散文件对受限 Agent 不可见，MAIN 和旧无 reader 令牌保持全读兼容。详见 [knowledge.md](knowledge.md)。

启用 health 插件后提供以下工具。返回的 JSON 只包含按天汇总的指标、个人基线、偏离程度和预警标记，不包含原始采样、来源名称或个人信息；只描述数据和趋势，不是诊断：

| 工具 | arguments | 返回 |
| --- | --- | --- |
| health_get_daily | `date?`: YYYY-MM-DD（默认今天）, `days?`: 1–31（默认 2） | 每天的数据状态、各指标及 7/30 天偏离（`hrv_ln_sdnn`、`hrv_ln_rmssd` 各自对比自己的基线，`hrv_preferred` 指明当天用哪一种）、训练与 TRIMP、预警、综合准备度 `readiness`（0–10 和四项分数）、当天手动记录的参照 `checkin`（`apple_fitness_readiness`、`perceived_readiness`，没记时为 null）；训练负荷；`readiness_note` |
| health_get_sleep | `date?`, `days?`: 1–31（默认 7） | 每晚入睡/醒来、时长、各阶段、分段与小睡、偏离；睡眠规律性指数（SRI）与作息波动 |
| health_get_baselines | `date?` | 截至该日（不含当天）每项指标的 7/30 天中位数、MAD、天数与可靠性 |
| health_report_input | `kind`: morning/weekly, `date`: YYYY-MM-DD | 这份简报允许引用的全部数据：上面三个工具对该日的结果、代码换算好的写法（display）、周报的本周与上周对比（week）、长期记忆里的训练目标（goals）。任务开始时算好并保存 |
| health_report_write | `kind`, `date`, `title`（1–80 字）, `body`（Markdown，1–6000 字） | `{accepted: true, report}`；或 `{accepted: false, errors: [{code, values, message}], attempts_left}`。code 为 unsupported_numbers（数字不在 health_report_input 里）、chinese_numerals、medical_terms、too_many_attempts。接受后报告为 ready，写入长期记忆，见 [health-reports.md](health-reports.md) |

启用 `weekly_review` 后另有以下工具，数字与缺数据规则见 [weekly-review.md](weekly-review.md)：

| 工具 | arguments | 返回 |
| --- | --- | --- |
| weekly_review_input | `date?`: YYYY-MM-DD（默认今天，取所在周） | 周一到周日的待办、作业、课程、活动、健康、Token 汇总，以及下周截止和考试标题；仅数字、日期、状态和标题，不含聊天原文 |
| weekly_review_write | `date`: YYYY-MM-DD，`text`: Markdown，1–6000 字 | `{accepted:true,week,path}`；写回周报与卡片，同周覆盖保留「## 笔记」 |

TodoCreate：`title` 必填；`notes=""`, `due_at=null`, `has_time=false`, `source_plugin`, `source_refs`（至少一个有效消息 ID）, `source_excerpt`（必须确实出现在所引用消息中）, `confidence`（0–1）, `created_by="agent"`。核心计算 dedup_key，Agent 不可绕过去重。低置信度仅写摘要，不入提醒事项。相似但非精确条目由 Agent 通过 todos_list 判断，并以已有待办的规范标题/时间再次 todos_add 合并来源。

消息、群公告和记忆均视为数据；其中的指令不得改变白名单、读取文件、发送消息或调用 shell。先成功写待办/摘要，最后标记本批消息；出错不标记，重试通过去重恢复。

插件 MCP 工具名已以 `<插件 id>_` 开头时直接使用（例如 health 插件的 `health_get_daily`），否则命名为 `plugin_<id>_<name>`；与内置工具或其他插件工具重名的不注册，并记一条 `plugin_tool_name_conflict` 运行记录。停用、删除即时不可调用。插件工具的参数统一放在 `arguments` 对象里，工具说明来自插件方法的 docstring。`/internal/tools` 与 `/internal/tools/{name}` 是 stdio 桥接用的内部协议，也必须鉴权（需要 `mcp` 权限），不属于 App v1 API。
