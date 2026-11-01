# Mac mini 到货当天：从零开始的部署清单

Windows 原生后端的安装、差别和校准见 [Windows 说明](windows.md)。

按顺序做完下面每一节，最后用 `scripts/doctor.py` 自检。能自动化的部分都在 `backend/scripts/install_mac.sh` 里，脚本可以重复运行：已装的跳过，已有的配置不覆盖。

系统设置里的菜单名称按 macOS 15 及以后的版本写，新版本如果改了名字，以实际界面为准。所有终端命令都用**将来运行后端的那个用户**执行，不要用 `sudo` 运行安装脚本。

## 0. 准备

- [ ] 网线（比 Wi‑Fi 稳）、HDMI 欺骗器（不接显示器时用，4K/60Hz 规格即可）
- [ ] Apple 账户：提醒事项和日历靠 iCloud 同步到 iPhone
- [ ] Tailscale 账号，iPhone 上装好 Tailscale 并登录同一个 tailnet
- [ ] Claude 订阅账号、ChatGPT 账号（分别给 Claude Code 和 Codex 用）
- [ ] 能访问这个私有仓库的 GitHub 账号

## 1. 首次开机

- [ ] 在设置助理里创建一个管理员账户，以后后端、QQ、Claude Code、Codex 都在这个账户下运行。
- [ ] 登录 Apple 账户，打开 iCloud 的「提醒事项」和「日历」。
- [ ] 设置助理问是否打开 FileVault 时，建议选**不打开**；开和关的利弊见下一节的「FileVault：开还是关」，以后也可以在系统设置里改。
- [ ] 系统设置 → 通用 → 共享 → 打开「屏幕共享」（可选）。之后可以从别的电脑经 Tailscale 远程看桌面、点授权弹窗。

## 2. 系统设置（24 小时运行）

| 检查项 | 怎么设置 | 命令行等价 | doctor 条目 |
| --- | --- | --- | --- |
| 断电恢复后自动开机 | 系统设置 → 能源 → 打开「断电后自动启动」 | `sudo pmset -a autorestart 1` | power_restore |
| 系统不睡眠 | 系统设置 → 能源 → 打开「显示器关闭时防止自动进入睡眠」 | `sudo pmset -a sleep 0 disksleep 0` | system_sleep |
| 显示器不睡眠（截图需要） | 系统设置 → 锁定屏幕 → 「不活跃时关闭显示器」：永不 | `sudo pmset -a displaysleep 0` | display_awake |
| 屏幕保护不启动 | 系统设置 → 锁定屏幕 → 「不活跃时启动屏幕保护程序」：永不 | — | screensaver |
| 不锁屏 | 系统设置 → 锁定屏幕 → 「屏幕保护程序启动或显示器关闭后需要密码」：永不 | — | lock_screen |
| FileVault（建议关闭） | 系统设置 → 隐私与安全性 → FileVault | `fdesetup status` 查看 | filevault |
| 自动登录（FileVault 关闭时） | 系统设置 → 用户与群组 → 「自动以此身份登录」选当前用户 | — | auto_login |
| 有显示器 | 接显示器；没有显示器就插 HDMI 欺骗器 | — | display_attached |

说明：

- **FileVault**：建议关闭，但这是加密和无人值守之间的取舍，两种都能用，见下面的「FileVault：开还是关」。
- **自动登录 + 不锁屏**：截图和辅助功能只能在已登录、未锁定的桌面会话里工作，所以 LaunchAgent 也只在这个会话里运行。锁屏期间每次截图都会失败，后端把这种失败记成 `screen_locked`（截图失败时查一下 `ioreg` 里的锁屏状态），看门狗和课表卡片会直接说「Mac mini 锁屏了，解锁后会自己恢复」。只是显示器睡眠、没有锁屏时，截图照常能用。
- **macOS 自动更新**：建议在 系统设置 → 通用 → 软件更新 → 自动更新 里关闭「安装 macOS 更新」，避免半夜自动重启后某些权限需要重新确认；安全响应可以保留。更新之后跑一次 doctor。

### FileVault：开还是关

这台 Mac 要 24 小时无人值守运行，FileVault 决定了**断电后能不能自己恢复**和**机器丢了数据安不安全**，两者不能兼得。文档建议关闭，是因为它放在家里、不带出门，断电自己恢复更重要；你也可以选择开启。

