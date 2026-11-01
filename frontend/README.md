# 24 小时助手 · SwiftUI 前端

App（iPhone、iPad、Apple Watch、Mac）只负责显示，所有数据都来自后端的 HTTP 接口（[docs/api.md](../docs/api.md)）。**Mac App、iPhone App 和 iPad App 已经有正式页面**，手表通过 iPhone 同步今日待办（见下文）。Mac mini 到货前默认用模拟数据，在 Mac App 的「设置」里可以切换成真实后端。

```text
frontend/
├── project.yml                     # XcodeGen 配置；Assistant.xcodeproj 由它生成，不提交
├── App/{iOS,macOS,watchOS}/        # 各平台的 App 入口
├── App/watchWidget/                # 手表表盘小组件
├── App/AppIcon.icon/               # 三端的 App 图标，Icon Composer 格式（scripts/make-app-icon.swift 生成）
├── scripts/check-watch-embed.sh    # 检查编译出的 iPhone App 里带着手表 App（CI 用）
├── scripts/make-app-icon.swift     # 用助理的贴图画 App 图标
└── Packages/AssistantCore/         # 共享 Swift Package
    ├── Sources/AssistantCore/      # 数据模型、API 客户端、钥匙串、卡片解析（不依赖 UI）
    ├── Sources/AssistantUI/        # 三端共用的 SwiftUI 视图
    └── Tests/AssistantCoreTests/   # 单元测试（用后端真实返回的 JSON）
```

## 本地运行（Mac）

```bash
brew install xcodegen
cd frontend
xcodegen generate          # 生成 Assistant.xcodeproj
open Assistant.xcodeproj
swift test --package-path Packages/AssistantCore
```

用免费的个人开发者账号装到真机：在 Xcode 每个 target 的 Signing & Capabilities 里选你的 Personal Team，并把 `project.yml` 里的 `BUNDLE_ID_PREFIX` 改成你自己的、唯一的前缀，然后重新运行 `xcodegen generate`。没有启用推送通知、iCloud 这类需要付费会员的能力；只有手表 App 和它的小组件用了 App Group（见「Apple Watch」一节）。免费账号签的 App 7 天后需要重新安装。

**iPhone 和手表一起装**：手表 App 嵌在 iPhone App 里（`Assistant-iOS.app/Watch/`），在 Xcode 里选 `Assistant-iOS` scheme 装到 iPhone，手表 App 和表盘小组件会跟着装到配对的手表上，不用再单独装。如果手表上没出现，打开 iPhone 上的「Watch」App：「通用」里打开「自动安装 App」，或者在「我的手表」页面底部的「可安装的 App」里点「安装」。`Assistant-watchOS` scheme 仍然保留，开发时可以直接装到手表上调试。三个 target（iPhone、手表、小组件）都要选同一个 Personal Team；免费账号每 7 天最多注册 10 个 App ID，这个项目一共用 4 个（iPhone、手表、小组件、Mac）。

## AssistantCore

- **模型**：`docs/api.md` v1 的全部数据结构（Todo、Today、Status、Digest、Run、PluginView、RuntimeSettings、HealthBatch…），字段名通过 CodingKeys 对应接口的 snake_case；时间支持后端的微秒精度。
- **APIClient**：每个 HTTP 接口一个 async 方法；Bearer Token 由 `TokenProvider` 提供，App 里用 `KeychainTokenStore`（本机钥匙串的通用密码项，`AfterFirstUnlockThisDeviceOnly`，不同步到 iCloud）；错误映射为 `APIError`（401/403/404/409/422/5xx、缺 token、解码失败）。每台设备用自己的设备令牌（iPhone、Mac App 各一个，`app` 权限），在 Mac 上用 `uv run python -m scripts.tokens create <名称> --scope app` 创建，见 [api.md](../docs/api.md)「连接与鉴权」；令牌没有 `app` 权限时 App 显示「这个令牌没有使用 App 的权限」。
- **卡片**：`Card.content` 把 `{type, title, data}` 解析成 `list / stat / chart / photo_grid / text / todo_list`；不认识的类型或字段不全时返回 `.unsupported`，不会让整个今日页解析失败。字段约定见 api.md 的「卡片 data 约定」。
- **DueText**：截止时间的显示（「今天 17:00」「明天」「已逾期 · 10月14日」）。
- **插件贴图**：每个插件一张贴图，放在 `Design/stickers/`，整个文件夹打包进三端 App；插件在 manifest 的 `mascot.sticker` 里选用哪张，没有贴图时显示占位图，App 自己用哪张写在 `Design/stickers/app.json`。`Mascot.swift` 是纯逻辑（贴图选择、API 状态到五种状态的映射、状态机、每一帧的位移/缩放/歪头），有单元测试；`AssistantUI/MascotView.swift` 负责绘制和动画，开启「减弱动态效果」时只显示静止姿势。流程见 [docs/mascot.md](../docs/mascot.md)。Mac App 和 iPhone 的设置里的「贴图预览」能看到所有贴图在各状态下的样子。

