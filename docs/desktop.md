# Aemeath Bot · Windows 桌面 App

Windows 桌面端位于 `desktop/`，使用 Compose Multiplatform Desktop / Kotlin JVM。直接将 `android/core` 引入为 `:core`，复用模型、HTTP 客户端、页面文字、Bot 分组、卡片解析、动画时序和模拟数据。没有修改安卓、Mac App、后端或移植任务说明文件。

当前交付是源码、测试和 MSI 构建工作流。开发这次移植的 Mac 没有 JDK，按要求没有安装或下载 JDK，**尚未实际编译、运行 JVM 测试、生成 MSI 或在 Windows 上截图验收**。下表的“已实现”指代码已接入，运行和视觉验收见文末清单。

## Mac 功能逐项对照

参照 `frontend/README.md` 的 Mac App 一节，以及 `AssistantUI/Mac/`、`Shared/`、`AppStore.swift`。

| Mac 功能 | 桌面实现 | 文件（位于 `desktop/src/main/kotlin/local/assistant/desktop/`） |
| --- | --- | --- |
| 左侧助理、角色、横线、知识库、消息来源；停用角色靠后 | 已实现；复用 `Bot.build`，头像、状态点、最近消息、未读和今日数量 | `ui/DesktopApp.kt`、`AppStore.kt` |
| 底部连接状态、设置；恢复上次页面和模型 | 已实现，本机偏好独立保存 | `DeviceSettings.kt`、`ui/DesktopApp.kt` |
| 角色从底色里探头，眨眼、注视、点击反馈、说话和任务状态 | 已实现；分层眼睛、眼部遮罩和原有 24 段动作，窗口隐藏时停止动画 | `ui/Mascot.kt`、`ui/MascotProps.kt`、`ui/MascotDrawing.kt` |
| 按宽度排多列、相近高度分组、短行铺满、依次浮入、悬停抬起 | 已实现；240 最小列宽、12 间距；单列小卡片成对 | `Presentation.kt`、`ui/CardGrid.kt`、`ui/Components.kt` |
| 卡片收起把手、虚线、卡片滚出后收起、聊天占满内容区 | 已实现，以稳定列表键保持当前聊天位置 | `ui/BotPage.kt` |
| 从旧到新的聊天，按天分组、更早消息、忙碌气泡、新消息动画 | 已实现；提炼结果、摘要、健康报告和对话复用 `BotTimeline` | `ui/BotPage.kt`、`AppStore.kt` |
| 今日待办：截止、来源悬停原文、同步状态、完成、编辑和删除 | 已实现；编辑通过 `TodoDraft` 只发送改变的字段；删除说明提醒事项也会删除 | `ui/Components.kt` |
| Agent 是否可用、最近提炼、立即运行、完整运行表 | 已实现；运行表包含时间、任务、Agent/尝试次数、耗时、结果和原因 | `ui/BotPage.kt`、`ui/SettingsPage.kt` |
| 六类服务端卡片、课表今天列、进度、折线/柱状/环形图 | 已实现；多系列颜色、目标虚线、超出部分、图例；照片卡片与 Mac 一样显示占位和说明 | `ui/Components.kt` |
| 助理插件卡片在前、今日待办和 Agent 各占一行 | 已实现 | `ui/BotPage.kt` |
| 知识库第一张卡片打开知识库，资料来源列表 | 已实现 | `ui/BotPage.kt` |
| 知识库搜索、主题、新主题、资料/笔记、谁能读、默认读者、Agent 名单、整理任务 | 已实现，所有操作交给 v1 HTTP API | `ui/KnowledgePage.kt` |
| 添加知识库资料、打开原件和来源笔记跳转 | 已实现；上传上限 50 MiB，原件选择保存位置后用系统关联程序打开 | `DesktopFiles.kt`、`ui/KnowledgePage.kt` |
| 健康的手动准备指数、体感和日期记录；简报不重复显示为文字卡片 | 已实现；复用 `ReadinessCheckin` | `ui/BotPage.kt` |
| 输入框、Return 发送、Shift+Return 换行、回复期间禁止再发 | 已实现，发送开始便锁定这个 Bot；失败恢复草稿 | `ui/BotPage.kt`、`AppStore.kt` |
| 自动、Claude Code、Codex、DeepSeek 的模型选择 | 已实现；从后端 Status 经 `ChatOption` 生成，保存本机选择；后端负责失败回退 | `ui/BotPage.kt` |
| 两级设置：连接、插件、消息来源、时间表、运行表、贴图预览 | 已实现；也保留当前 Mac 已有的“卡片风格”（素雅、点彩、柔光） | `ui/SettingsPage.kt` |
| 插件开关、仅预览、白名单/课程群、插件信息 | 已实现；另接入删除接口；不支持的平台由后端状态决定 | `ui/SettingsPage.kt`、`AppStore.kt` |
| cron 中文说明、置信度阈值、Agent 优先顺序 | 已实现；保持后续回退顺序，不覆盖其他后端设置 | `Presentation.kt`、`ui/SettingsPage.kt` |
| 菜单栏静止贴图及剩余数量、小窗动画、完成待办、提炼、打开主窗口、退出 | 已映射为 Windows 系统托盘和小窗；关闭主窗口保留后台刷新 | `DesktopTray.kt`、`Main.kt` |
| 空闲 30 秒、任务/回复中 2 秒刷新 | 已实现；立即操作唤醒轮询；插件卡片和健康报告错误隔离 | `AppStore.kt`、`Presentation.kt` |
| 全部页面的模拟数据和操作 | 复用核心内存 `MockBackend`；不访问后端，可改待办、聊天、开关插件、时间表和知识库 | `AppStore.kt`、`Resources.kt` |
| 浅/深色、衬线标题、正文、减弱动态效果 | 照 `Palette` 两套色值；Windows 主题和动画设置每 2 秒重读 | `ui/Theme.kt` |

