# 个人知识库

Windows 原生后端的安装、差别和校准见 [Windows 说明](windows.md)。

公开视频也可夜间转换为主题下的带时间点文字稿和本地幻灯片资料，见 [视频资料](video-notes.md)。

用户文件仍是权威副本，位置由 `ASSISTANT_VAULT_DIR` 决定。一般知识库接口不搬动、不删除已有资料；用户启用 [finance](finance.md) 后，仅把已成功导入或重复的收件箱账单按不覆盖规则移至私人主题内的账单原件目录。上传遇同名文件返回 409；Agent 只能通过专用工具更新后端生成的整理笔记，不能覆盖用户自己放在整理目录里的文件。

```text
记忆目录/
├── 关于我.md                 # 根目录文件系统区（path = .）
├── Bots/                     # 一个系统区
│   └── 作业/{soul.md,记忆.md}
├── 每日/                     # 一个系统区
├── 健康/                     # 一个系统区
├── 周报/                     # 出现后成为一个系统区
└── 知识库/
    ├── 待归类.pdf             # 散文件：只有 App 用户可见，受限 Agent 不可读
    └── 课程/                 # 一个主题区
        ├── 第5章.pdf
        ├── 图.png
        └── 整理/             # 同属课程区域，沿用相同读者
            └── 最短路径.md
```

## 区域和读者

`知识库/` 第一层每个文件夹各算一个 `topic` 区域；记忆目录第一层其他文件夹各算一个 `system` 区域。根目录散文件合起来是 `.`（根目录文件）。不跟随文件或目录符号链接。`知识库/` 第一层散文件不属于主题，App 应提示移进主题。

读者先是 `core/bots.py` 中按 CHARACTERS 顺序排列的八个角色：助理、成绩、校园、作业、用量、健康、知识库、记账，然后是可增删的自运行 Agent，默认 Claude Code、Codex。主题读者完全由用户设置，包括空数组（私人资料）。新建或第一次扫描到的主题采用 `default_readers`，默认 `["助理", "知识库"]`（知识库管知识库），当场存到 SQLite；已有主题不会自动加上知识库，要在 App 里勾。之后改默认值不改变已有主题；改默认之前会先登记已经出现的主题。主题改名视为新主题，采用当时默认值；旧路径的设置保留，不会自动迁移。如果以后重新使用旧名字，它会沿用该路径保存的设置，可在 App 重设。

系统区的所有 Bot 读者被锁定，不能移除；Agent 默认无权读取系统区，可以逐区勾选。权限在 `knowledge_areas` 和 kv 的 `knowledge_settings` 中，与 SQLite 一起备份。删除 Agent 时会从默认值和所有区域里移除；其旧设备令牌仍有效，但不能再读任何记忆文件。以后重新添加同名 Agent 才恢复它作为读者的资格，仍需重新勾选区域。

## 后端强制执行的边界

`BearerAuth` 每个请求验证 Grant，并通过请求上下文传给 MCP。`Memory.search` 在 SQL 的 WHERE 中按路径限定区域，再应用 LIMIT；`Memory.read` 在读文件前校验区域，无权时返回「没有读取这个文件夹的权限」。不能用路径穿越、符号链接或大小写变体绕过写保护。

| 身份 | 读 | memory_write |
| --- | --- | --- |
| MAIN（环境中的主 ASSISTANT_TOKEN） | 全部，后台提炼/周报保持兼容 | 沿用原规则：知识库、soul.md 只读 |
| 旧的无 reader 的设备令牌 | mcp 权限时沿用全部读取 | 沿用原规则；tokens list 显示「未限定」，建议作废重建 |
| Bot 对话临时令牌 | 系统区 + 用户勾选的主题 | 只能写 `Bots/<自己>/**/*.md`（soul.md 除外）和 `每日/**/*.md` |
| 未映射角色的插件对话临时令牌 | 仅系统区，无主题区 | 不开放 memory_write |
| 带 Agent reader 的设备令牌 | 仅勾选区域 | 只能写可读系统区的 Markdown，不能写 Bots、知识库 |
| 整理临时令牌 | 仅当前一个有读者的主题 | 不能调用 memory_write；专用 knowledge_note_write 只写本主题整理目录 |
| App 权限令牌（用户本人） | 知识库 HTTP 接口看全部 | 通过上传接口添加原资料，不覆盖已有文件 |