| | 关闭 FileVault（建议） | 开启 FileVault |
| --- | --- | --- |
| 断电后来电 | 自动开机、自动登录，后端、QQ、微信自己恢复 | 停在磁盘解锁界面，**必须有人在 Mac 前输入密码**；在此之前后端、截图、提醒事项同步都不工作，也没法远程处理（Tailscale 和屏幕共享在解锁前都还没启动） |
| 自动登录 | 可以开 | 系统不允许开；在解锁界面输入密码后会直接登录这个用户，之后后端照常由 launchd 启动 |
| 机器被偷、被拿走 | 不需要密码就能读到数据：聊天截图、数据库、`.env` 里的 token、Obsidian 笔记、照片。Apple 芯片的硬盘本身是加密的，但不开 FileVault 时这层加密不需要密码 | 没有密码读不到数据 |
| 系统更新等计划重启 | 无影响 | 用 `sudo fdesetup authrestart` 重启可以跳过这一次解锁 |

缓解办法：

- **开着 FileVault 时，配一个不间断电源（UPS）**。几分钟到几十分钟的停电，Mac mini 靠 UPS 继续运行，根本不会重启，也就不用解锁。用 USB 连接的 UPS，macOS 能识别，在 系统设置 → 能源 里可以设置电量低时自动关机；停电时间超过 UPS 能撑的时间，Mac 会正常关机，来电后仍然需要有人解锁。
- **关着 FileVault 时**：Mac 放在家里固定的位置。某台设备的令牌泄露，只作废那一个（`uv run python -m scripts.tokens revoke <名称>`）；Mac 本身丢失时，在新机器上换一个新的 `ASSISTANT_TOKEN`，作废全部设备令牌再给各设备重新创建（数据库里只有令牌的哈希，拿到备份也不能直接用来调用后端）；必要时撤销飞书授权、退出 QQ 和微信登录。
- 两种情况都建议开着 系统设置 → 能源 → 「断电后自动启动」。

doctor 的 `filevault` 条目：关闭时显示通过；开启时显示「需人工确认」，提醒你确认已经接受断电后要手动解锁（或者已经配了 UPS），不会算作未通过。开启 FileVault 时，`auto_login` 条目显示「不适用」。

## 3. 一条命令安装后端

仓库不要放在「桌面」「文稿」「下载」里。这几个目录受系统隐私保护，后端进程读它们需要额外授权。建议放在主目录下：

```bash
cd ~
git clone https://github.com/feixing-Aemeath/aemeath-bot.git
cd aemeath-bot
bash backend/scripts/install_mac.sh
```

脚本按顺序做这些事，每一步都会先检查，已经完成的跳过：

1. Xcode 命令行工具：没有就弹出安装窗口，装完后重新运行脚本。
2. Homebrew：没有就用官方脚本安装（要输入一次登录密码），并把 `brew shellenv` 加到 `~/.zprofile`，已经有了就不加。
3. uv：`brew install uv`。
4. Claude Code、Codex、Tailscale：`brew install --cask claude-code codex tailscale-app`，已安装的跳过（包括用官方脚本或 App Store 装的）。不想让脚本装这些，加 `--no-apps`。
5. Python 依赖：`uv sync --locked`。第一次用 Python 3.12 建 `.venv`；`.venv` 已存在时保留原来的解释器，因为换解释器会让已给的系统权限失效。
6. `backend/.env`：不存在时从 `.env.example` 生成，自动填一个随机 token，权限 600；已存在就完全不动。
7. 编译 Swift 助手 `apple-sync`、`screen-helper`、`doc-text` 和新的 `photo-export`（`--no-helper` 全部跳过）。编译完仍然是假提醒事项模式，按第 6 节验证后再切换。
8. 生成并注册 LaunchAgent `~/Library/LaunchAgents/local.assistant.plist`（开机登录后自动启动，崩溃后 30 秒重启，日志在 `backend/.state/logs/`）：
   - 文件不存在：生成并 `launchctl bootstrap`；
   - 已存在且内容相同：不动，服务没注册就注册；
   - 已存在但内容不同：**不覆盖**，新版本写到 `backend/.state/launchd/local.assistant.plist.candidate`，确认后加 `--replace-plist` 替换（会先备份旧文件）。
9. 等后端启动，最后运行 `scripts/doctor.py` 输出检查清单。脚本的退出码就是 doctor 的退出码。

其他选项：`--restart`（更新代码后重启服务）、`--serve`（见第 5 节）、`--help`。

## 4. 登录 Claude Code 和 Codex

必须用运行后端的同一个用户登录。后端在后台调用它们时用的是这个用户的登录状态。

```bash
claude auth login      # 默认用 Claude 订阅账号；按提示在浏览器里完成登录
claude auth status     # 应该看到 "loggedIn": true

codex login            # 用 ChatGPT 账号登录
codex login status     # 应该看到 Logged in
```

- Claude Code 的登录信息存在钥匙串里。自动登录会解锁登录钥匙串，所以 LaunchAgent 里也能用。
- 只装了一个也能跑，但额度用完时就没法自动换另一个，所以两个都要登录。
- doctor 的 `claude`、`codex` 两项就是调用上面两条 status 命令。

