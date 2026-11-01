# Aemeath Bot 安卓版

手机和平板端移植，以 `frontend/Packages/AssistantCore` 的 iPhone、iPad 实现和 `docs/api.md` v1 为准。仅新增 `android/`、本文和 Android 工作流，不修改后端、iPhone App 或移植说明。

## iPhone 功能清单与对应实现

先按现有 iPhone 页面逐项列出范围，开发与验收共用这张清单：

| iPhone 功能 | 安卓文件（android/ 下） |
| --- | --- |
| 连接地址、设备令牌、离线模拟数据 | app/…/data/DeviceSettings.kt；core/…/MockBackend.kt |
| Bot 合并、排序、角色名、职责、状态文字 | core/…/Bots.kt、Presentation.kt |
| 主屏角色圆头像、眨眼、点击反应、说话点头、角色动作 | app/…/ui/Mascot.kt；core/…/Mascot.kt |
| 侧滑让开主屏、未读标记、恢复上次 Bot | app/…/ui/BotScreen.kt、App.kt |
| 文字输入、Agent/模型选择、回复轮询、失败保留草稿 | app/…/ui/BotScreen.kt、data/AppModel.kt |
| 六种卡片；列表/时间轴/圆环；数字条；折线/堆叠柱/圆环；表格 | core/…/Cards.kt；app/…/ui/Cards.kt |
| 今日待办、完成/恢复/编辑/删除、来源与同步状态 | app/…/ui/TodoViews.kt；core/…/TodoDraft.kt |
| 卡片折叠、素雅/点彩/柔光、下拉刷新、分日聊天与更早消息 | app/…/ui/BotScreen.kt、Theme.kt |
| 知识库：主题、搜索、读者权限、系统目录、默认读者、Agent、新建主题 | core/…/Knowledge.kt；app/…/ui/KnowledgeScreen.kt |
| 知识库文件/照片上传、文字、来源/关联笔记、原件 | app/…/ui/KnowledgeScreen.kt |
| 准备度：日期、Apple Fitness 0–10（手填）、自评 1–5 | core/…/Health.kt；app/…/ui/TodoViews.kt |
| 设置菜单 → 连接、插件详情、消息来源、时间表、记录、卡片风格、贴图、健康 | app/…/ui/SettingsScreen.kt |
| 插件开关、仅预览、白名单/课程群、完整配置保留 | core/…/Presentation.kt；app/…/ui/SettingsScreen.kt |
| 健康授权、逐条增量上传、前台补传、后台上传、上传状态 | app/…/health/HealthSync.kt、HealthRecordMapping.kt；core/…/HealthSamples.kt、HealthIncrementalSync.kt |
| 平板侧栏、预览、多列卡片、选择恢复 | app/…/ui/Tablet.kt、App.kt；core/…/PadPresentation.kt、CardPacking.kt |
| PDF 和图片原件预览、缩放 | app/…/ui/OriginalPreview.kt、KnowledgeScreen.kt |
| 浅色/深色 JVM 截图 | app/src/test/…/ui/UiScreenshotTest.kt |

下文补充构建、连接、差异和验证说明。

## 工程与版本

`android/core` 是不依赖 Android 的 Kotlin/JVM 模块：模型、HTTP 客户端、演示后端、卡片、页面文字、Bot 分组/消息、知识库权限保存、待办编辑、贴图时序、健康样本映射与增量同步；旧的健康汇总 API 保留供共享客户端使用。`android/app` 是单 Activity Compose 自适应手机/平板界面、Keystore、系统文件选择器、Health Connect 和 WorkManager。包名和 applicationId 都是 `local.assistant.android`，显示名称为 Aemeath Bot。默认打开模拟模式。

