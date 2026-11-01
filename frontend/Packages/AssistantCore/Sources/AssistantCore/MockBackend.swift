import Foundation

/// An in-memory backend with invented data (no real names or chats), so every screen can be
/// used before the Mac mini exists. Changes behave like the real API: completing a todo
/// removes it from today, deleting is final, a started job runs for a few seconds and then
/// succeeds, plugins can be switched on and off and the schedule edited.
public actor MockBackend: Backend {
    private var todoItems: [Todo]
    private var digestItems: [Digest]
    private var runItems: [Run]
    private var pluginItems: [PluginView]
    private var pluginCards: [String: [Card]]
    private var reports: [HealthReport]
    private var runtime: RuntimeSettings
    private var online: Bool
    private var chats: [String: [ChatMessage]] = [:]
    private var library: MockKnowledge
    private var nextID = 100
    private var uploadedIDs: Set<String> = []
    private let clock: @Sendable () -> Date
    private let calendar: Calendar

    /// How long a started job runs before it succeeds.
    public static let jobDuration: TimeInterval = 4
    /// How long a Bot takes to answer in chat.
    public static let replyDelay: TimeInterval = 2
    public static let replyText =
        "（模拟数据里的示例回复）连上 Mac 上的后端以后，我会查你的待办和记录来回答，也能帮你记待办。"

    public init(
        now: Date = Date(), calendar: Calendar = DueText.calendar(),
        clock: @escaping @Sendable () -> Date = { Date() }, online: Bool = true
    ) {
        let scenario = MockScenario(now: now, calendar: calendar)
        todoItems = scenario.todos
        digestItems = scenario.digests
        runItems = scenario.runs
        pluginItems = scenario.plugins
        pluginCards = scenario.cards
        reports = scenario.reports
        runtime = scenario.settings
        library = MockKnowledge(now: now)
        self.online = online
        self.clock = clock
        self.calendar = calendar
    }

    func check() throws {
        guard online else { throw APIError.transport("offline (mock)") }
    }

    public func setOnline(_ value: Bool) {
        online = value
    }

    public func status() async throws -> Status {
        try check()
        let runs = try await self.runs()
        return Status(
            online: true, platform: "darwin", appleMode: "real",
            agents: [
                "claude_code": AgentStatus(
                    available: true,
                    models: [
                        AgentModel(id: "fable", name: "Fable"), AgentModel(id: "opus", name: "Opus"),
                        AgentModel(id: "sonnet", name: "Sonnet"),
                    ]),
                "codex": AgentStatus(
                    available: true,
                    models: [
                        AgentModel(id: "gpt-6.1-sol", name: "GPT-6.1-Sol", isDefault: true),
                        AgentModel(id: "gpt-6-luna", name: "GPT-6-Luna"),
                    ]),
            ],
            lastRun: runs.first,
            pendingSync: todoItems.filter { $0.syncStatus == .pending }.count)
    }

    public func today() async throws -> Today {
        try check()
        let now = clock()
        let end = calendar.startOfDay(for: now)
        let due = todoItems.filter { todo in
            guard let date = todo.dueAt, todo.status == .pending else { return false }
            return calendar.startOfDay(for: date) <= end
        }
        .sorted { ($0.dueAt ?? now) < ($1.dueAt ?? now) }
        return Today(
            date: DueText.format(now, "yyyy-MM-dd", calendar),
            summary: "班级群有 2 条截止通知，实验室提醒年度材料明天截止，今天没有新的健康预警。",
            todos: due,
            // Every running plugin's cards, as the backend's /today.
            cards: pluginItems.filter { $0.state == .enabled }.flatMap { pluginCards[$0.id] ?? [] })
    }

    public func todos(status: TodoStatus?) async throws -> [Todo] {
        try check()
        return todoItems.filter { status == nil ? $0.status != .deleted : $0.status == status }
    }

    public func updateTodo(id: String, _ patch: TodoPatch) async throws -> Todo {
        try check()
        guard let index = todoItems.firstIndex(where: { $0.id == id }) else {
            throw APIError.notFound
        }
        var todo = todoItems[index]
        if todo.status == .deleted && patch.status != .deleted { throw APIError.conflict }
        if let title = patch.title { todo.title = title }
        if let notes = patch.notes { todo.notes = notes }
        switch patch.due {
        case .set(let date): todo.dueAt = date
        case .clear:
            todo.dueAt = nil
            todo.hasTime = false
        case nil: break
        }
        if let hasTime = patch.hasTime { todo.hasTime = hasTime }
        if todo.hasTime && todo.dueAt == nil { throw APIError.invalidRequest }
        if let status = patch.status { todo.status = status }
        if todo.status == .deleted {
            todo.reminderID = nil
            todo.calendarEventID = nil
        }
        todo.updatedAt = clock()
        todo.syncStatus = .synced
        todoItems[index] = todo
        return todo
    }

    public func digests() async throws -> [Digest] {
        try check()
        return digestItems.sorted { $0.date > $1.date }
    }

    public func runs() async throws -> [Run] {
        try check()
        let now = clock()
        for index in runItems.indices where runItems[index].status == .running {
            let end = runItems[index].startedAt.addingTimeInterval(Self.jobDuration)
            if now >= end {
                runItems[index].status = .succeeded
                runItems[index].finishedAt = end
            }
        }
        return runItems.sorted { $0.startedAt > $1.startedAt }
    }

    public func runJob(_ jobID: String) async throws -> JobAccepted {
        try check()
        _ = try await runs()
        let known = ["extract_todos", "apple_sync", "backup", "memory_index"]
            + pluginItems.filter { $0.state == .enabled }.map { "fetch:\($0.id)" }
        guard known.contains(jobID) else { throw APIError.notFound }
        if runItems.contains(where: { $0.job == jobID && $0.status == .running }) {
            throw APIError.conflict
        }
        nextID += 1
        runItems.append(
            Run(
                id: "mock-run-\(nextID)", job: jobID,
                harness: jobID == "extract_todos" ? runtime.harnessOrder.first : nil,
                startedAt: clock(), finishedAt: nil, status: .running, error: nil, attempt: 1))
        return JobAccepted(jobID: jobID, status: "accepted")
    }

    public func plugins() async throws -> [PluginView] {
        try check()
        return pluginItems
    }

    /// Accepts samples like the backend: the same ID twice counts as a duplicate.
    public func uploadHealth(_ batch: HealthBatch) async throws -> HealthAccepted {
        try check()
        let fresh = batch.samples.filter { !uploadedIDs.contains($0.id) }
        uploadedIDs.formUnion(fresh.map(\.id))
        return HealthAccepted(accepted: fresh.count, duplicates: batch.samples.count - fresh.count)
    }

    public func updatePlugin(id: String, _ patch: PluginPatch) async throws -> PluginView {
        try check()
        guard let index = pluginItems.firstIndex(where: { $0.id == id }) else {
            throw APIError.notFound
        }
        var plugin = pluginItems[index]
        if let enabled = patch.enabled {
            guard plugin.state != .unsupported || !enabled else { throw APIError.conflict }
            plugin.enabled = enabled
            plugin.state = enabled ? .enabled : .disabled
            plugin.error = nil
        }
        if let config = patch.config { plugin.config = config }
        pluginItems[index] = plugin
        return plugin
    }

    public func cards(pluginID: String) async throws -> [Card] {
        try check()
        guard let plugin = pluginItems.first(where: { $0.id == pluginID }) else {
            throw APIError.notFound
        }
        return plugin.state == .enabled ? pluginCards[pluginID] ?? [] : []
    }

    public func healthReports() async throws -> [HealthReport] {
        try check()
        return reports.sorted { $0.date > $1.date }
    }

    public func settings() async throws -> RuntimeSettings {
        try check()
        return runtime
    }

    public func updateSettings(_ patch: SettingsPatch) async throws -> RuntimeSettings {
        try check()
        for cron in [patch.sourceCron, patch.extractionCron].compactMap({ $0 }) {
            guard CronText.isValid(cron) else { throw APIError.invalidRequest }
        }
        if let value = patch.timezone { runtime.timezone = value }
        if let value = patch.sourceCron { runtime.sourceCron = value }
        if let value = patch.extractionCron { runtime.extractionCron = value }
        if let value = patch.confidenceThreshold { runtime.confidenceThreshold = value }
        if let value = patch.harnessOrder { runtime.harnessOrder = value }
        if let value = patch.jobHarness { runtime.jobHarness = value }
        if let value = patch.agentTimeout { runtime.agentTimeout = value }
        if let value = patch.retryAttempts { runtime.retryAttempts = value }
        if let value = patch.retryBaseSeconds { runtime.retryBaseSeconds = value }
        return runtime
    }

    public func chat(botID: String) async throws -> [ChatMessage] {
        try check()
        let now = clock()
        // A reply is written `replyDelay` after the question.
        chats[botID] = chats[botID]?.map { line in
            guard line.status == .pending, now >= line.createdAt.addingTimeInterval(Self.replyDelay) else {
                return line
            }
            var line = line
            line.status = .ready
            line.text = Self.replyText
            line.updatedAt = line.createdAt.addingTimeInterval(Self.replyDelay)
            return line
        }
        return chats[botID] ?? []
    }

    public func sendChat(botID: String, text: String, botName: String?, choice: ChatChoice) async throws
        -> ChatAccepted
    {
        try check()
        let isBot =
            botID == Bot.assistantID || botID == Bot.knowledgeID
            || pluginItems.contains { $0.id == botID && $0.state == .enabled && !$0.provides.contains("source") }
        guard isBot else { throw APIError.notFound }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= 2000 else { throw APIError.invalidRequest }
        let current = try await chat(botID: botID)
        if current.contains(where: { $0.status == .pending }) { throw APIError.conflict }
        let now = clock()
        nextID += 2
        let line = ChatMessage(
            id: "chat\(nextID - 1)", botID: botID, author: .me, text: trimmed, status: .ready,
            createdAt: now, updatedAt: now)
        let reply = ChatMessage(
            id: "chat\(nextID)", botID: botID, author: .bot, text: "", status: .pending,
            createdAt: now, updatedAt: now)
        chats[botID] = current + [line, reply]
        return ChatAccepted(message: line, reply: reply)
    }

    public func knowledge() async throws -> KnowledgeOverview {
        try check()
        return library.overview
    }

    public func updateKnowledge(_ patch: KnowledgePatch) async throws -> KnowledgeOverview {
        try check()
        try library.update(patch)
        return library.overview
    }

    public func setReaders(_ readers: [String], area path: String) async throws -> KnowledgeArea {
        try check()
        return try library.setReaders(readers, area: path)
    }

    public func createTopic(name: String, readers: [String]?) async throws -> KnowledgeArea {
        try check()
        return try library.createTopic(name: name, readers: readers)
    }

    public func knowledgeFiles(path: String) async throws -> KnowledgeFiles {
        try check()
        return try library.list(path)
    }

    public func knowledgeFile(path: String) async throws -> KnowledgeFile {
        try check()
        return try library.file(path)
    }

    /// The invented sources have no originals to show.
    public func knowledgeOriginal(path: String) async throws -> Data {
        try check()
        throw APIError.notFound
    }

    public func searchKnowledge(_ query: String) async throws -> [KnowledgeSearchResult] {
        try check()
        return try library.search(query)
    }

    public func uploadKnowledge(path: String, data: Data, contentType: String) async throws -> KnowledgeEntry {
        try check()
        return try library.upload(path: path, size: data.count, now: clock())
    }
}