## Mac App

左边是 Bot 列表，右边是选中的 Bot，像聊天软件；菜单栏另有一个小图标。

- **Bot 列表**：上面是 Bots：第一个是 App 自己的助手助理Bot（个人助理：待办、每日总结、天气和今日提醒，贴图见 `Design/stickers/app.json`），后面每个角色插件一个 Bot（成绩Bot 学分成绩、校园Bot 请假和学校活动、作业Bot 作业和课表、用量Bot Token 余额和消耗、健康Bot 健康，见 [mascot.md](../docs/mascot.md)），停用的排在后面；一条横线下面是 App 自己的知识库Bot（知识库、GoodNotes、课堂照片和视频，页面上的第一张卡片打开知识库）；再下面是「消息来源」（QQ、飞书、微信这些只读消息的插件，用插件图标）。头像是贴图里角色的脸，右下角的小圆点是状态；名字是贴图对应的角色名（`Design/stickers/characters.json`），没写就用插件名；名字下面是它最近说的一句话，有没看过的新消息时名字加粗、右边一个小圆点。最下面是连接状态和「设置」。
- **Bot 页**：最上面是这个 Bot 的头部：角色从一条它颜色的底色里探出头，旁边是名字、管什么和一句话状态（如「上次运行在 10 分钟前，提取了 3 条待办」）。下面是卡片（打开页面时一张张浮上来，鼠标移上去会轻轻抬起），隔一条虚线，再下面是这个 Bot 说过的话，像聊天一样从旧到新、按天分开，新消息从下面滑进来，Bot 的任务正在运行时最后有一个「正在忙」的气泡；往上滚动卡片就移出去，聊天占满窗口，虚线上方的把手可以收起卡片。最下面是输入框（消息来源没有）。开启「减弱动态效果」时这些动画都不播放。
  - **Aemeath Bot**：卡片是今日待办和 Agent。待办每条显示截止时间、来源（哪个群、谁发的，鼠标停留显示原文）、是否已同步到提醒事项，可以勾选完成、编辑（标题、备注、截止时间）、删除（会同时删除提醒事项）；Agent 是各 Agent 是否可用、最近一次提炼待办和「立即运行一次」（头部的贴图表情和动作跟着 Agent 状态变）。聊天里是今天每次提炼待办的结果（用的哪个 Agent、第几次尝试、用时或失败原因）和今天的总结。工具栏按钮打开完整的运行记录表。
  - **角色 Bot 和消息来源**：卡片是后端返回的插件卡片（动态显示），停用时换成启用开关；聊天里每天一条：Agent 写的信息摘要和这天提炼出的待办。健康插件（健康Bot）的晨间简报和周报（失败的写明原因）也作为消息发出来，所以它的文字卡片不再重复显示，其余是指标卡片（见 [api.md](../docs/api.md) 的「health 插件的卡片」）。健康Bot 的卡片里还有「今天的记录」：手动记健身 App 的准备指数和自己的体感，用来校准准备度（见 [readiness-calibration.md](../docs/readiness-calibration.md)）。工具栏的 ⓘ 是启用开关和插件信息。
  - **输入框**：发给这个 Bot，Mac 上的 Agent 读了待办和记录后回答（`/api/v1/bots/{bot_id}/messages`，见 [api.md](../docs/api.md)）；回答期间头部角色显示在忙，输入框暂时不能再发。
