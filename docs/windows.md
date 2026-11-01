# Windows 原生后端

在 Windows 10 / 11 上运行 Python 后端，iPhone、iPad、Mac App 仍通过 Tailscale 连接。无需 WSL；不修改 SwiftUI App。建议使用 Windows 10 22H2 或 Windows 11、64 位 Python 3.12。macOS 继续使用 [Mac 部署说明](mac-setup.md)。

## 安装和第一次启动

安装 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)、[Tailscale](https://tailscale.com/download/windows) 和 Node.js LTS（只有使用 npm 版 Agent 时需要）。在日常登录、运行 QQ 的同一个 Windows 用户下操作 PowerShell：

```powershell
winget install --id astral-sh.uv -e
# 安装后重新打开 PowerShell；切到仓库 backend 目录
uv sync --locked --python 3.12
Copy-Item .env.example .env
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

将生成的令牌填入 `.env` 的 `ASSISTANT_TOKEN`，不要发进聊天、截图或 Git。已有 `.env` 时保留它，不要再次复制示例。最小配置：

```dotenv
ASSISTANT_TOKEN=填入刚生成的至少32位令牌
ASSISTANT_HOST=127.0.0.1
ASSISTANT_PORT=8765
ASSISTANT_DATA_DIR=.state
# 可选：用正斜线表示 Windows 绝对路径，避免转义问题
# ASSISTANT_VAULT_DIR=C:/Users/你的用户名/Documents/AssistantVault
```

`.env`、SQLite、OAuth 文件、截图和 Obsidian 目录应放在本地 NTFS 磁盘，使用文件属性里的「安全」页确认只有本用户和系统可读。POSIX 的 `chmod 600` 在 Windows 上不能替代 NTFS ACL。资料接口拒绝目录联接（junction）、符号链接、设备名、备用数据流及带尾随空格/点的路径。

先在前台验证配置，再运行：

```powershell
uv run python -m scripts.windows_service --check
uv run python -X utf8 -m core
```

默认插件停用。Windows 即使配置了 `ASSISTANT_APPLE_MODE=fake`，正常服务也不会把它当作已同步：待办只留在 App 和待同步队列。离线演示 `uv run python -m scripts.demo` 才显式注入假同步器。

## Agent 和密钥

安装官方 Claude Code 或 Codex CLI，并在同一个 Windows 用户下登录。npm 方式可用：

```powershell
npm install -g @anthropic-ai/claude-code @openai/codex
claude auth login
codex login
```

后端查找 PATH，以及 `%USERPROFILE%/.local/bin`、`.cargo/bin`、`%APPDATA%/npm`、WinGet Links 等目录。对 npm 的 `claude.cmd` / `codex.cmd`，从相邻 `node_modules` 的包清单找到 JavaScript 入口，直接通过 Node 启动；JSON 配置、空参数和中文不经 cmd.exe 转义。找不到标准 npm 入口会报错，不执行未知批处理文件。自定义安装可把原生可执行文件或标准 npm 前缀加入 PATH。

DeepSeek 可在启动环境或 `.env` 配置 `ASSISTANT_DEEPSEEK_API_KEY`，也可存入 Windows「凭据管理器」的 Windows Vault：

```powershell
uv run python -m keyring set "AemeathBot DeepSeek API" $env:USERNAME
```

命令会交互式询问密钥，不把密钥作为命令行参数。读取用户名使用 `getpass.getuser()`；如果它与 `$env:USERNAME` 不同，使用前者的值。环境变量/`.env` 优先。Mac 继续读取原有钥匙串。Claude 用量读取优先使用凭据管理器的 `Claude Code-credentials`，否则读取本用户 `.claude/.credentials.json`；不修改登录文件。token_usage 的默认会话位置仍为 `~/.claude/projects`、`~/.codex/sessions`，Windows 的 `~` 指 `%USERPROFILE%`。

CLI、MCP 子进程和本地处理工具先以挂起状态创建，加入带 `KILL_ON_JOB_CLOSE` 的 Windows Job Object 后恢复。超时、取消或常驻入口被任务计划程序终止时，会清理子进程树。

## Tailscale 与 App

后端仅接受 loopback 或 Tailscale IP 作为监听地址，拒绝 `0.0.0.0`、`::`、普通局域网及公网地址。推荐保留 `127.0.0.1`，通过 Tailscale Serve 提供 HTTPS：

```powershell
& "$env:ProgramFiles/Tailscale/tailscale.exe" status
& "$env:ProgramFiles/Tailscale/tailscale.exe" serve --bg --https=443 http://127.0.0.1:8765
```

不启用 Funnel。所有设备加入同一个 tailnet；App 填 `https://你的电脑名.你的tailnet.ts.net`。为每台设备创建独立令牌，操作见 [远程连接与鉴权](remote-mcp.md)。远程 MCP 还需 `.env` 配置 `ASSISTANT_MCP_ALLOWED_HOSTS=你的电脑名.你的tailnet.ts.net`，HTTP App 无需该项。默认端口和 Mac 相同；若用自定义端口，同时修改 Serve 目标。

也可把 `ASSISTANT_HOST` 改成该 Windows 电脑自己的 Tailscale IP，直接连接该地址。程序只绑定配置的一个地址；要同时使用本机 HTTP 和远程 HTTPS，请用前述 Serve 方案。自检识别默认的 `C:/Program Files/Tailscale/tailscale.exe`，即使它不在 PATH。

## 登录常驻与日志

在 backend 目录执行；安装本身不立即启动服务，加 `-Start` 才立即运行：

```powershell
.\scripts\install_windows.ps1 install
.\scripts\install_windows.ps1 install -Start
.\scripts\install_windows.ps1 status
.\scripts\install_windows.ps1 uninstall
```

若 PowerShell 的脚本执行策略拦截文件，可在确认本地脚本内容后，使用进程级设置：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1 install -Start
```

任务名 `local.assistant.windows`，仅在当前用户登录的交互式桌面运行，使用普通权限。登录触发；崩溃后每分钟重试，最多 999 次；允许电池运行，无运行时长上限；同一任务不重叠，核心文件锁也阻止重复实例。未登录、注销或重启至登录界面时不能读屏。安装脚本不会保存 Windows 密码，不会启用自动登录，也不改变电源/锁屏设置。卸载保留数据、令牌和凭据。同名任务指向其他 checkout 时拒绝覆盖。

日志与 Mac 对应，位于数据目录的 `logs/service.log`、`logs/service-error.log`，默认即 `backend/.state/logs/`。只有配置检查通过后才能安装。不要同时保留一个前台实例和一个计划任务。更新后先停止计划任务，`uv sync --locked --python 3.12`，再通过任务计划程序启动；使用可选 ASR 时加 `--extra windows-asr`。

## 系统设置与自检

屏幕读取需要用户已登录、桌面未锁定、窗口未最小化。QQ、微信和后端使用相同的普通权限；不要单独以管理员身份运行客户端。

在「设置 → 系统 → 电源」让接电/电池时系统和显示器都不关闭。也可自行在终端执行：

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0
```

在登录选项里检查动态锁、恢复后要求登录和屏幕保护程序；组织策略可能覆盖个人设置。自动登录可自行使用 [Microsoft Sysinternals Autologon](https://learn.microsoft.com/en-us/sysinternals/downloads/autologon)，安装脚本只给建议。RDP 断开、休眠、锁屏后应先确认桌面仍可访问，不要假定会持续读屏。

在「时间和语言 → 语言和区域 → 中文(简体) → 语言选项」安装「光学字符识别」。必要时管理员终端可安装系统语言能力：

```powershell
Add-WindowsCapability -Online -Name Language.OCR~~~zh-CN~0.0.1.0
```

自检只读取状态，不申请权限、不截图、不点击，也不运行视频处理：

```powershell
uv run python -X utf8 scripts/doctor.py
uv run python -X utf8 scripts/doctor.py --json
```

它检查 Python、uv、SQLite FTS5、CLI 登录、Tailscale/Serve、计划任务、电源超时、锁屏配置、自动登录建议、QQ/微信进程、简体 OCR、桌面可访问性、后端响应及备份。不能自动确定的项目标为「需人工确认」，不会把未验证的登录或 NTFS 权限当作通过。

## QQ / 微信校准

实现使用 `PrintWindow(PW_RENDERFULLCONTENT)` 截取指定窗口，被其他窗口遮挡时也请求完整渲染，不截整个桌面作为兜底。Windows API 集中在 `plugins/screen/windows_native.py`；无 API 的区域检测按 Swift `WindowShot` 的规则寻找竖向分隔线、标题分隔线、输入框上边界和右侧成员栏。OCR 用本机 Windows.Media.Ocr 的 `zh-Hans`，输出左上角起算的比例坐标。

先启动 QQ，让主窗口显示会话列表。群名完整露出，窗口至少 500×400，保持普通缩放比例及足够宽的聊天区。先做无输入的 layout 校准：

```powershell
$shots = Join-Path $PWD '.state/screens/qq_screen'
$request = @{action='layout'; app='qq'; out_dir=$shots} | ConvertTo-Json -Compress
$request | uv run python -X utf8 -m plugins.screen.windows_helper
```

检查返回的 `found` 包含 `nav,list,header,input`；有右侧成员栏时还应包含 `panel`。在本机打开 `files.list/header/chat` 三张 PNG，确认标题完整、聊天裁剪不含输入框和成员列表。`preview` 只截会话列表；`recognize` 只接受同一输出目录下的 PNG。JSON 动作还包括 `permissions`、`capture_chat`、`capture_page`、`capture_announcement`，后者目前固定返回 `{"screenshot":null}`。完整协议见 [屏幕读取说明](screen-reading.md)。

Windows 的 QQ / 微信插件不必配置 `helper`，自动使用内置 Python helper。先在 App 中配置白名单并保持 `preview_only=true`。核对预览识别的群名、时间和消息方向，再配置 `open_on_demand` 或关闭预览模式。需要调整消息分类阈值时，仍使用原来的 `layout` 配置（它调整 OCR 后的消息解析，不替代窗口分隔线检测）。

只有 `capture_chat` 会输入：激活目标窗口，核对当前标题；必要时点击请求会话在列表中的唯一匹配行，重新核对完整标题，之后只在聊天区域滚轮翻页，截图前再核对标题。第一屏滚到底，后续每屏向上三格以保留重叠。找不到分隔线、标题不符、窗口移动、点击位置被别的窗口遮住、权限不足时停止。锁屏返回 `screen_locked`。从不打字、发送、点搜索框、添加好友或点击聊天内容。

鼠标滚轮每格滚动行数会影响拼接。先保持 Windows 默认值；若读屏出现 `screen_history_gap`，调整滚动设置/窗口大小后重新验证，不能把缺口当作完整历史。单聊/群聊、QQ/微信版本、深色主题、跨屏缩放都应逐项校准。真实聊天与截图不进入日志或测试夹具。

课表插件同样省略 `helper`，从当前标签标题包含 `page_title` 的 Chrome 窗口截 `capture_page`。不激活、不点击、不滚动浏览器；窗口可被遮挡，但不可最小化。Windows 暂不执行 Mac 的 AppleScript 自动刷新；学校会话过期时手动刷新/登录并回到周历。

## 文档、视频与功能差别

| 功能 | Windows 行为 |
| --- | --- |
| HTTP / MCP、记忆、调度、飞书、学校/健康/财务等 `any` 插件 | 原生运行；飞书继续使用官方 SDK 和白名单 |
| QQ / 微信读屏 | Python helper，同一 JSON 协议；需真机校准 |
| PDF / 图片 OCR | Windows OCR；PDFium 先读文字层，没有文字才渲染并识别；大图按 OCR 尺寸限制缩放 |
| Word 文档 | 本地读取 docx 正文，不执行宏、不启动 Office |
| HEIC | 依赖 Windows 可用的 HEIF 图像解码器；解码失败会报告提取失败 |
| Apple 提醒事项 / 日历 / 课表日历 | 需要 Mac；不接微软 To Do、不伪造同步成功 |
| class_photos 苹果照片库 | `unsupported`，自动跳过，不影响其他插件 |
| Chrome 课表自动刷新 | Windows 暂无该项，保留截图读取 |

`/api/v1/status.apple_sync_error` 在 Windows 是 `macos_required`；同步任务在 runs 中明确失败为此代码，待办的 `sync_error` 相同，保留未同步状态和原有 ID。App 的列表、编辑、完成和删除照常写本地数据。接口仍是 `AppleBridge.call`，以后可接入 Mac 写入端；当前没有实现跨机器的 Apple 同步桥。

video_notes 使用 PATH 或插件配置里的绝对路径寻找 `ffmpeg.exe`、`yt-dlp.exe`。本地幻灯片 OCR 默认用 Windows OCR，不需要编译 Swift doc-text。无字幕视频在 Windows 可使用 CPU 版 faster-whisper：

```powershell
uv sync --locked --python 3.12 --extra windows-asr
```

预先准备完整的本地 CTranslate2 Whisper 模型目录（至少包含 `config.json`、`model.bin`，实际模型所需 tokenizer 文件也要齐全），将其绝对路径填入插件 `asr_model`；不配置 `asr_command` 时使用内置 `scripts/windows_transcribe.py`。运行时强制离线、本地目录、CPU/int8，不从 Hub 自动下载模型。自定义识别命令仍可配置，但需兼容现有 JSON 输出和参数。Mac 默认继续用 MLX Whisper。视频功能详见 [视频笔记](video-notes.md)。

## 验证方式

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

CI 保留 Ubuntu / Python 3.12、3.13，并增加 `windows-latest` / Python 3.12，执行锁定安装、PowerShell 语法解析、全量 pytest 和 Ruff。进程树测试用真实 Python 子进程，屏幕输入/识别测试用合成图和假桌面，不访问真实聊天。符号链接用例在 Windows 缺少相应权限时才跳过；可启用开发者模式后重跑。Mac 专属 shell 安装器测试在 Windows 跳过，其他平台继续验证。

## 待在 Windows 上实测

当前开发机是 Mac；以下项目未用真 Windows 验收。Windows CI 配置已加入，尚未推送触发运行。

- Windows 10 / 11 的 `uv sync --locked --python 3.12` 和完整 CI；WinRT 投影、pywin32、PDFium 二进制导入；可选 faster-whisper 和本地模型。
- 新建计划任务的权限、登录后自动启动、崩溃自动重启、任务强制停止后的进程树清理、注销/重启、中文和空格路径、休眠/电池设置；日志实际位置。
- npm 安装及官方原生版 Claude/Codex 的路径、登录、stdio MCP、中文/带引号配置、超时与取消后不残留 Node/MCP 子进程；Agent 失败回退。
- 凭据管理器和 `.env` 的读取优先级、用户名称、NTFS ACL；Claude 真实登录文件/配额接口和 Codex 的限额读取。
- Tailscale 默认/自定义安装目录、Serve HTTPS、设备令牌、远程 App/MCP、IPv6、监听地址限制及确认没有 Funnel。
- Windows OCR 简体中文包的安装和报错；中文/英文混排、EXIF 旋转、大图、多页/扫描/加密 PDF、docx 表格、HEIC 解码器。
- QQ、微信不同版本和主题的分隔线、群/单聊标题、同名前缀/长群名、成员栏、头像、发送方向、群公告为 null；默认预览不标已读，授权点开才标已读。
- 100% / 125% / 150% / 200% DPI、跨屏和负坐标、窗口移动、被遮挡时的 PrintWindow 图像、GPU 渲染黑图检测、最小化、锁屏/UAC/RDP 断开；不支持时必须报错并保留原始消息状态。
- 滚到底、每屏三格的重叠、`at_top` 指纹、历史拼接及 OCR 高度变化；缺口不得静默丢消息。
- Chrome 课表标题匹配、被遮挡截图、登录失效和手动刷新；Windows 不尝试 AppleScript。
- 飞书增量和 token 刷新、跨 QQ/飞书去重、`macos_required` 在状态/运行记录/待办可见、待同步队列不被清空、停用/删除单插件不影响核心。
- `any` 插件的真实文件路径、GoodNotes WebDAV 传输、token_usage 会话导入、ffmpeg/yt-dlp/CPU ASR、每日数据库和 Markdown 备份恢复。

API 依据：[PrintWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow)、[Windows OCR](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine)、[PyWinRT](https://pywinrt.readthedocs.io/en/latest/)、[Task Scheduler 设置](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset)。