## 5. Tailscale 和 tailscale serve（HTTPS）

后端始终只监听 `127.0.0.1:8765`。iPhone 和手表通过 `tailscale serve` 访问，它在 tailnet 里提供带正式证书的 HTTPS，再转发到本机端口。这样 App 不需要关闭 ATS，也不需要把后端绑定到 Tailscale IP。

1. [ ] 打开「Tailscale」App，登录你的 tailnet；在它的设置里打开「登录时启动」。
2. [ ] 在 Tailscale 管理后台（login.tailscale.com → DNS）确认 **MagicDNS** 和 **HTTPS Certificates** 都已开启。
3. [ ] 运行：

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   tailscale serve status
   ```

   也可以用 `bash backend/scripts/install_mac.sh --serve`，它只在当前没有任何 serve 配置时执行这条命令。第一次执行时，如果后台还没开启 HTTPS，命令会给出一个链接并等你在网页上确认。
4. [ ] 记下地址 `https://<机器名>.<tailnet 名>.ts.net`，App 的服务器地址填这个。
5. [ ] 给每台要连接的设备创建自己的令牌（在 Mac 的 `backend` 目录执行；令牌只显示一次，直接粘贴到那台设备上，不要发到聊天里）：

   ```bash
   uv run python -m scripts.tokens create iPhone --scope app          # iPhone App 的设置里填这个
   uv run python -m scripts.tokens create 快捷指令 --scope health_upload  # 只上传健康数据
   uv run python -m scripts.tokens list
   ```

   某台设备丢了或不用了，只作废它的令牌：`uv run python -m scripts.tokens revoke iPhone`，其他设备不受影响。远程 Agent 的令牌见 [remote-mcp.md](remote-mcp.md)。
6. [ ] 从另一台在 tailnet 里的设备验证：

   ```bash
   curl -H "Authorization: Bearer <这台设备的令牌>" https://<机器名>.<tailnet 名>.ts.net/api/v1/status
   ```

注意：

- **不要用 `tailscale funnel`**，它会把后端暴露到公网。doctor 发现 Funnel 开着会判为未通过。
- `.env` 里的 `ASSISTANT_HOST` 保持 `127.0.0.1`，令牌仍然必需；`/dav/` 也接受 Basic，密码填独立 dav 设备令牌。GoodNotes 用当前 HTTPS 基址加 `/dav/goodnotes/`，不需要单独转发路径，完整步骤见 [webdav.md](webdav.md)。
- 经 serve 访问 `/mcp` 时，请求的 Host 是 `*.ts.net`，默认会被 MCP 的 DNS 重绑定保护拦下（返回 421）。要让别的设备上的 Agent 接入记忆中心，把这个主机名写进 `.env` 的 `ASSISTANT_MCP_ALLOWED_HOSTS`，见 [remote-mcp.md](remote-mcp.md)。本机的 Claude Code 和 Codex 走 stdio，不受影响。

## 6. 系统权限

都在 系统设置 → 隐私与安全性 里授权。后端由 launchd 直接启动 `backend/.venv/bin/python`，系统按这个文件的**真实路径**记录授权，可以这样查看（安装脚本也会打印）：

```bash
backend/.venv/bin/python -c 'import os,sys; print(os.path.realpath(sys.executable))'
# 例如 /Users/<你>/.local/share/uv/python/cpython-3.12.x-macos-aarch64-none/bin/python3.12
```

| 权限 | 用途 | 阶段 | 授权给谁 | 怎么授权 |
| --- | --- | --- | --- | --- |
| 屏幕录制 | QQ、微信截图 | 二 | screen-helper：`~/Library/Application Support/AemeathBot/bin/screen-helper` | 点「+」，在文件选择框里按 ⌘⇧G 粘贴路径 |
| 辅助功能 | 关闭预览模式后，点开会话、滚动 | 二 | screen-helper（预览模式不需要） | 同上 |
| 完全磁盘访问 | 读短信 `chat.db`、照片库数据库 | 三 | 同上 | 同上 |
| 提醒事项 | 写「AI 待办」列表 | 一 | apple-sync（弹窗里叫「Assistant Apple Sync」） | 只能在程序请求时弹窗允许，不能手动添加 |
| 日历 | 有具体时间的待办写进日历 | 一 | 同上 | 同上 |
| 照片 | 课堂照片 PhotoKit 只读导出 | 知识库入口 | photo-export（Assistant Photo Export） | 桌面终端运行 photo-export authorize，弹窗允许；先在「照片」打开 iCloud 照片 |