/// The invented data behind `MockBackend`, relative to `now`.
struct MockScenario {
    var todos: [Todo] = []
    var digests: [Digest] = []
    var runs: [Run] = []
    var plugins: [PluginView] = []
    var cards: [String: [Card]] = [:]
    var reports: [HealthReport] = []
    var settings: RuntimeSettings

    init(now: Date, calendar: Calendar) {
        let today = calendar.startOfDay(for: now)
        func at(_ days: Int, _ hour: Int, _ minute: Int = 0) -> Date {
            let day = calendar.date(byAdding: .day, value: days, to: today) ?? today
            return calendar.date(bySettingHour: hour, minute: minute, second: 0, of: day) ?? day
        }
        func ago(_ minutes: Double) -> Date { now.addingTimeInterval(-minutes * 60) }
        func date(_ days: Int) -> String {
            DueText.format(at(days, 12), "yyyy-MM-dd", calendar)
        }

        let source = { (plugin: String, chat: String, sender: String, sent: Date) in
            TodoSource(
                messageID: "mock-\(plugin)-\(chat)-\(sent.timeIntervalSince1970)", plugin: plugin,
                chatName: chat, sender: sender, sentAt: sent)
        }
        func todo(
            _ id: String, _ title: String, due: Date?, hasTime: Bool, sources: [TodoSource],
            excerpt: String, created: Date, status: TodoStatus = .pending,
            sync: SyncStatus = .synced
        ) -> Todo {
            Todo(
                id: id, title: title, dueAt: due, hasTime: hasTime,
                sourcePlugin: Array(Set(sources.map(\.plugin))).sorted().joined(separator: ","),
                sourceRefs: sources.map(\.messageID), sourceExcerpt: excerpt, confidence: 0.92,
                status: status, reminderID: sync == .synced ? "mock-reminder-\(id)" : nil,
                createdAt: created, updatedAt: created, syncStatus: sync, sources: sources)
        }
        let classGroup = "软件工程2班（示例）"
        let lab = "智能系统实验室（示例）"
        todos = [
            todo(
                "t1", "在学工系统完成学生信息核对", due: at(0, 17), hasTime: true,
                sources: [source("qq_screen", classGroup, "李老师", ago(25))],
                excerpt: "@全体成员 请在今天17:00前完成学生信息核对", created: ago(8)),
            todo(
                "t2", "交数据结构第三次作业", due: at(0, 0), hasTime: false,
                sources: [
                    source("qq_screen", "数据结构课程群（示例）", "助教小周", ago(300)),
                    source("feishu", "课程助教群（示例）", "助教小周", ago(290)),
                ],
                excerpt: "第三次作业：第4章 4.2、4.5、4.9", created: ago(280)),
            todo(
                "t3", "补交寝室卫生评比材料", due: at(-1, 12), hasTime: true,
                sources: [source("qq_screen", "宿舍楼通知群（示例）", "楼长", ago(1500))],
                excerpt: "明天中午12点前务必补交", created: ago(1490), sync: .pending),
            todo(
                "t4", "提交实验室年度材料", due: at(1, 18), hasTime: true,
                sources: [source("feishu", lab, "王老师", ago(40))],
                excerpt: "请每人在明天18:00前提交到飞书文档", created: ago(30)),
            todo(
                "t5", "报名学院篮球赛", due: at(3, 0), hasTime: false,
                sources: [source("qq_screen", classGroup, "体育委员", ago(9))],
                excerpt: "想参加篮球赛的同学周五前找我报名", created: ago(7)),
            todo(
                "t6", "把团员证交给团支书", due: at(0, 12), hasTime: true,
                sources: [source("qq_screen", classGroup, "团支书", ago(200))],
                excerpt: "团员证还没交的同学中午之前交给我", created: ago(190),
                status: .completed),
        ]

        digests = [
            Digest(
                id: "d1", date: date(0), plugin: "qq_screen",
                summary: "班级群：学生信息核对今天 17:00 截止（已建待办）；周五篮球赛报名（已建待办）。\n"
                    + "宿舍楼通知群：下周三停水检修（纯通知）。\n"
                    + "（预览）数据结构课程群：作业提醒，内容被截断，等读到完整消息再判断。",
                createdAt: ago(7)),
            Digest(
                id: "d2", date: date(0), plugin: "feishu",
                summary: "实验室：年度材料明天 18:00 前提交（已建待办）；周会改到周四下午（纯通知）。",
                createdAt: ago(30)),
            Digest(
                id: "d3", date: date(-1), plugin: "qq_screen",
                summary: "宿舍楼通知群：卫生评比材料今天中午前补交（已建待办）。\n班级群：讲座宣传两条（纯通知）。",
                createdAt: ago(1490)),
            Digest(
                id: "d4", date: date(0), plugin: "health",
                summary: "晨间简报已生成：睡眠 7 小时 12 分，恢复良好。",
                createdAt: ago(600)),
            Digest(
                id: "d5", date: date(-2), plugin: "grades",
                summary: "教务系统：本学期选课结果已确认，一共 22 学分。", createdAt: ago(2950)),
            Digest(
                id: "d6", date: date(0), plugin: "grades",
                summary: "教务系统：《离散数学》出成绩了，**88** 分；平均绩点从 3.48 升到 **3.62**。",
                createdAt: ago(58)),
            Digest(
                id: "d7", date: date(-3), plugin: "campus",
                summary: "辅导员：10月20日的病假已批准。", createdAt: ago(4400)),
            Digest(
                id: "d8", date: date(0), plugin: "campus",
                summary: "班级群：学院篮球赛周五前报名（已建待办）。\n学术报告厅：周六 14:00 人工智能讲座，可以加第二课堂学分（纯通知）。",
                createdAt: ago(12)),
            Digest(
                id: "d9", date: date(-1), plugin: "homework",
                summary: "大学物理：实验三的报告下周一交纸质版。", createdAt: ago(1300)),
            Digest(
                id: "d10", date: date(0), plugin: "homework",
                summary: "数据结构课程群：第三次作业今天交（已建待办）。\n离散数学：习题 5 周四上课前交。",
                createdAt: ago(28)),
            Digest(
                id: "d11", date: date(-1), plugin: "token_usage",
                summary: "Claude 余额低于 $15，按最近 7 天的速度还能用 9 天左右。", createdAt: ago(1380)),
            Digest(
                id: "d12", date: date(0), plugin: "token_usage",
                summary: "昨天一共用了 **210k** tokens，比前天多 12%；提炼待办时 Claude 额度用完了一次，自动换成了 Codex。",
                createdAt: ago(4)),
        ]

        func run(
            _ id: String, _ job: String, _ harness: String?, started: Date, seconds: Double,
            _ status: RunStatus, error: String? = nil, attempt: Int = 1
        ) -> Run {
            Run(
                id: id, job: job, harness: harness, startedAt: started,
                finishedAt: started.addingTimeInterval(seconds), status: status, error: error,
                attempt: attempt)
        }
        runs = [
            run("r1", "fetch:qq_screen", nil, started: ago(10), seconds: 35, .succeeded),
            run("r2", "extract_todos", "codex", started: ago(9), seconds: 95, .succeeded, attempt: 2),
            run(
                "r3", "extract_todos", "claude_code", started: ago(9.5), seconds: 20, .failed,
                error: "quota_exhausted"),
            run("r4", "fetch:feishu", nil, started: ago(40), seconds: 4, .succeeded),
            run(
                "r5", "plugin:health:morning_report", "claude_code", started: ago(610),
                seconds: 70, .succeeded),
            run("r6", "fetch:qq_screen", nil, started: ago(190), seconds: 41, .succeeded),
            run(
                "r7", "fetch:qq_screen", nil, started: ago(370), seconds: 120, .failed,
                error: "screen_history_gap"),
            run("r8", "fetch:qq_screen", nil, started: ago(1500), seconds: 30, .succeeded),
            run("r9", "apple_sync", nil, started: ago(7), seconds: 2, .succeeded),
            run("r10", "plugin:grades:sync", nil, started: ago(60), seconds: 12, .succeeded),
            run("r11", "plugin:campus:sync", nil, started: ago(14), seconds: 6, .succeeded),
            run("r12", "plugin:homework:collect", "claude_code", started: ago(30), seconds: 48, .succeeded),
            run("r13", "plugin:token_usage:check", nil, started: ago(5), seconds: 3, .succeeded),
        ]

        let schema = JSONValue.object(["type": .string("object")])
        func plugin(
            _ id: String, _ name: String, _ icon: String, _ description: String,
            _ state: PluginState, sticker: String?, accent: String, platforms: [String],
            provides: [String], schedule: String? = nil, error: String? = nil
        ) -> PluginView {
            PluginView(
                id: id, name: name, version: "0.2.0", icon: icon, description: description,
                platforms: platforms, permissions: [], schedule: schedule, provides: provides,
                configSchema: schema, mascot: PluginMascot(sticker: sticker, accent: accent),
                enabled: state == .enabled || state == .error, config: .object([:]),
                lastRunAt: nil, state: state, error: error)
        }
        plugins = [
            plugin(
                "qq_screen", "QQ 屏幕读取", "message", "读取白名单 QQ 群和联系人的消息；默认只读会话列表",
                .enabled, sticker: "red_ribbon", accent: "#E8383F", platforms: ["macos"],
                provides: ["source", "cards"]),
            plugin(
                "feishu", "飞书", "paperplane", "以你的身份读取白名单会话的历史消息", .enabled,
                sticker: nil, accent: "#22B8CF", platforms: ["linux", "macos"],
                provides: ["source"]),
            plugin(
                "wechat_screen", "微信屏幕读取", "message", "与 QQ 共用截图框架的微信读取", .disabled,
                sticker: nil, accent: "#34C77B", platforms: ["macos"], provides: ["source", "cards"]),
            // Characters with jobs of their own (Design/stickers/characters.json names them).
            plugin(
                "grades", "学分成绩", "graduationcap", "从教务系统读取已修学分、各科成绩和绩点", .enabled,
                sticker: "red_ribbon", accent: "#E8383F", platforms: ["any"], provides: ["cards"],
                schedule: "0 8 * * *"),
            plugin(
                "campus", "请假与学校活动", "calendar", "请假记录，以及报名、讲座、比赛这类学校活动", .enabled,
                sticker: "luosela", accent: "#5B43C9", platforms: ["any"], provides: ["cards"]),
            plugin(
                "homework", "作业", "book", "从课程群里整理作业和截止时间", .enabled,
                sticker: "moning", accent: "#D65F63", platforms: ["any"], provides: ["cards"]),
            plugin(
                "weather", "天气", "cloud.sun", "定时读取天气预报，给出带伞、穿衣、防晒的提醒", .enabled,
                sticker: "aemeath", accent: "#E86A9A", platforms: ["any"], provides: ["mcp_tools", "cards"]),
            plugin(
                "timetable", "课表", "calendar", "截图读取 Chrome 里打开的学校网站个人日程（周历）", .enabled,
                sticker: "moning", accent: "#D65F63", platforms: ["macos"], provides: ["mcp_tools", "cards"]),
            plugin(
                "token_usage", "Token 余额与消耗", "gauge.with.dots.needle.50percent",
                "各个 Agent 的余额和总消耗", .enabled, sticker: "shouanren", accent: "#3E7FD6",
                platforms: ["any"], provides: ["cards"], schedule: "0 * * * *"),
            plugin(
                "health", "健康", "heart", "健康数据汇总、个人基线和每天的晨间简报", .enabled,
                sticker: "lily_veil", accent: "#8068B2", platforms: ["any"],
                provides: ["mcp_tools", "cards"]),
            // 知识库Bot's inputs, still to be set up on the Mac.
            plugin(
                "goodnotes", "GoodNotes 备份", "book.closed", "将 WebDAV 的 PDF 备份安全复制到对应知识库主题。",
                .disabled, sticker: "orange_daisy", accent: "#E57A3C", platforms: ["any"], provides: []),
            plugin(
                "class_photos", "课堂照片", "camera", "按拍摄时间匹配课程，在本机识别 PPT 和板书后导入知识库。",
                .disabled, sticker: "orange_daisy", accent: "#E57A3C", platforms: ["macos"], provides: []),
        ]

        let qqCards: [Card] = [
            Card(
                type: .stat, title: "今天读到的消息",
                data: .object([
                    "value": .number(46), "unit": .string("条"), "label": .string("4 个白名单会话"),
                ])),
            Card(
                type: .list, title: "白名单会话",
                data: .object([
                    "items": .array([
                        .object(["title": .string(classGroup), "detail": .string("10 分钟前")]),
                        .object(["title": .string("数据结构课程群（示例）"), "detail": .string("3 小时前")]),
                        .object(["title": .string("宿舍楼通知群（示例）"), "detail": .string("昨天")]),
                    ])
                ])),
            Card(
                type: .text, title: "读取方式",
                data: .object([
                    "text": .string("仅预览模式：只读会话列表，不点开聊天。预览可能被截断，读到完整消息后会自动替换。")
                ])),
        ]
        // The same set of cards as the health plugin (backend/plugins/health/cards.py).
        let nights = (0..<7).map { DueText.format(at($0 - 6, 12), "M/d", calendar) }
        let stageHours: [(String, [Double])] = [
            ("深睡", [1.3, 1.4, 1.1, 1.5, 1.4, 1.2, 1.4]),
            ("核心", [3.9, 4.1, 3.5, 4.2, 3.9, 3.7, 4.1]),
            ("REM", [1.4, 1.6, 1.3, 1.9, 1.5, 1.4, 1.7]),
            ("清醒", [0.3, 0.4, 0.5, 0.3, 0.3, 0.4, 0.3]),
            // Naps of the same sleep day, stacked on the main sleep as the plugin does.
            ("小睡", [0, 0, 0, 1.2, 0, 0, 0]),
        ]
        var stageSeries: [JSONValue] = []
        for (name, hours) in stageHours {
            var points: [JSONValue] = []
            for (night, value) in zip(nights, hours) {
                points.append(.object(["x": .string(night), "y": .number(value)]))
            }
            stageSeries.append(.object(["name": .string(name), "points": .array(points)]))
        }
        let healthCards: [Card] = [
            Card(
                type: .list, title: "健康预警",
                data: .object([
                    "items": .array([.string("目前没有需要关注的指标。")]), "count": .number(0),
                ])),
            Card(
                type: .stat, title: "今日准备度",
                data: .object([
                    "value": .number(8.2), "unit": .string("/ 10"),
                    "label": .string("夜间 HRV 7.9 · 静息心率 8.3 · 睡眠 9.0 · 训练负荷 7.0"),
                    "delta": .string("昨天 7.6"),
                ])),
            Card(
                type: .stat, title: "昨晚睡眠",
                data: .object([
                    "value": .string("7小时12分"), "label": .string("23:20 入睡 · 06:50 醒来"),
                    "delta": .string("比近 30 天中位数多 18分"), "trend": .string("flat"),
                ])),
            Card(
                type: .chart, title: "最近 7 晚睡眠分期",
                data: .object([
                    "kind": .string("bar"), "unit": .string("小时"), "series": .array(stageSeries),
                ])),
            Card(
                type: .stat, title: "作息规律",
                data: .object([
                    "value": .number(86.5), "unit": .string("SRI"),
                    "label": .string("近 7 天睡眠规律性指数，越高越规律 · 入睡时间波动 ±24 分钟 · 醒来时间波动 ±17 分钟"),
                    "delta": .string("近 30 天 84.1"),
                ])),
            Card(
                type: .stat, title: "静息心率",
                data: .object([
                    "value": .number(58), "unit": .string("次/分"),
                    "label": .string("今天 · 近 30 天中位数 60 次/分"), "delta": .string("-2.0"),
                    "trend": .string("down"),
                ])),
            Card(
                type: .stat, title: "HRV",
                data: .object([
                    "value": .number(60), "unit": .string("毫秒（RMSSD）"),
                    "label": .string("今天 · 近 30 天中位数 56 毫秒 · 夜间测量"), "delta": .string("+7%"),
                    "trend": .string("flat"),
                ])),
            Card(
                type: .stat, title: "训练负荷",
                data: .object([
                    "value": .number(320), "unit": .string("TRIMP（近 7 天）"),
                    "label": .string("近 28 天平均每周 290 · 急慢性负荷比 1.1（仅供参考）"),
                ])),
        ]
        func item(_ title: String, _ subtitle: String? = nil, _ detail: String? = nil) -> JSONValue {
            var fields: [String: JSONValue] = ["title": .string(title)]
            if let subtitle { fields["subtitle"] = .string(subtitle) }
            if let detail { fields["detail"] = .string(detail) }
            return .object(fields)
        }
        func points(_ values: [(String, Double)]) -> JSONValue {
            .array(values.map { .object(["x": .string($0.0), "y": .number($0.1)]) })
        }
        func bar(_ title: String, _ subtitle: String, _ shown: String, _ value: Double, _ max: Double) -> JSONValue {
            .object([
                "title": .string(title), "subtitle": .string(subtitle), "detail": .string("\(shown) / \(Int(max))"),
                "progress": .object(["value": .number(value), "max": .number(max)]),
            ])
        }
        let gradesCards: [Card] = [
            Card(
                type: .list, title: "学业进度",
                data: .object([
                    "style": .string("rings"),
                    "items": .array([
                        bar("平均绩点", "标准 4.0 · 按学分加权 · 上学期 3.48", "3.62", 3.62, 4),
                        bar("综测", "毕业要 60 分，已经够了", "72", 72, 100),
                        bar("体测", "离满分差 22", "78", 78, 100),
                        bar("已修学分", "还差 74", "86", 86, 160),
                    ])
                ])),
            Card(
                type: .list, title: "本学期已出成绩",
                data: .object([
                    "items": .array([
                        item("离散数学", "3 学分 · 必修", "88"), item("大学物理", "4 学分 · 必修", "81"),
                        item("体育（篮球）", "1 学分", "优"),
                    ])
                ])),
            Card(
                type: .chart, title: "各学期绩点",
                data: .object([
                    "kind": .string("bar"),
                    "points": points([("大一上", 3.31), ("大一下", 3.45), ("大二上", 3.48), ("大二下", 3.62)]),
                ])),
        ]
        let campusCards: [Card] = [
            Card(
                type: .list, title: "请假记录",
                data: .object([
                    "items": .array([
                        item("10月28日 事假半天", "回家办证件", "审批中"), item("10月20日 病假 1 天", "辅导员 王老师", "已批准"),
                    ])
                ])),
            Card(
                type: .list, title: "近期活动",
                data: .object([
                    "items": .array([
                        item("学院篮球赛", "周五前报名", "还没报名"), item("人工智能前沿讲座", "周六 14:00 · 学术报告厅", "加二课分"),
                        item("秋季运动会", "11月8日", "通知"),
                    ])
                ])),
            Card(
                type: .stat, title: "第二课堂学分",
                data: .object([
                    "value": .number(3.5), "unit": .string("/ 6"), "label": .string("毕业前还差 2.5 分"),
                ])),
        ]
        let homeworkCards: [Card] = [
            Card(
                type: .stat, title: "这周要交的作业",
                data: .object([
                    "value": .number(3), "unit": .string("项"), "label": .string("最近一项：今天 · 数据结构第三次作业"),
                ])),
            Card(
                type: .list, title: "还没交",
                data: .object([
                    "items": .array([
                        item("数据结构第三次作业", "第 4 章 4.2、4.5、4.9", "今天"), item("离散数学习题 5", "教材 P132 第 1–8 题", "周四"),
                        item("大学物理实验报告", "实验三 · 纸质版", "下周一"),
                    ])
                ])),
            Card(
                type: .chart, title: "最近 4 周的作业",
                data: .object([
                    "kind": .string("bar"), "unit": .string("项"),
                    "points": points([("第 5 周", 4), ("第 6 周", 6), ("第 7 周", 3), ("第 8 周", 5)]),
                ])),
        ]
        let timetableCards: [Card] = [
            Card(
                type: .list, title: "今天的课",
                data: .object([
                    "style": .string("timeline"), "first": .bool(true),
                    "items": .array([
                        .object([
                            "title": .string("大学英语（1）"), "subtitle": .string("08:00–09:35"),
                            "state": .string("done"),
                        ]),
                        .object([
                            "title": .string("高等数学I（A）"), "subtitle": .string("09:55–12:20"),
                            "state": .string("now"),
                        ]),
                        .object([
                            "title": .string("社团例会"), "subtitle": .string("19:00–20:00"),
                            "detail": .string("自己的日程"),
                        ]),
                    ]),
                ])),
            Card(
                type: .list, title: "明天的课 · 推测",
                data: .object([
                    "style": .string("timeline"),
                    "items": .array([
                        .object(["title": .string("大学生职业发展与就业指导（A）"), "subtitle": .string("08:00–09:35")])
                    ])
                ])),
            Card(
                type: .text, title: "这周课表",
                data: .object([
                    "text": .string(
                        "**周一** 09:55 俱乐部体育1\n**周二** 08:00 大学英语（1）；09:55 高等数学I（A）\n"
                            + "**周三** 08:00 大学生职业发展与就业指导（A）\n**周五** 15:55 高等数学I（A）\n\n今天 07:30 读到这周。"),
                    "table": .object([
                        "columns": .array(["", "周一", "周二", "周三", "周四", "周五"].map(JSONValue.string)),
                        "rows": .array(
                            [
                                ["08:00", "", "大学英语（1）", "大学生职业发展与就业指导（A）", "大学英语（1）", ""],
                                ["09:55", "俱乐部体育1", "高等数学I（A）", "", "高等数学I（A）", ""],
                                ["14:00", "写作与沟通", "", "", "", ""],
                                ["15:55", "", "", "", "思想道德与法治", "高等数学I（A）"],
                            ].map { .array($0.map(JSONValue.string)) }),
                        "highlight": .number(4),
                    ]),
                    "footnote": .string("今天 07:30 读到 10月26日–11月1日 那周。"),
                ])),
        ]
        let weatherCards: [Card] = [
            Card(
                type: .text, title: "今日提醒",
                data: .object([
                    "text": .string(
                        "早上好！今天上海多云，9–21°C，不用带伞；早晚凉，穿件外套。\n\n"
                            + "- 08:00 大学英语（1）\n- 17:00 前在学工系统完成学生信息核对")
                ])),
            Card(
                type: .stat, title: "上海今天",
                data: .object([
                    "value": .string("9–21"), "unit": .string("°C"), "label": .string("多云 · 降水概率 10%"),
                    "symbol": .string("cloud.sun.fill"),
                ])),
            Card(
                type: .list, title: "出门提醒",
                data: .object([
                    "items": .array([
                        item("不用带伞", "降水概率 10%"), item("外套或卫衣", "体感 7–20°C，早晚温差大，带件外套"),
                        item("注意防晒", "紫外线指数 7"),
                    ])
                ])),
            Card(
                type: .stat, title: "明天",
                data: .object([
                    "value": .string("12–18"), "unit": .string("°C"), "label": .string("小雨 · 降水概率 70% · 要带伞"),
                    "symbol": .string("cloud.drizzle.fill"),
                ])),
        ]
        let days = (0..<7).map { DueText.format(at($0 - 6, 12), "M/d", calendar) }
        func window(_ name: String, _ resets: String, _ used: Double) -> JSONValue {
            .object([
                "title": .string(name), "subtitle": .string("\(resets) 重置"), "detail": .string("已用 \(Int(used))%"),
                "progress": .object(
                    ["value": .number(used), "max": .number(100)]
                        .merging(used >= 80 ? ["tone": .string(used >= 95 ? "alert" : "warn")] : [:]) { a, _ in a }),
            ])
        }
        let tokenCards: [Card] = [
            Card(
                type: .list, title: "Claude 用量限额 · Max",
                data: .object(["items": .array([window("5 小时", "17:00", 34), window("本周", "周四 08:00", 82)])])),
            Card(
                type: .chart, title: "总共使用的 token",
                data: .object([
                    "kind": .string("donut"), "value": .string("26.5B"), "unit": .string("tokens"),
                    "label": .string("Claude 22.4B · GPT 2.93B · DeepSeek 1.2B"),
                    "points": points([("Claude", 22.4), ("GPT", 2.93), ("DeepSeek", 1.2)]),
                ])),
            Card(
                type: .stat, title: "Claude 余额",
                data: .object([
                    "value": .string("$12.40"), "label": .string("按最近 7 天的用量还能用 9 天左右"),
                    "delta": .string("每天约 -$1.35"), "trend": .string("down"),
                ])),
            Card(
                type: .stat, title: "本月总消耗",
                data: .object([
                    "value": .string("1.82M"), "unit": .string("tokens"),
                    "label": .string("Claude Code 1.21M · Codex 0.61M"), "delta": .string("比上月同期 +14%"),
                    "trend": .string("up"),
                ])),
            Card(
                type: .chart, title: "最近 7 天消耗",
                data: .object([
                    "kind": .string("line"), "unit": .string("千 tokens"),
                    "points": points(Array(zip(days, [150.0, 172, 138, 190, 168, 188, 210]))),
                ])),
        ]
        cards = [
            "qq_screen": qqCards, "health": healthCards, "grades": gradesCards, "campus": campusCards,
            "homework": homeworkCards, "token_usage": tokenCards, "timetable": timetableCards,
            "weather": weatherCards,
        ]

        reports = [
            HealthReport(
                id: "h1", date: date(0), kind: .morning, status: .ready,
                title: "睡得不错，今天可以正常训练",
                body: "昨晚睡了 **7小时12分**，比 7 天基线多 18 分钟，深睡 1小时24分。\n\n"
                    + "- 综合准备度 8.2 分\n- 静息心率 58 次/分，比基线低 2\n- HRV（RMSSD）60 毫秒，在正常范围\n\n"
                    + "建议：今天可以按计划进行中等强度训练。",
                harness: "claude_code", createdAt: ago(600), updatedAt: ago(600)),
            HealthReport(
                id: "h2", date: date(-1), kind: .morning, status: .failed,
                error: "report_not_written", createdAt: ago(2040), updatedAt: ago(2040)),
            HealthReport(
                id: "h3", date: date(-4), kind: .weekly, status: .ready,
                title: "这周睡眠规律，训练量适中",
                body: "本周平均睡眠 **7小时03分**，入睡时间波动 25 分钟。\n\n"
                    + "- 训练 3 次，总时长 2小时40分\n- 静息心率稳定\n\n下周建议保持现在的作息。",
                harness: "codex", createdAt: ago(5760), updatedAt: ago(5760)),
        ]

        settings = RuntimeSettings(
            timezone: "Asia/Shanghai", sourceCron: "*/30 * * * *",
            extractionCron: "0 7-23/3 * * *", confidenceThreshold: 0.7,
            harnessOrder: ["claude_code", "codex"], jobHarness: [:], agentTimeout: 900,
            retryAttempts: 3, retryBaseSeconds: 2)
    }
}