- **设置**：第一页只有几行，每行点进去是单独一页（和 iPhone 同一套）：连接（模拟数据开关、后端地址、令牌存钥匙串）、插件（每个插件一页：开关、仅预览、白名单或课程群、插件信息）、消息来源、运行时间表（读取消息和提炼待办的 cron，附中文说明；置信度阈值；优先使用的 Agent）、运行记录（一张表）、贴图预览。

菜单栏图标是 App 贴图在当前状态下的静止图（菜单栏不能播放动画）加今日剩余待办数；点开是一个小窗口：动画贴图、今日待办（可以直接勾选完成）、「立即提炼一次」「打开主窗口」。

数据流：`AppStore`（AssistantUI）从 `Backend` 读数据并执行操作，每 30 秒刷新一次，有任务在运行时每 2 秒刷新一次。`Backend` 有两个实现：`LiveBackend`（经 `APIClient` 访问后端）和 `MockBackend`（内存里的示例数据，完成、编辑、删除、立即运行、开关插件、改时间表都能操作）。页面要显示的文字和分组（来源、同步状态、插件一句话状态、摘要分组、运行记录的中文、cron 说明、编辑表单只发送改动的字段、Bot 列表和每个 Bot 的消息 `Bots.swift`）都在 AssistantCore 里，有单元测试。

## iPhone App

按手绘草图做，和 Mac 一样以 Bot 为中心：

- **主屏是一个 Bot 的页面**：最上面是它的脸，在一个它颜色的圆里（会眨眼，点一下有反应，Bot 说话时点头），下面是名字、一个标签写它管什么（每个 Bot 一样的格式：「学分 · 成绩」「作业 · 课表」，和列表里的「“学分”与“成绩”」是同一组词）和一句话状态；然后是卡片（一列，和 Mac 用同一套 `BotCards`）、收起卡片的把手、带纸飞机的波浪线，再下面是它说过的话（`ChatTimeline`，和 Mac 共用），最下面是输入框（消息来源没有）。下拉刷新。助理Bot 是个人助理：最前面是天气插件的卡片（今日提醒、今天的天气、出门要不要带伞和穿什么、明天，见 [weather.md](../docs/weather.md)），然后是今日待办和 Agent：点圆圈完成，点一下待办编辑，长按有完成、编辑、删除。页面顶上只有左上角打开 Bot 列表的按钮；插件开关、插件信息和运行记录都在设置里。
- **输入框**：像 Claude 的 App，文字在上面，下面一行左边是选模型的按钮（「✦ 自动 ⌄」），右边是发送。可以选「自动」（按设置里的 Agent 顺序），或者 Claude Code 的 Fable、Opus、Sonnet，或者 Codex、DeepSeek 列出来的模型；选的这个 Agent 先回答，失败时照常换另一个。选择记在这台设备上，Mac App 的输入框也一样。
- **Bot 列表从左边滑出**：在页面上向右滑，或者点左上角的按钮，页面往右让开，露出助理Bot 和角色 Bot（每个一行，圆形头像、名字、管什么，有新消息时加粗带圆点），一条横线下面是管知识库的知识库Bot；左下角是设置齿轮和连接状态。点一个 Bot 就换过去，向左滑或点右边露出的页面就收回去。App 下次打开时回到上次看的 Bot。消息来源（QQ、飞书、微信）不在这里，免得误点，在设置的「消息来源」里。
- **设置**：齿轮打开，第一页只有几行，每行点进去是单独一页：连接（后端地址、令牌、模拟数据）、插件（每个插件一页：启用开关、QQ 和微信的仅预览、白名单或课程群、插件信息）、消息来源（点一个就关掉设置、打开它读到的消息）、运行时间表、运行记录、贴图预览。Mac App 的设置是同一套两级页面（`Shared/SettingsPages.swift`），在窗口里一页页点进去；运行记录在 Mac 上是一张表。
- 颜色和字体和 Mac 一样，仿 Claude 的 App：米色的列表、更浅的页面、白色卡片，标题用系统衬线体；深色模式下换成对应的深色。