- 每次改完权限，重启服务：`launchctl kickstart -k gui/$(id -u)/local.assistant`。屏幕录制权限必须重启进程才生效。
- **提醒事项和日历**：两个助手（apple-sync、screen-helper）启动后都会放弃父进程的责任，以自己的名义申请权限，所以授权记在助手本身，不是 Python，也不管是终端还是 launchd 启动的。先在 Mac 桌面的终端里运行一次 apple-sync，弹窗「Assistant Apple Sync」时点「允许」，按 [deployment.md](deployment.md) 的「EventKit helper」一节，用测试内容 upsert 再 delete。然后在 `.env` 设 `ASSISTANT_APPLE_MODE=real`、`ASSISTANT_APPLE_BINARY=~/Library/Application Support/AemeathBot/bin/apple-sync 的绝对路径`，重启服务，再手动运行一次 `POST /api/v1/jobs/apple_sync/run`。
- **屏幕录制**：`echo '{"action":"authorize"}' | ~/Library/Application\ Support/AemeathBot/bin/screen-helper` 会让系统弹出申请；也可以直接在系统设置里点「+」添加。`{"action":"permissions"}` 只查不申请。
- 两个助手换了新版本（安装脚本提示「换了新版本」）时，系统会把它们当成新程序，权限要重新给一次：在列表里选中旧的点「−」删掉，再点「+」加回来。
- 升级 Python（换了 `.venv` 的解释器）后，真实路径会变，上面的授权都要重新给。
- **doctor 怎么核对权限**：它读取系统的权限数据库，确认上面的 python（以及 apple-sync、截图助手）有没有授权。读这个数据库本身需要「完全磁盘访问」，所以如果运行 doctor 的终端没有这个权限，这几项会显示「需人工确认」，这时去系统设置里看一眼即可。是否给终端完全磁盘访问由你决定，不是必需的。

## 7. QQ 和微信

- [ ] 从官网安装 QQ，登录后保持主窗口打开。别的电脑上不要再登录 QQ 桌面版，否则这台会被挤下线（手机 QQ 不影响）。
- [ ] 微信等 QQ 稳定之后再接，同样保持登录。
- [ ] 截图插件默认「仅预览模式」，只截会话列表、不点开聊天，也不碰 QQ（窗口被挡住也能截，但不要最小化）。QQ 主窗口保持打开；白名单里的群最好置顶，关闭预览模式后才能在列表里找到它们。界面定位和校准见 [screen-reading.md](screen-reading.md)。
- doctor 只能看到 QQ 有没有在运行，看不到是否已登录，所以这一项总是「需人工确认」。

## 8. 本地模型（阶段三，现在可以跳过）

照片分类和截图识别兜底要用本地的 Qwen3.8-27B（MLX，4 位量化）。安装 LM Studio（OpenAI 兼容接口，端口 1234）或 Ollama（端口 11434），设成登录时启动并常驻。doctor 的 `local_model` 是可选项，没装不影响结论。

## 9. 自检

```bash
cd ~/aemeath-bot/backend
.venv/bin/python scripts/doctor.py           # 中文清单：通过 / 未通过 / 需人工确认
.venv/bin/python scripts/doctor.py --json    # 机器可读
.venv/bin/python scripts/doctor.py --strict  # 需人工确认的也算失败
```

- 每项后面的「→」是修复方法。
- 有必需项未通过时退出码为 1；标「可选」的不影响结论。
- doctor 新增 `photo_export` 项，用工具的 status 显示照片权限（authorized / limited / denied 等），不申请权限。课堂照片和 GoodNotes 的五步设置见 [webdav.md](webdav.md)。

doctor 只读：不改设置、不请求权限、不点任何 App、不读聊天内容。

doctor 检查的项目：Python、SQLite FTS5、依赖包、Homebrew、uv、Xcode 命令行工具、`.env`（存在、权限 600、被 Git 忽略）、监听地址、提醒事项同步模式；第 2 节的全部系统设置；第 6 节的六项权限；Claude Code 和 Codex 登录、Tailscale 连接、serve 转发（以及没有开 Funnel）、launchd 服务在运行、后端 HTTP 接口能用 token 访问、QQ / 微信、本地模型、iCloud 账户；最近 48 小时内有完整备份。

## 10. 日常维护

- 更新代码：`git pull && bash backend/scripts/install_mac.sh --restart`
- 日志：`backend/.state/logs/service.log`、`service-error.log`（不含 token 和聊天全文）
- 停止服务：`launchctl bootout gui/$(id -u)/local.assistant`；重新启动：再运行一次安装脚本
- 备份：后端每天 03:00 备份数据库和记忆目录到 `backend/.state/backups/`，同一块盘上的备份防不了硬盘损坏，定期复制到别的磁盘