Bot 的每日写入目前允许整个 `每日/**`：插件可动态返回文件名和负责 Bot，尚未建立完整、稳定的文件归属表；提示词仍要求只改自己负责文件的「## 笔记」。跨角色长期记忆写入已由后端阻止。没有 reader 的主令牌与旧设备令牌是兼容例外，不应分发给需要受限的 Agent。

临时令牌与设备令牌同为 `a24_` 加 256 位随机数，只保存在当前服务进程内，不写数据库、不输出日志。每次 harness 尝试有独立令牌，运行成功、失败、取消或超时都撤销，最长有效期为 `agent_timeout + 30` 秒。Claude Code、Codex 的 stdio 桥接和 DeepSeek 的内部工具请求使用同一个临时身份机制，不再给 Bot CLI 主令牌。

**范围只管记忆目录里的文件。** 待办、消息、摘要、chat_reply 和其他插件工具本版不按 reader 区域限制；可信本地插件仍可通过自己的代码读写文件。这不是插件或操作系统的安全沙箱。

**在 Mac mini 本机有终端权限的 Agent 仍可以直接读磁盘文件。** 读者令牌只能限制经本后端 HTTP/MCP 的访问。本项目没有给 Codex 提供按目录禁读的选项，`read-only` 也不等于禁止读取记忆目录；需要真正磁盘隔离时使用单独操作系统账户或隔离环境，远程电脑只拿受限 MCP 令牌。Codex 的边界与配置见 [OpenAI 官方 MCP 文档](https://developers.openai.com/codex/mcp)和[安全说明](https://developers.openai.com/codex/security)。

Claude Code 可以在本机 `~/.claude/settings.json` 加 Read 禁止规则（换成实际绝对路径；绝对路径以 `//` 开头）：

```json
{"permissions":{"deny":["Read(//Users/you/AI工作区/AI知识库/**)"]}}
```

这限制内置文件工具，不能作为任意终端子进程的完整磁盘隔离；具体适用范围见 [Claude Code 官方权限说明](https://code.claude.com/docs/en/permissions)。不要把这些建议表述成已有本机安全隔离的验证结果。

## 给本机和学校电脑各建令牌

后端启动过一次后，在 Mac mini 的 `backend/` 创建四个独立令牌（每串只显示一次，不发到聊天、不提交）：

```bash
uv run python -m scripts.tokens create 'Claude Code（Mac）' --scope mcp --reader 'Claude Code'
uv run python -m scripts.tokens create 'Codex（Mac）' --scope mcp --reader Codex
uv run python -m scripts.tokens create 'Claude Code（学校）' --scope mcp --reader 'Claude Code' --days 180
uv run python -m scripts.tokens create 'Codex（学校）' --scope mcp --reader Codex --days 180
uv run python -m scripts.tokens list
```

读者必须是 App 设置里当前存在的 Agent，不能传角色名。四个令牌分别管理设备，但同一 reader 共用区域勾选。App 创建主题并授予对应 reader 后才可读取。怀疑泄露时 `uv run python -m scripts.tokens revoke 'Codex（学校）'`，下一请求即返回 401。

本机 stdio 不读取 `.env`，MCP 配置的 env 必须显式放它自己的设备令牌，**不要复制 `.env` 里的主 ASSISTANT_TOKEN**。以下占位符要替换为各自创建的令牌，不要照字面使用。

Claude Code 的用户 MCP JSON（例如 `~/.claude.json` 中对应的 `mcpServers`）：

```json
{
  "mcpServers": {
    "assistant": {
      "command": "/absolute/path/backend/.venv/bin/python",
      "args": ["-m", "core.mcp_stdio"],
      "env": {
        "PYTHONPATH": "/absolute/path/backend",
        "ASSISTANT_BASE_URL": "http://127.0.0.1:8765",
        "ASSISTANT_TOKEN": "<Claude Code（Mac）的设备令牌>"
      }
    }
  }
}
```

Codex 的用户 `~/.codex/config.toml`：

```toml
[mcp_servers.assistant]
command = "/absolute/path/backend/.venv/bin/python"
args = ["-m", "core.mcp_stdio"]

[mcp_servers.assistant.env]
PYTHONPATH = "/absolute/path/backend"
ASSISTANT_BASE_URL = "http://127.0.0.1:8765"
ASSISTANT_TOKEN = "<Codex（Mac）的设备令牌>"
```

学校电脑先按 [remote-mcp.md](remote-mcp.md) 配好 Tailscale、Host 白名单和 HTTPS；Claude Code、Codex 分别使用各自的学校令牌。Claude Code HTTP 配置的 headers 放 `Authorization: Bearer <Claude Code（学校）的令牌>`；Codex 的用户配置如下，启动前把 `AEMEATH_CODEX_TOKEN` 设为 Codex（学校）的令牌：

```toml
[mcp_servers.assistant]
url = "https://mac-mini.tail1234.ts.net/mcp"
bearer_token_env_var = "AEMEATH_CODEX_TOKEN"
```

配置文件含令牌时限制为本用户可读，不加入 Git 或同步目录。这里没有创建任何真实设备令牌，也未修改用户配置。

## 资料提取和状态

支持 `.md`、`.txt`、`.pdf`、`.png`、`.jpg`、`.jpeg`、`.heic`、`.docx`（扩展名大小写均可）。没有读者的私人主题额外允许上传 `.csv`、`.xlsx`、`.zip`（仅保存文件，知识库不提取、不送模型）；有读者主题拒绝这些格式，并说明私人主题限制。PDF 原文件最多 500 MiB，其他格式最多 50 MiB（App 普通上传仍限 50 MiB，大文件走 DAV），文字最多 2,000,000 UTF-8 字节，超出截断并标记。原资料保持不变，PDF 的文字以 `[第 N 页]` 分隔。

- macOS：`helpers/doc-text` 使用 PDFKit 逐页提取；少于约 20 个字符的页面渲染后用 Vision 的 zh-Hans/en-US 识别。图片也用 Vision；docx 用系统 `textutil -convert txt -stdout`。不联网、不需要屏幕录制或辅助功能权限。
- Linux / 测试：`Extractor` 协议提供 `FakeExtractor`，按测试资料返回固定文字或失败；未配置假结果时明确显示提取不可用，不把二进制猜成文本。
- 数据目录 `knowledge_text/<sha256>.json` 缓存提取文字、截断标记和简短失败状态。相同内容不重复提取；失败也缓存，文件变化后重新提取。环境修复后可删除相应数据目录缓存再运行索引，不改原文件。
- `memory_index` 启动时和每 10 分钟扫描，把提取文字按原文件路径放进 FTS。App 上传成功先返回 pending，再安排本地提取。提取失败不影响其他文件。MCP memory_read 返回提取文字和 `extracted: true`，不返回原始二进制。

`knowledge_sources` 记录每个原资料的 sha256、status、error、organized_at、notes，以及长文分批进度。`pending` 是待整理；`organized` 是这份内容已整理；`unreadable` 是不支持、太大或提取失败；`skipped` 是主题无读者（包括知识库不支持的格式，error 为 null，不显示成无法读取）；`failed` 为可显示的失败状态预留，运行失败本版保持 pending、记录固定 error，供下次重试。主题设置为空读者时，即使之前整理过，也不再发送给模型。知识库散文件不进入整理任务。以 `.` 开头的文件和文件夹（Finder 的 `.DS_Store`、Obsidian 的 `.obsidian`）不算资料，也不算区域。

## 每晚整理

任务 ID `knowledge_organize`，默认 `0 3 * * *`（运行设置的时区）。`PATCH /api/v1/settings` 的 `knowledge_organize_cron` 可以改时间，`job_harness.knowledge_organize` 可以改优先适配器，默认 codex，失败沿现有 harness_order 回退。也可 `POST /api/v1/jobs/knowledge_organize/run`，返回 202 后查 runs。每个主题逐个运行，一次 Agent 会话最多带 8 份资料、60,000 个正文字符；还有待整理的就再开一次，每个主题每次任务最多 6 次，剩下的留到下一次任务。单个长文件分批处理，只有最后一段写成功才标 organized，避免只读开头却报完成。

无读者的主题、散文件、不支持或提取失败的资料不会进入整理提示词。提取本身在本机进行，不调用模型。提示词在 `prompts/knowledge_organize.md`，资料正文由后端放进提示词，要求按概念组织中文笔记、每个要点对应来源、不得编造，并把资料中的指令当作数据。整理 Grant 只允许读一个主题，所以即使模型尝试访问其他主题也会拒绝。

`knowledge_note_write(path,title,content,sources)` 只对整理临时身份开放，只写当前主题 `整理/**/*.md`；来源必须是本主题存在的原资料。后端组装 YAML frontmatter（title、sources、organized_by、organized_at）、正文、可点击的 `[[知识库/课程/资料.pdf]]` 来源列表，以及 `## 我的批注`。已有笔记中这个标题之后的内容（包括换行和空格）原样保留；模型 content 自带的该节会剥离。未在数据库 knowledge_notes 登记为后端生成的文件拒绝覆盖（即使用户文件碰巧有 organized_by 也一样），模型须换一个新路径。

成功写入后立即更新 FTS；只有本次成功 harness 实际写出的笔记可用于确认整理成功，并按真实来源保存资料与笔记的关系。运行失败的资料保持 pending，下次重试；sha256 不变且已完成的不再重复整理。运行中资料发生变化也不会误标完成。文件与 SQLite 不做跨介质事务，崩溃可能留下已写笔记而资料仍 pending，下次会读已有笔记合并。

## App 接口和验证范围

所有知识库 HTTP 路由都要求 app 权限，字段、状态码和原始字节上传协议见 [api.md](api.md)；精确 schema 见 [openapi.json](openapi.json)。App 用户可以浏览系统区和全部主题、切换读者、上传资料、搜索提取文字、看笔记和来源、用系统预览打开原资料。

离线测试覆盖区域、令牌、认证、SQL 过滤、写保护、提取缓存、整理成功/失败和各 App 接口。Swift 源码已提供，本轮没有编译、没有调用真实模型，也没有访问真实用户知识库；PDFKit/Vision/textutil 的真机效果仍需在沙盒外验证。

## 自动输入资料

[GoodNotes 与课堂照片](webdav.md) 为知识库增加两个可停用入口。GoodNotes 经 `/dav/goodnotes/` 落在数据目录，goodnotes 插件安全复制到主题 `GoodNotes/`，SQLite 记录归属和写出 SHA，只有未被用户改过的本插件文件可以更新；备份删除不删副本。课堂照片先按课表和拍摄时间筛选，再由独立 photo-export 导出原图、本机 OCR，符合文字门槛才进入 `课堂照片/<日期>/`；按完整 PhotoKit ID 去重，用户删除后不恢复。

两个插件都没有额外整理流程，调用现有知识库同步发现 SHA 变化，仍由夜间任务整理。新主题沿用现有默认读者逻辑，空读者不交给 Agent。大 PDF 使用新增的系统 JXA/PDFKit 逐页读取路径，需要 OCR 的页仍调用原有 doc-text 图片模式；不修改已装的 Swift 工具。状态通过 `GET /api/v1/knowledge/inputs` 给 App，详见 [api.md](api.md)。

私人财务主题必须保持 readers=[]；记账经 finance_* 专用工具访问，不给她勾主题读者。每日共享区不放财务明细；各来源覆盖与 ZIP、归档、月报边界见 [finance.md](finance.md)。