两个 App 共用 `AppStore` 和 AssistantCore 里的文字逻辑；页面容器分别在 `AssistantUI/Mac/` 和 `AssistantUI/Phone/`，共用的部分（配色 `Theme.swift`、头像和消息 `BotViews.swift`、卡片区和聊天 `BotPageParts.swift`、今日待办卡片 `TodayTodos.swift`）在 `AssistantUI/Shared/`。

## iPad App

iPhone target 是通用 App，同一份安装包支持 iPhone 和 iPad。iPhone 仍只竖屏；iPad 支持四个方向、分屏和 Slide Over。

- **宽屏**（`horizontalSizeClass == .regular`）：`PadRootView` 用系统的 `NavigationSplitView`。左边依次是助理Bot、角色 Bot（停用的在后）、横线、知识库Bot、「消息来源」，最下面是连接状态和设置。沿用 iPhone 列表的圆形头像、状态圆点、未读加粗和圆点，第二行显示 Bot 最近说的话，没有消息时显示它管什么。横屏默认显示侧栏，竖屏可用左上角的系统按钮展开或收起。
- **右侧详情**：头部角色、卡片、收起卡片的把手、纸飞机分隔线、聊天和输入框；卡片、聊天和动画复用共享组件。`CardGrid` 按详情栏可用宽度自动排多列（卡片至少 270 点宽，常见竖屏约两列、横屏约三列）；助理Bot 的今日待办和 Agent、知识库Bot 的知识库入口不进网格，和 Mac 一样按固定顺序各占一行。设置、插件、运行记录和知识库都在右侧一页页打开。收起卡片、选择 Bot、打开设置均可直接触控，动画尊重「减弱动态效果」。接键盘时沿用输入框的 Return 发送，`⌘R` 刷新，`⌘,` 打开设置。
- **窄屏**（分屏、Slide Over 的 `compact` 宽度）：直接用 `PhoneRootView`，保留 iPhone 的抽屉和单列卡片。iPad 两种布局共用本机 `assistant.pad.bot` 选择记录，iPhone 仍用自己的记录。
- **健康和手表**：iPad 可以查看后端的健康卡片和简报，但不请求 HealthKit 权限、不启动健康上传，设置中也没有「健康数据」页；上传只由 iPhone 负责。iPad 不启动 `PhoneWatchBridge`，不和手表配对。令牌仍是每台设备各自保存在钥匙串里的设备令牌。

iPad 专用容器在 `AssistantUI/Pad/`；选择恢复、侧栏预览文字在 AssistantCore 的 `PadPresentation.swift`，网格列数在 `CardPacking.swift`，均有单元测试。`PadRootView` 的 Preview 明确使用 `MockBackend`，本地检查无需连接真实后端。

## Apple Watch

手表 App 上下翻三页（手指上下滑或转数码表冠），每页顶上是那个 Bot 的头像和名字：

- **助理Bot · 今日待办**：还剩几件、什么时候更新的；点一条勾选完成；最下面「让 iPhone 刷新」。
- **作业Bot · 今天的课**：每节课的时间和课名，正在上的标「正在上」、还没开始的第一节标「下一节」，上完的变淡（每分钟更新）；学校网站还没显示这一周、按上周推测的会写出来。课表插件没开或还没读到时，这一页说明原因。
- **健康Bot · 准备度**：今日准备度画成满分 10 的圆环，旁边是昨天的分数，下面是各项分数；再下面是昨晚睡眠。数据不足时显示原因。

手表**不直接连后端，也不需要令牌**：watchOS 上没有 Tailscale，离开家里的网络就连不上 Mac，所以数据都经过 iPhone。