## 编译和打包

构建机需要 JDK 17 和 **Gradle 8.13**。MSI 必须在 Windows x64 构建，另外需要 WiX Toolset **3.14.1**。最终安装包包含 Java 运行时，使用者不用安装 Java。开发调试可以在有 JDK 的 Linux / macOS 上运行。

固定版本：Kotlin JVM、Serialization、Compose 编译器插件均为 **2.2.20**；Compose Multiplatform 插件 **1.9.1**；Coroutines **1.10.2**；Serialization JSON **1.8.1**；OkHttp **4.12.0**；JNA Platform **5.17.0**。Compose 编译器插件与 Kotlin 使用相同版本，参见 [Compose 兼容说明](https://kotlinlang.org/docs/multiplatform/compose-compatibility-and-versioning.html)。

从仓库根目录执行：

```powershell
cd desktop
python scripts/check_sources.py
gradle --no-daemon --stacktrace test :core:test
gradle run --args="--mock"
# 以下命令在 Windows 执行
gradle --no-daemon --stacktrace packageMsi
```

`--mock` 将本机模拟数据开关设为开启；以后可在连接页关闭。首次启动默认也使用模拟数据。模拟数据改变只保留在进程内。

MSI 输出在 `desktop/build/compose/binaries/main/msi/`。版本从 **1.0.0** 开始，Windows 的 `upgradeUuid` 固定为 `8aef6340-3d05-45a7-94a7-caa29d50d9ba`。发新版时同步提高 `build.gradle.kts` 中 `version` 和 `packageVersion`，保留 UUID。打包属性和运行时说明见 [Compose 原生分发文档](https://kotlinlang.org/docs/multiplatform/compose-native-distribution.html)。

贴图通过 `processResources` 直接引用 `frontend/Design/stickers/`；没有在源码目录复制素材。`generateDesktopAssets` 用 Java ImageIO 从 `frontend/App/AppIcon.icon/Assets/aemeath.png` 生成包含 16–256 像素 PNG 条目的 ICO，以及贴图索引，都写进 `build/generated/desktop/`。不提交生成的 ICO、MSI、字体或 Gradle wrapper JAR。

标题英文优先 Georgia，再 Cambria；中文依次尝试 Noto Serif SC、Source Han Serif SC / Noto Serif CJK SC、宋体；正文使用 Segoe UI 和微软雅黑。字体只从本机选择，不下载、不打包。

## 安装与日常使用

在 GitHub Actions 的 **Windows Desktop** 工作流里下载 `Aemeath-Bot-Windows-MSI` artifact，解压后双击 MSI。安装范围是当前 Windows 用户，添加开始菜单和桌面入口。安装包暂未配置发布者签名。

运行后左边选 Bot，右边查看卡片和消息。`Ctrl+R` 刷新、`Ctrl+,` 打开设置、`Alt+左方向键` 返回；侧栏可用上下方向键切换。输入框按 Return 发送，Shift+Return 换行。

关闭主窗口后 App 继续留在托盘，点托盘图标打开今日小窗；右键也能打开主窗口或退出。今日剩余数量放在托盘图标的提示文字中。系统不能创建托盘图标时，关闭主窗口会退出，避免出现没有入口的后台进程。

更新前先从托盘退出旧版本，再运行新 MSI。卸载程序之后，本机连接偏好和加密令牌仍在 `%LOCALAPPDATA%/Aemeath Bot/`，可以手动删除这个目录。

## 连接后端

### 同一台 Windows 电脑

按 [Windows 原生后端说明](windows.md) 单独启动后端。在 App 的“设置 → 连接”里关闭“使用模拟数据”，后端地址填 `http://127.0.0.1:8765`，粘贴这台设备自己的 App Bearer Token，点击“保存并连接”。地址填服务根路径，不额外加 `/api/v1`；有反向代理基础路径时会保留。

App 不启动后端、Agent、读屏或同步工具，也不读取后端的 `.env` 或 `.state`。运行状态、白名单和同步结果都由用户填的后端返回。Windows 后端目前不能写 EventKit：待办操作可用，但提醒事项会显示同步失败或等待；要同步到 iPhone，需连接支持 Apple 同步的 Mac 后端。详见 [Windows 功能差异](windows.md#文档视频与功能差别) 和该文的 Apple 同步说明。

### 另一台 Mac / Windows（Tailscale）

两台设备登录同一个 tailnet。若后端已经配置 Tailscale Serve，填该设备的 `https://设备名.你的tailnet.ts.net`；也可填后端实际监听的 Tailscale 地址及端口。服务端配置按 [Windows 文档](windows.md#tailscale-与-app) 或 Mac 部署文档，不开放公网。HTTP App 不需要远程 MCP 的额外 Host 配置。

令牌在 Windows 用 JNA `Crypt32Util` 调用 DPAPI，加密范围是**当前用户**，未使用机器级标志。只有密文写入 `token.dpapi`，普通偏好文件不含令牌。留空保留旧令牌，也可明确选择“清除已保存的令牌”。换 Windows 用户、密文损坏或 DPAPI 不能解密时重新填写。

非 Windows 开发环境只在内存中保留令牌，新进程必须重新输入，不读取或创建磁盘令牌文件。普通连接偏好在 `~/.aemeath-desktop/`。App 不接统计、广告或崩溃上报；网络使用复用的 v1 客户端，不跟随重定向。聊天不写客户端日志或聊天缓存。用户选择上传的文件只发给当前后端；选择保存的知识库原件保留在用户指定位置。

## 和 Mac 版的差别

- 菜单栏改为系统托盘，数量在提示文字里；小窗位置按点击所在屏幕定位。窗口边框、菜单和表单控件由 Compose / Windows 绘制，使用 Windows 快捷键。
- Mac 的钥匙串改用当前 Windows 用户的 DPAPI；其他开发平台的令牌不落盘。
- New York / 宋体的排版映射为本机的 Georgia / Cambria 与宋体或思源宋体。没有携带 Apple 字体或 SF Symbols；小图标使用可用符号和矢量绘制，字形与原生控件细节会有差异。
- Quick Look 没有直接移植。知识库“打开原件”改为选择保存位置，再交给 Windows 的默认 PDF / 图片 / Word 程序；正文提取文字和来源跳转仍在 App 内。
- 运行记录和插件信息在窗口中逐页打开；没有复制 SwiftUI 的 sheet / popover 容器。卡片折叠沿用滚出视野和把手操作，没有额外复制 AppKit 滚动速度触发的甩动手势。
- 图表以 Compose Canvas 绘制，保留数据、系列、目标线、超出目标的颜色和说明；Swift Charts 的自动刻度、文字布局不是同一个渲染器。Markdown 复用 Kotlin 核心的标题/逐行解析，补上加粗、斜体、行内代码、代码块、列表和链接文字；复杂排版与 SwiftUI 的渲染仍可能不同。链接只在主动点击时交给系统浏览器打开。
- Apple 提醒事项、日历、HealthKit 和手表桥接不在 Windows 客户端本机实现。提醒事项/日历操作仍交后端；可查看健康卡片和简报、手动记录准备度。后端平台能力不会因为换客户端而改变。

照片卡片的占位图沿用当前 Mac 的行为；v1 卡片只给照片记录，没有新增图片下载接口。模拟原件沿用核心的示例 PDF，其他没有提供模拟二进制的原件仍可能提示找不到。

上述平台替代之外，**尚未完成真实 Windows 上与 Mac 的视觉对照和安装验收**，不能据此认为像素级复刻已经通过。

## 测试与 CI

新增 **54 个 JVM 单元测试声明**：

- `PresentationTest.kt`：列数边界、相近高度分组、短行、托盘数量与状态、刷新策略、设置行及时间表补丁。
- `InlineMarkdownTest.kt`：嵌套强调、代码字面量、转义、不完整标记、链接及空输入。
- `TokenStoreTest.kt`：非 Windows 只保存在本次运行内、不碰磁盘旧令牌；模拟 Windows 密文读写、清除、加密失败保留旧值、损坏密文和偏好隔离。使用注入的测试编码器，不冒充真实 DPAPI 互通测试。
- `AppStoreTest.kt`：模拟数据加载、待办操作、对话互斥、来源不可发送、插件、时间表、立即运行、知识库、错误不泄露、取消传播、真实轮询节奏、并发旧响应不覆盖新消息/风格，以及旧连接不能读取新令牌。

此外直接运行 `:core:test`，不复制或重写共享核心测试。当前源码里可数到 153 个核心 `@Test` 声明，最终执行数以 JUnit 报告为准。

本机已运行 `python3 desktop/scripts/check_sources.py`，并通过独立的 tree-sitter Kotlin 语法解析检查；这些只检查源码结构、素材、版本固定和语法，**没有进行 Kotlin 类型检查，也没有执行上述 JVM 测试**。

`.github/workflows/desktop.yml` 只在 `desktop/**`、`android/core/**` 或自身变化时触发，也可手动运行。JDK 和 Gradle 由 Actions 配置，不提交 wrapper JAR。

| CI runner | 命令 | 产物 |
| --- | --- | --- |
| `ubuntu-latest` | `python3 scripts/check_sources.py`；`gradle --no-daemon --stacktrace test :core:test` | 桌面及核心的测试报告 |
| `windows-latest`（测试任务成功后） | 安装/检查 WiX 3.14.1；`gradle --no-daemon --stacktrace packageMsi` | 含运行时的 MSI artifact |

所有命令工作目录均为 `desktop/`。Ubuntu 的 `test` 也会编译桌面 UI 源码；Windows 打包会编译对应系统的桌面依赖。

尚无实际编译错误报告。首次 CI 仍可能暴露的地方是 Compose 1.9.1 的 Desktop/Skia 方法签名、`SubcomposeLayout` 的测量约束、Gradle 原生分发任务的参数，以及 Windows runner 的 WiX 搜索路径。已对照固定版本发布说明和 Maven 源码核对关键 API，但它们不能替代真正的编译和启动。

## 还没在真 Windows 上验证的清单

- [ ] Ubuntu 的桌面与共享核心测试全部通过；Windows `packageMsi` 成功，artifact 内确实有 MSI。
- [ ] 无 Java 的 Windows 10 / 11 x64 双击安装、开始菜单/桌面启动、卸载和 1.0.0 → 后续版本覆盖升级。
- [ ] 当前用户 DPAPI 跨重启恢复令牌；换用户不能解开；普通偏好/日志和安装包中没有令牌。
- [ ] 浅色、深色、动态效果开关运行时切换，Georgia / Cambria / 宋体及思源宋体的回退。
- [ ] 在模拟模式逐页与 Mac 对照截图；至少检查 100%、150%、200% 缩放、窄窗口、最大化和多屏。
- [ ] 贴图分层眼睛、注视、点击、24 段动作、窗口隐藏后的动画开销，以及卡片悬停与高度分组。
- [ ] 托盘点击、完成待办、关闭留驻、退出和主窗口重新置前；托盘创建失败时可正常退出。
- [ ] 两秒/三十秒刷新、慢响应、断线重连、正在回复时不能重复发送，切换 Bot 后草稿和未读行为。
- [ ] 同机 Windows 后端与 Tailscale 远程 Mac 后端的鉴权、模型选择、待办来源及真实同步状态。
- [ ] 插件切换/删除、白名单/预览、时间表保存、健康手动记录、知识库权限/上传和原件下载打开。

本次改动留在 `local/desktop` 工作区，没有提交、推送，也没有启动或改动后端状态。