| 工具 / 库 | 固定版本 |
| --- | --- |
| JDK | 17 |
| Gradle | 8.13 |
| Android Gradle Plugin | 8.11.1 |
| Kotlin / serialization 插件 / Compose 编译器插件 | 2.2.20 |
| Compose BOM | 2025.04.01 |
| compileSdk / targetSdk / minSdk | 36 / 36 / 28（Android 9） |
| SDK Build Tools | 35.0.0（AGP 默认值） |
| kotlinx.serialization / coroutines | 1.8.1 / 1.10.2 |
| OkHttp | 4.12.0 |
| Health Connect / WorkManager | 1.1.0 / 2.10.1 |
| Roborazzi / Robolectric（仅测试） | 1.50.0 / 4.14.1 |

这是固定的稳定版本组合，没有使用动态版本或 alpha。[AGP 8.11 官方兼容表](https://developer.android.com/build/releases/agp-8-11-0-release-notes)对应 Gradle 8.13、JDK 17、Build Tools 35.0.0 和 API 36；Kotlin 2.2.20 的支持范围见 [Kotlin 官方兼容表](https://kotlinlang.org/docs/gradle-configure-project.html)。Compose 编译器插件与 Kotlin 同版本，界面库由 BOM 锁定；Health Connect 1.1.0 为[稳定版](https://developer.android.com/jetpack/androidx/releases/health-connect#1.1.0)。

贴图通过 `app/build.gradle.kts` 直接把 `../../frontend/Design/stickers` 作为 assets。启动图标在构建时从 iPhone 的 `AppIcon.icon/Assets/aemeath.png` 生成资源，生成文件只在 `build/`，没有在源码里复制图片。

## 编译

本次开发遵守移植约束：没有在这台 Mac 上安装/下载 JDK 或 Android SDK，没有执行 Android 构建，也没有连接正在运行的后端。下面的命令供已有工具的开发机或 GitHub Actions 使用。

命令行环境需要 JDK 17、Gradle 8.13 和 Android SDK。`ANDROID_HOME` 指向 SDK；Android Studio 用户也可以在未跟踪的 `android/local.properties` 中设置 `sdk.dir`。

```sh
cd android
sdkmanager 'platforms;android-36' 'build-tools;35.0.0'
python3 scripts/check_sources.py
gradle --no-daemon --stacktrace assembleDebug testDebugUnitTest :core:test
```

APK 在 `android/app/build/outputs/apk/debug/app-debug.apk`。仅使用 Android 默认调试签名，没有发布配置、发布密钥或签名文件。

仓库按要求没有 Gradle wrapper 二进制。在有 Gradle 8.13 的开发机上可自行生成本地 wrapper：

```sh
cd android
gradle wrapper --gradle-version 8.13 --distribution-type bin
./gradlew assembleDebug testDebugUnitTest :core:test
```

生成的脚本和 `gradle-wrapper.jar` 已忽略，不提交它们。Android Studio 中打开 `android/`，Gradle JDK 选 17，安装 API 36；建议先生成本地 wrapper 再同步。也可在 Gradle 设置中选择本地 Gradle 8.13。不要接受 IDE 自动升级 AGP/Kotlin 的建议后只改其中一个版本。

## 装到手机

手机打开「开发者选项 → USB 调试」，USB 连接后允许这台电脑调试：

```sh
adb devices
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n local.assistant.android/.MainActivity
```

这些命令在仓库根目录执行；在 `android/` 下执行时去掉 APK 路径前的 `android/`。已有其他调试签名的安装会提示 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`；需要先卸载旧安装，再装这份 APK，卸载会清除该 App 本机设置和令牌。

## 连接后端

1. 后端电脑和安卓手机登录同一个 Tailscale 网络，确认手机能访问后端的 Tailscale 地址。
2. App 左侧抽屉下方齿轮 → 连接。关闭「使用模拟数据」，填写例如 `http://100.x.y.z:8765`，或自己的 Tailscale HTTPS 地址。填服务根地址，App 自动追加 `/api/v1`；反向代理的路径前缀会保留。手机的 `127.0.0.1` 指手机自身。
3. 粘贴后端为这台设备创建的 **app 权限** Bearer Token，点「保存并连接」。已有令牌时留空保留原值。只含 `health_upload` 权限的令牌不能浏览 App 页面。
4. 连接成功后可下拉刷新。401 表示令牌无效，403 表示权限不足；连接失败保留已有页面，切回模拟模式可继续查看演示。

令牌使用 Android Keystore 中的 AES-GCM 密钥加密；SharedPreferences 只存密文与 IV，`allowBackup=false`。聊天和健康原始采样不写入本机持久缓存或日志。HTTP 客户端不记录请求/响应正文、不自动跟随重定向，不把 Bearer Token 交给系统文件查看器。App 没有统计、广告或崩溃上报 SDK，也不直接连接聊天软件。允许明文 HTTP 是为了兼容 Tailscale 网络内已有的 HTTP 服务，网络隔离仍由用户配置的后端与 Tailscale 负责。

知识库资料只上传到所填后端，文件选择用 Android 系统选择器，无需整个存储空间权限。原件通过鉴权接口流式下载到私有缓存，PDF 用 Android PdfRenderer、图片用 BitmapFactory 在 App 内预览，可双指缩放，关闭时删除临时文件；DOCX 等其他格式以临时 FileProvider 读权限交给用户选择的查看器。遗留缓存超过一天后在下一次打开原件时清理。Markdown 中的网页链接由用户点击后交给系统浏览器。

## 平板与窗口布局

实际窗口宽度 ≥600dp（Medium/Expanded）显示 280dp 侧栏与右侧详情；Compact 窗口使用手机抽屉。侧栏依次为助理Bot、角色 Bot（停用的在后）、分隔线、知识库Bot、消息来源，底部保留连接状态与设置。每行显示头像、状态点、未读标记和 Bot 最近说的话；没有消息时显示职责，用户消息与 App 通知不会替换预览。

详情沿用角色头部、卡片把手、纸飞机分隔线、聊天与输入框。卡片按实际可用宽度计算列数，每列至少 270dp，按实测高度分组；助理Bot 的插件卡片在前，今日待办和 Agent 固定在后；知识库Bot 的知识库入口和资料来源在网格前。手机保留原有单列及方块配对。

手机选择继续用 `chosen`，平板用 `chosen.tablet`；平板分屏切换布局时仍用同一选择。插件加载完成前不会清掉保存的选择，已删除的 Bot 在加载完成后回退到助理Bot。设备类型按完整屏幕识别，独立于分屏窗口宽度。手机锁定竖屏，平板允许四个方向。平板不请求 Health Connect 权限，不读、不上传健康数据；已有后端健康简报仍正常显示。

## Health Connect 权限与上传

设置 → 健康数据 →「允许读取健康数据」。只申请读取，不申请写入。支持的类型：心率、静息心率、HRV RMSSD、呼吸频率、血氧、步数、活动能量、最大摄氧量、睡眠和训练；皮肤温度和后台读取先检查系统功能是否可用，再申请权限。用户可只允许一部分类型，App 只读已授权的部分；没有 Health Connect 的手机仍能使用其余页面。

Android 14 及以上使用系统 Health Connect；较早的兼容设备需要安装/更新提供方。权限页可以打开系统设置、重新授权、立即上传或关闭自动上传。应用内有健康数据使用说明，Manifest 同时提供旧版 rationale 和 Android 14 的权限使用说明入口。系统背景读取要求见 [Health Connect 官方读取说明](https://developer.android.com/health-and-fitness/health-connect/read-data)。

上传仍为 `POST /api/v1/health/samples` 的 `{samples:[{id,type,value,unit,start_at,end_at,source}]}`，每批最多 1000 项：

- 每个 Health Connect 类型分别持有 changes token；第一次先取得 token，再分页读取最近 14 个当地日历日（保留当前时刻，和 iPhone 一样），最后消费扫描期间新增的 changes，避免分页时漏数据。以后只读取该类型的变化。
- 心率 series 拆成每个实际读数，保留时间；静息心率、RMSSD、呼吸频率、血氧、VO₂ max 逐条映射；步数、活动能量保留原始区间与数值。当天的样本可以立即上传，无须等一天结束。
- 训练按次上传 `workout:<activity>`，保留 ID、类型、分钟数、起止时间、来源这七个 iPhone HealthSample 字段，分钟数扣除已记录的暂停/休息片段；不按天合并。睡眠按实际阶段上传，LIGHT 对应 `sleep_core`，未知阶段省略；只有没有阶段的睡眠记录用 `sleep_asleep` 表示未分期。
- RMSSD 使用 `hrv_rmssd`，不冒充 SDNN。只有测量部位明确为手腕、且有绝对基准温度时，才用基准加温差算出 `wrist_temperature`；只有相对温差时省略。没有授权或来源未提供的类型不补造。
- 独立记录沿用 Health Connect ID；series/stage 使用记录 ID、时间和序号派生稳定 ID。每页可拆多批，全部由后端确认 accepted/duplicates 后才持久保存 token；中断后重复批次由后端去重。changes token 过期后重读可访问的最近 30 天并去重，之后继续增量。更早的缺口受 Health Connect 历史读取范围限制。
- 前台自动同步至少间隔 5 分钟，手动按钮可立即执行；切换连接会清除各类型游标，重新向目标后端补传。上传前和保存游标前检查连接、令牌、同步开关。
- WorkManager 按小时请求后台执行，要求网络可用；失败指数退避，最多重试 5 次。有后台读取授权才读后台健康数据，未授权时只在前台补传；模拟模式和平板始终不上传。

健康样本接口当前按 ID 去重，已存在 ID 的修正不会覆盖已有数据，也没有删除样本接口；和 iPhone 一样不会转发删除记录。没有把更新改成新 ID 再累计，以免重复计数。changes token 的分页、过期与按类型隔离参考 [Health Connect 官方同步文档](https://developer.android.com/health-and-fitness/health-connect/sync-data)。

## 和 iPhone 版的差别

- 已补上对应 iPad 的平板布局。Mac 菜单栏、Apple Watch 配对和表盘复杂功能属于其他平台；本轮不做 Wear OS 客户端。
- 健康来源改为 Health Connect，不访问 HealthKit 或直接连接 Apple Watch。Android 后台由 WorkManager 调度，不具有 HealthKit background delivery 的推送时机；系统省电和厂商后台限制会延迟运行。
- 样本种类、粒度和 HealthBatch 字段已与 iPhone 对齐。Health Connect 通常提供 RMSSD，不能据此计算 SDNN；腕温需要来源提供手腕部位与基准温度，未提供的读数缺省。实际采样频率取决于手表和写入 Health Connect 的来源 App。
- 提醒事项与日历仍由同一个后端同步到 Apple，安卓端不另外请求本机日历或提醒事项权限。
- 标题用 Android `FontFamily.Serif`，中文最终字形取决于手机系统的字体回退，不下载 Apple 宋体。配色、圆角与页面顺序沿用原版；系统选择器、日期/时间控件、部分 SF Symbols 用 Android 控件或 Canvas/系统字形替代，尚未做真机截图逐像素比较。
- 角色原图、眼睛分层、动作选择与时序沿用原版，24 种道具以 Canvas 移植；系统字形和绘制抗锯齿会有平台差异。
- PDF 和图片已支持 App 内预览。DOCX 等其他格式通过系统查看器打开，未安装查看器时提示失败；Android 没有 iOS Quick Look 的统一 Office 预览服务。模拟数据只提供合成的 PDF 演示原件。

## 测试与复用数据

目前声明 **218 项 JVM 测试**：`core` 186 项，`app` 32 项（含 16 项截图）。本轮增加 **62 项**：33 项纯逻辑、13 项 Android 映射/设备策略、16 项截图。**尚未实际运行这些测试**；本机没有任务所需 JDK/SDK。已执行的是源文件审计和 Kotlin 语法树检查，不能据此称为编译或测试通过。

| Swift 测试范围 | 安卓对应测试 |
| --- | --- |
| Model / ChatChoice / TodoDraft | ModelsTest |
| APIClient 与知识库路由 | BackendTest（MockWebServer） |
| Card / 手机 CardPacking | CardsTest |
| Presentation / AgentText / DueText | PresentationTest |
| Bot / BotChat / BotTimeline | BotsTest |
| MockBackend / 准备度上传去重 | MockBackendTest |
| Knowledge / KnowledgeReaderSelection | KnowledgeTest（含连续修改与失败回滚） |
| ReadinessCheckin / 旧健康日汇总 API | HealthTest（保留共享客户端兼容性） |
| iPad 侧栏预览、排序、选择恢复、网格列数和高度分组 | PadPresentationTest、CardPackingTest |
| 样本映射、批次、上传确认、分页、首次扫描竞争、token 过期和权限撤销 | HealthSamplesTest、HealthIncrementalSyncTest |
| Mascot / MascotMotion / MascotAnimation | ArtworkTest、MascotTest |
| Markdown | MarkdownTest |
| Android Health Connect 类型映射、平板禁用上传/权限、独立选择、连接游标清理 | app 的 HealthMappingTest、TabletHealthTest |
| 页面浅色/深色截图 | app 的 UiScreenshotTest |

iPad 的列数、卡片分组、预览和选择恢复断言已移植。Mac 自动收卡片、菜单栏与 Apple Watch 专用断言不在本轮范围。手机保留把手折叠和原版 `phoneRows` 配对。

截图在 Robolectric API 35 Native Graphics 上运行，无模拟器、真实后端、令牌或健康数据。覆盖手机助理Bot、健康Bot、知识库Bot、抽屉展开、设置首页、知识库首页，以及 1280×800dp 平板横屏的助理Bot、健康Bot 侧栏与详情，全部浅色/深色，共 16 张。使用固定时间、时区、演示后端和静止角色，等待贴图加载完成后截图。测试渲染生产页面的 `AppContent`，不启动生命周期轮询。

```sh
# android/ 下，已有 JDK/SDK 的开发机
# 仅记录图片，暂不做像素比对
gradle --no-daemon :app:recordRoborazziDebug --tests '*UiScreenshotTest'
```

图片在 `android/app/build/outputs/roborazzi/`，CI 上传为 `android-ui-screenshots` artifact。配置方式见 [Roborazzi 1.50.0 文档](https://github.com/takahirom/roborazzi/tree/1.50.0)。本机没有生成截图，仍需在 CI 首次运行后查看图片。

`core/src/test/resources/fixtures` 的 17 份 JSON 直接来自 Swift `Fixtures.swift`（后端真实返回、合成内容）及 `KnowledgeTests.swift`，不另写一份近似数据。`core/src/main/resources/mock` 的 23 份 JSON 由原版 MockBackend/MockData 在固定时刻导出，运行时按当前日期平移；安卓演示里的待办修改、插件开关、设置、知识库权限、上传、延迟回复和任务运行可交互。

无需 JDK/SDK 的检查：

```sh
python3 android/scripts/check_sources.py
```

它比较 Swift/安卓 fixture 的 JSON 值，检查全部 JSON/XML、贴图图层尺寸及纯逻辑模块边界，并报告测试数量；不执行 Kotlin。需要刷新同源数据时，在已有 Swift 的 Mac 上执行：

```sh
python3 android/scripts/check_sources.py --regenerate
```

该命令仅调用已有 `swiftc`，使用临时目录编译 `scripts/ExportFixtures.swift`，只重写安卓资源，不启动后端、不改 Swift 源码。

## CI 命令与首轮排错

`.github/workflows/android.yml` 只对 `android/**` 或该工作流本身的 PR/push 变化触发，push 范围为 `main` 与 `local/android`，另外支持手动运行。Ubuntu 配好 JDK 17、Gradle 8.13 和 SDK 后执行：

```sh
# 工作目录 android/
python3 scripts/check_sources.py
gradle --no-daemon --stacktrace assembleDebug :core:test
gradle --no-daemon --stacktrace :app:recordRoborazziDebug
```

`:core:test` 显式执行纯 JVM 模块测试；`:app:recordRoborazziDebug` 执行全部 App 单元测试并记录截图，不加筛选，保留完整 App 测试报告。仅运行 `testDebugUnitTest` 不会自动执行 JVM 模块测试。工作流上传调试 APK、JUnit XML 和 HTML 测试报告，并单独上传截图，无须设备、令牌或真实健康数据。

本次没有运行 GitHub Actions；尚未通过实际编译确认 Kotlin/Compose 类型检查和测试运行。首轮 CI 重点检查：

1. **工具链与依赖获取**：`SDK location not found`、SDK license、Google Maven 下载失败先检查 SDK/网络；确认 Gradle 8.13、JDK 17、platforms;android-36、build-tools;35.0.0，避免 IDE 自带 JDK/AGP 升级混入。
2. **Kotlin 类型与 UI 重载**：语法检查不能发现 Compose 的 receiver/重载、Material 3 `PullToRefreshBox` / 进度条 / `LinkAnnotation` 等调用的类型错误。若出现 `Unresolved reference` / `Type mismatch`，先单跑 `gradle :app:compileDebugKotlin --stacktrace` 定位；API 以锁定 BOM 为准。
3. **Health Connect API**：皮肤温度、背景读取和训练类型已按 1.1.0 源码核对，仍需编译与设备端确认。不要用删权限或编造其他指标的方式绕过失败。
4. **资源链接**：若提示找不到 `ic_character`，检查 `:app:generateLauncher`、源图路径及 `preBuild` 依赖；若贴图空白，检查 assets 源目录，不能另复制图片掩盖路径问题。
5. **单元测试**：先跑 `gradle :core:test`，再跑 `gradle :app:testDebugUnitTest`。查看上传的报告；不要将纯逻辑测试改成依赖 Mac、模拟器或真实后端。
6. **截图首轮**：重点看 Robolectric Android 15 运行包/Native Graphics 的下载、内存、字体及 Compose 空闲等待；已固定 API 35，避免 compileSdk 36 自动选择未支持的测试运行版本。若图片缺失，查看 record 任务的 JUnit 报告；不要把缺图改成成功。每张图首次生成仍需人工目检。

## 尚未真机验证

- [ ] CI 完整编译、218 项 JVM 测试、调试 APK 启动。
- [ ] Android 9/13/14/16 的导航、返回键、键盘遮挡、横向手势与下拉刷新。
- [ ] 平板四向旋转、分屏 Compact/Medium/Expanded 切换、独立选择恢复、健康数据权限不弹出。
- [ ] 浅色/深色、中文衬线回退、字体放大、TalkBack、刘海和系统导航栏。
- [ ] 与 iPhone 模拟模式逐页对照截图，特别是头像圆框、眨眼遮罩、24 个道具、图表和卡片配对。
- [ ] Tailscale HTTP/HTTPS、路径前缀、无效/撤销令牌、断网恢复和连接切换。
- [ ] 待办完成/恢复/改日期/删除以及后端实际提醒事项、日历同步。
- [ ] 对话模型持久化、延迟/失败回复、未读标记、多天消息、应用退到后台再返回。
- [ ] 知识库连续撤销权限、锁定读者、上传失败/重名/50 MiB 限制、Photo Picker、PDF/图片/DOCX 查看器。
- [ ] Health Connect 完整/部分/撤销授权，无提供方、旧版提供方及后台能力不可用。
- [ ] 真实手表来源、逐条心率/步数/能量、每次训练、RMSSD/睡眠/腕温映射、跨时区、跨日、夏令时、changes token 过期。
- [ ] WorkManager 在锁屏、省电、重启、网络恢复和 OEM 后台限制下的补传；模拟模式或关闭同步后不上传。
- [ ] Keystore 令牌重启后解密、卸载/迁移/备份行为，以及缓存原件的临时读取权限。