- iPhone 每次刷新后，用 WatchConnectivity 的 `updateApplicationContext` 把一份小快照发给手表：今日待办（标题、截止时间文字、是否逾期、来源、是否完成）、剩余件数、Agent 状态、iPhone 是否连得上 Mac；还有从 `/api/v1/today` 的插件卡片里取出的「今天的课」（课表）、「今日准备度」和「昨晚睡眠」（健康），以及这两个插件的贴图。手表离开 iPhone 时只会收到最新的一份。旧版本 App 发来的快照没有后两页的数据，照样能读。
- 手表上可以**勾选完成**：先在手表上打勾，再请 iPhone 去改。iPhone 在附近时用 `sendMessage` 立刻处理并回传新快照；不在附近时用 `transferUserInfo` 排队，iPhone 收到后再处理。
- 手表上的「让 iPhone 刷新」会请 iPhone 从 Mac 重新读取，再把新快照发回来；iPhone App 不在前台时，系统会在后台唤醒它处理。
- **表盘小组件**（`Assistant-watchWidget`，WidgetKit）显示今日剩余待办数，支持圆形、角落、单行和矩形四种样式。手表 App 收到新快照后把数字写进 App Group，并通知小组件刷新。
- 快照、请求和小组件数据的格式在 AssistantCore 的 `WatchSync.swift`，有单元测试；iPhone 端在 `AssistantUI/Phone/PhoneWatchBridge.swift`，手表端在 `AssistantUI/Watch/`。
- 手表 App 是 iPhone App 的配套 App（`WKCompanionAppBundleIdentifier`，`WKRunsIndependentlyOfCompanionApp` 为否），bundle ID 是 `<前缀>.ios.watch`，小组件是 `<前缀>.ios.watch.widget`。它嵌在 iPhone App 里（XcodeGen 给 iPhone target 生成「Embed Watch Content」，复制到 `Watch/`），安装 iPhone App 时一起装上；手表 target 设了 `SKIP_INSTALL`，归档时只出一个 iPhone App。
- 手表 App 刚装好（或者换了一块手表）时，iPhone 收到 `sessionWatchStateDidChange`，立刻把今日快照发过去，不用等下一次刷新。
- 手表 App 和小组件共用 App Group `group.<前缀>`，用来传剩余件数。免费的个人开发者账号如果不能用 App Group，小组件会显示「–」，手表 App 本身不受影响。
- 在模拟器里截图第二、三页：调试版本启动时加 `-page courses` 或 `-page health`（`xcrun simctl launch <手表> <bundle ID> -page courses`）就直接打开那一页。

## App 图标

三端共用 `App/AppIcon.icon`，是 Icon Composer 的分层图标（iOS 26、watchOS 26、macOS 26 用液态玻璃画它）：白色的底，助理的头单独一层放在上面。默认外观是白底彩色；在主屏「自定义」里选「透明」或「色调」时，底变成透出壁纸的玻璃，头像变成玻璃质感，和系统图标一样，不会是一块白板。更早的系统用的平面图由 Xcode 编译时从它生成。可以用 Xcode 自带的 Icon Composer 打开微调。换贴图或改样子后重新生成：

```bash
swift scripts/make-app-icon.swift Design/stickers/aemeath.png App/AppIcon.icon
```

## 持续集成

`.github/workflows/frontend.yml` 只在 `frontend/` 或这个 workflow 有改动时运行，而且只在 PR 和合并到 main 时各跑一次（macOS 分钟数按 10 倍计费）：安装 XcodeGen → 生成项目 → `swift test` → 关闭签名编译 iOS（手表 App 和小组件作为它的依赖一起按 watchOS 模拟器编译，所以不再单独编译 watchOS）→ 用 `scripts/check-watch-embed.sh` 检查编译出的 iPhone App 里确实有手表 App：只有一个、bundle ID 以 iPhone App 的开头、`WKCompanionAppBundleIdentifier` 指向 iPhone App、是 watchOS 版本、带着小组件 → 编译 macOS。

