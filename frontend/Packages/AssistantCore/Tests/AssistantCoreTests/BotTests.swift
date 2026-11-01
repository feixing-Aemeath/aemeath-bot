import XCTest

@testable import AssistantCore

final class BotTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    let now = APIDateCoding.date(from: "2026-10-15T21:00:00+08:00")!

    func at(_ text: String) -> Date { APIDateCoding.date(from: text)! }

    func plugin(
        _ id: String, _ state: PluginState, sticker: String? = nil, provides: [String] = ["cards"]
    ) -> PluginView {
        PluginView(
            id: id, name: "插件\(id)", version: "1", icon: "message", description: "",
            platforms: ["any"], permissions: [], schedule: nil, provides: provides,
            configSchema: .object([:]), mascot: PluginMascot(sticker: sticker, accent: "#E8383F"),
            enabled: state == .enabled, config: .object([:]), lastRunAt: nil, state: state, error: nil)
    }

    func run(
        _ id: String, _ job: String, _ status: RunStatus, started: String, attempt: Int = 1,
        error: String? = nil
    ) -> Run {
        let start = at(started)
        return Run(
            id: id, job: job, harness: "claude_code", startedAt: start,
            finishedAt: status == .running ? nil : start.addingTimeInterval(90), status: status,
            error: error, attempt: attempt)
    }

    func digest(_ id: String, _ plugin: String, _ date: String, _ summary: String, at time: String) -> Digest {
        Digest(id: id, date: date, plugin: plugin, summary: summary, createdAt: at(time))
    }

    func testTheAssistantThenCharactersThenSources() {
        let catalog = StickerCatalog(
            names: ["aemeath", "lily_veil", "red_ribbon"],
            roles: ["assistant": PluginMascot(sticker: "aemeath", accent: "#E86A9A")],
            characters: ["aemeath": "助理Bot", "lily_veil": "健康Bot", "red_ribbon": "成绩Bot"])
        let plugins = [
            plugin("wechat_screen", .disabled, provides: ["source", "cards"]),
            // A source keeps its plugin name even though its manifest still has a sticker.
            plugin("qq_screen", .enabled, sticker: "red_ribbon", provides: ["source", "cards"]),
            plugin("homework", .disabled),
            plugin("grades", .error, sticker: "red_ribbon"),
            plugin("health", .enabled, sticker: "lily_veil", provides: ["mcp_tools", "cards"]),
        ]
        let bots = BotList.build(plugins: plugins, catalog: catalog)
        XCTAssertEqual(
            bots.map(\.id), ["assistant", "grades", "health", "homework", "knowledge", "qq_screen", "wechat_screen"])
        XCTAssertEqual(
            bots.map(\.kind), [.assistant, .character, .character, .character, .knowledge, .source, .source])
        // Characters are named after their sticker when the stickers folder names it.
        XCTAssertEqual(
            bots.map(\.name),
            ["助理Bot", "成绩Bot", "健康Bot", "插件homework", "知识库Bot", "插件qq_screen", "插件wechat_screen"])
        XCTAssertEqual(
            bots.map(\.role), ["个人助理", "插件grades", "插件health", "插件homework", "知识库", "消息来源", "消息来源"])
        XCTAssertTrue(bots[0].isAssistant)
        XCTAssertEqual(bots[0].look, catalog.assistant)
        XCTAssertEqual(bots[5].icon, "message")
        // Without names for the App's stickers, the App's own Bots are still 助理Bot and 知识库Bot.
        XCTAssertEqual(
            BotList.build(plugins: [], catalog: StickerCatalog(names: [])).map(\.name), ["助理Bot", "知识库Bot"])
    }

    func testGoodNotesPhotosAndVideosJoin知识库Bot() {
        let catalog = StickerCatalog(
            names: ["aemeath", "orange_daisy"],
            roles: [
                "assistant": PluginMascot(sticker: "aemeath", accent: "#E86A9A"),
                "knowledge": PluginMascot(sticker: "orange_daisy", accent: "#E57A3C"),
            ],
            characters: ["aemeath": "助理Bot", "orange_daisy": "知识库Bot"])
        let plugins = [
            plugin("video_notes", .enabled, sticker: "orange_daisy"),
            plugin("goodnotes", .disabled, sticker: "orange_daisy"),
            plugin("class_photos", .disabled, sticker: "orange_daisy"),
            plugin("feishu", .enabled, provides: ["source"]),
        ]
        let bots = BotList.build(plugins: plugins, catalog: catalog)
        XCTAssertEqual(bots.map(\.id), ["assistant", "knowledge", "feishu"])
        let shelf = bots[1]
        XCTAssertEqual(shelf.name, "知识库Bot")
        XCTAssertTrue(shelf.isBuiltIn)
        XCTAssertEqual(shelf.look.sticker, "orange_daisy")
        // Her page shows only the joined plugins' cards; she has no plugin of her own.
        XCTAssertEqual(shelf.joined, ["video_notes", "goodnotes", "class_photos"])
        XCTAssertEqual(shelf.pluginIDs, ["video_notes", "goodnotes", "class_photos"])
        XCTAssertEqual(shelf.intro, "“知识库”与“课堂笔记”")
        XCTAssertEqual(shelf.tag, "知识库 · 课堂笔记")
    }

    func testPluginsOfOneCharacterAreOneBot() {
        let catalog = StickerCatalog(names: ["moning"], characters: ["moning": "作业Bot"])
        var homework = plugin("homework", .enabled, sticker: "moning")
        homework.name = "作业"
        var timetable = plugin("timetable", .enabled, sticker: "moning")
        timetable.name = "课表"
        let bots = BotList.build(
            plugins: [homework, timetable, plugin("feishu", .enabled, provides: ["source"])], catalog: catalog)
        XCTAssertEqual(bots.map(\.id), ["assistant", "homework", "knowledge", "feishu"])
        let moning = bots[1]
        XCTAssertEqual(moning.name, "作业Bot")
        XCTAssertEqual(moning.joined, ["timetable"])
        XCTAssertEqual(moning.pluginIDs, ["homework", "timetable"])
        XCTAssertEqual(moning.role, "作业 · 课表")
        XCTAssertEqual(moning.intro, "“作业”与“课表”")
        XCTAssertEqual(bots[0].pluginIDs, [])
        // A sticker the App does not have is the placeholder, not a shared character.
        // A plugin with the assistant's own sticker (天气) is part of 助理Bot.
        let withAssistant = StickerCatalog(
            names: ["aemeath", "moning"],
            roles: ["assistant": PluginMascot(sticker: "aemeath", accent: "#E86A9A")],
            characters: ["aemeath": "助理Bot", "moning": "作业Bot"])
        let mine = BotList.build(plugins: [plugin("weather", .enabled, sticker: "aemeath"), homework], catalog: withAssistant)
        XCTAssertEqual(mine.map(\.id), ["assistant", "homework", "knowledge"])
        XCTAssertEqual(mine[0].pluginIDs, ["weather"])
        XCTAssertEqual(mine[0].intro, "“个人助理”")
        let unknown = BotList.build(
            plugins: [plugin("a", .enabled, sticker: "ghost"), plugin("b", .enabled, sticker: "ghost")],
            catalog: catalog)
        XCTAssertEqual(unknown.map(\.id), ["assistant", "a", "b", "knowledge"])
    }

    func testAPluginSaysWhatItReadEachDay() {
        let digests = [
            digest("1", "qq_screen", "2026-10-15", "班级群：交表（已建待办）\n\n宿舍群：停水通知", at: "2026-10-15T20:10:00+08:00"),
            digest("2", "qq_screen", "2026-10-15", "课程群：作业", at: "2026-10-15T14:00:00+08:00"),
            digest("3", "qq_screen", "2026-10-14", "讲座宣传", at: "2026-10-14T22:00:00+08:00"),
            digest("4", "feishu", "2026-10-15", "实验室：交材料", at: "2026-10-15T20:00:00+08:00"),
        ]
        let todos = [
            Todo(
                id: "a", title: "交表", sourcePlugin: "qq_screen", status: .pending,
                createdAt: at("2026-10-15T20:06:00+08:00"), updatedAt: at("2026-10-15T20:06:00+08:00")),
        ]
        let bot = Bot(id: "qq_screen", name: "QQ", look: StickerLook(sticker: "red_ribbon"))
        let messages = BotTimeline.messages(
            for: bot, today: nil, digests: digests, todos: todos, runs: [], reports: [], now: now,
            calendar: calendar)
        XCTAssertEqual(messages.map(\.id), ["digest-qq_screen-2026-10-14", "digest-qq_screen-2026-10-15"])
        // A day is as new as its newest digest, and carries the todos extracted that day.
        XCTAssertEqual(messages[1].date, at("2026-10-15T20:10:00+08:00"))
        XCTAssertEqual(messages[1].text, "班级群：交表（已建待办）\n宿舍群：停水通知\n课程群：作业")
        XCTAssertEqual(messages[1].todos.map(\.id), ["a"])
        XCTAssertTrue(messages[0].todos.isEmpty)
        XCTAssertEqual(messages[1].preview, "班级群：交表（已建待办）")
    }

    func testTheHealthBotAlsoSaysItsReports() {
        let reports = [
            HealthReport(
                id: "m", date: "2026-10-15", kind: .morning, status: .ready, title: "睡得不错",
                body: "睡了 **7小时**", createdAt: at("2026-10-15T08:00:00+08:00"),
                updatedAt: at("2026-10-15T08:01:00+08:00")),
            HealthReport(
                id: "f", date: "2026-10-14", kind: .morning, status: .failed, error: "report_not_written",
                createdAt: at("2026-10-14T08:00:00+08:00"), updatedAt: at("2026-10-14T08:05:00+08:00")),
            HealthReport(
                id: "w", date: "2026-10-11", kind: .weekly, status: .pending,
                createdAt: at("2026-10-11T21:00:00+08:00"), updatedAt: at("2026-10-11T21:00:00+08:00")),
        ]
        let health = Bot(id: "health", name: "健康Bot", look: StickerLook(sticker: "lily_veil"))
        let messages = BotTimeline.messages(
            for: health, today: nil, digests: [], todos: [], runs: [], reports: reports, now: now,
            calendar: calendar)
        XCTAssertEqual(messages.map(\.id), ["report-w", "report-f", "report-m"])
        XCTAssertEqual(messages.map(\.title), ["周报", "晨间简报", "晨间简报 · 睡得不错"])
        XCTAssertEqual(messages[0].text, "正在写…")
        XCTAssertTrue(messages[1].failed)
        XCTAssertTrue(messages[1].text.hasPrefix("没有生成："))
        XCTAssertEqual(messages[2].text, "睡了 **7小时**")
        XCTAssertEqual(messages[2].preview, "晨间简报 · 睡得不错")
        // Other plugins do not show health reports.
        let qq = Bot(id: "qq_screen", name: "QQ", look: StickerLook(sticker: nil))
        XCTAssertTrue(
            BotTimeline.messages(
                for: qq, today: nil, digests: [], todos: [], runs: [], reports: reports, now: now,
                calendar: calendar
            ).isEmpty)
    }

    func testTheAssistantSaysTodaysRunsAndSummary() {
        let runs = [
            run("1", "extract_todos", .succeeded, started: "2026-10-15T10:00:00+08:00"),
            run("2", "extract_todos", .failed, started: "2026-10-15T13:00:00+08:00", attempt: 2, error: "quota_exhausted"),
            run("3", "extract_todos", .running, started: "2026-10-15T20:58:00+08:00"),
            run("4", "extract_todos", .succeeded, started: "2026-10-14T22:00:00+08:00"),  // yesterday
            run("5", "apple_sync", .succeeded, started: "2026-10-15T10:02:00+08:00"),  // not extraction
        ]
        let digests = [digest("1", "qq_screen", "2026-10-15", "班级群", at: "2026-10-15T19:00:00+08:00")]
        let today = Today(date: "2026-10-15", summary: "班级群有 2 条截止通知", todos: [], cards: [])
        let assistant = Bot(
            id: Bot.assistantID, name: "助理Bot", kind: .assistant, look: StickerLook(sticker: nil))
        // Typed at 20:00, before the run that is still going.
        let typed = at("2026-10-15T20:00:00+08:00")
        let chat = BotChat.messages([
            ChatMessage(
                id: "c1", botID: Bot.assistantID, author: .me, text: "在吗", status: .ready,
                createdAt: typed, updatedAt: typed)
        ])
        let messages = BotTimeline.messages(
            for: assistant, today: today, digests: digests, todos: [], runs: runs, reports: [],
            chat: chat, now: now, calendar: calendar)
        XCTAssertEqual(messages.map(\.id), ["run-1", "run-2", "summary-2026-10-15", "c1", "run-3"])
        XCTAssertEqual(messages[0].text, "提炼完一次待办 · Claude Code · 用时 1 分 30 秒")
        XCTAssertTrue(messages[1].failed)
        XCTAssertTrue(messages[1].text.contains("（第 2 次）"))
        XCTAssertEqual(messages[2].title, "今天的总结")
        XCTAssertEqual(messages[2].date, at("2026-10-15T19:00:00+08:00"))
        XCTAssertTrue(messages[4].text.hasPrefix("正在提炼待办"))
    }

    func testTheChatStartsWithTheLastTwoDaysThatHaveMessages() {
        let messages = [
            BotMessage(id: "1", author: .bot, date: at("2026-10-24T08:00:00+08:00"), text: "a"),
            BotMessage(id: "2", author: .bot, date: at("2026-10-25T08:00:00+08:00"), text: "b"),
            BotMessage(id: "3", author: .me, date: at("2026-10-27T23:50:00+08:00"), text: "c"),
            BotMessage(id: "4", author: .bot, date: at("2026-10-28T00:10:00+08:00"), text: "d"),
            BotMessage(id: "5", author: .bot, date: at("2026-10-28T09:00:00+08:00"), text: "e"),
        ]
        let recent = BotTimeline.recent(messages, days: 2, calendar: calendar)
        // Days without messages (10/26) do not count; the 27th and the 28th are shown.
        XCTAssertEqual(recent.messages.map(\.id), ["3", "4", "5"])
        XCTAssertEqual(recent.hiddenDays, 2)
        XCTAssertEqual(BotTimeline.recent(messages, days: 5, calendar: calendar).messages.count, 5)
        XCTAssertEqual(BotTimeline.recent(messages, days: 5, calendar: calendar).hiddenDays, 0)
        XCTAssertEqual(BotTimeline.recent([], days: 2, calendar: calendar).hiddenDays, 0)
    }

    func testUnreadCountsOnlyWhatTheBotSaidSinceThePageWasOpen() {
        let messages = [
            BotMessage(id: "1", author: .bot, date: at("2026-10-15T08:00:00+08:00"), text: "早"),
            BotMessage(id: "2", author: .me, date: at("2026-10-15T09:00:00+08:00"), text: "在吗"),
            BotMessage(id: "3", author: .app, date: at("2026-10-15T09:00:00+08:00"), text: "提示"),
            BotMessage(id: "4", author: .bot, date: at("2026-10-15T20:00:00+08:00"), text: "晚"),
        ]
        let launch = at("2026-10-01T00:00:00+08:00")
        XCTAssertEqual(BotTimeline.unread(messages, seen: at("2026-10-15T07:00:00+08:00"), firstLaunch: launch), 2)
        XCTAssertEqual(BotTimeline.unread(messages, seen: at("2026-10-15T12:00:00+08:00"), firstLaunch: launch), 1)
        // Never opened: only what came after the App first started.
        XCTAssertEqual(BotTimeline.unread(messages, seen: nil, firstLaunch: at("2026-10-15T10:00:00+08:00")), 1)
        XCTAssertEqual(BotTimeline.unread(messages, seen: nil, firstLaunch: now), 0)
    }

    func testDayDividers() {
        XCTAssertEqual(BotTimeline.day(at("2026-10-15T08:00:00+08:00"), now: now, calendar: calendar), "今天")
        XCTAssertEqual(BotTimeline.day(at("2026-10-14T23:00:00+08:00"), now: now, calendar: calendar), "昨天")
        XCTAssertEqual(BotTimeline.day(at("2026-10-09T12:00:00+08:00"), now: now, calendar: calendar), "10月9日")
    }

    func testChatLinesBecomeBubbles() {
        func line(_ id: String, _ author: ChatAuthor, _ status: ChatStatus, _ text: String = "", error: String? = nil)
            -> ChatMessage
        {
            ChatMessage(
                id: id, botID: "homework", author: author, text: text, status: status, error: error,
                createdAt: at("2026-10-15T20:00:00+08:00"), updatedAt: at("2026-10-15T20:00:30+08:00"))
        }
        let chat = [
            line("1", .me, .ready, "我还有什么作业"), line("2", .bot, .ready, "还有 **2 项**"),
            line("3", .me, .ready, "在吗"), line("4", .bot, .failed, error: "all_harnesses_failed"),
            line("5", .me, .ready, "在吗"), line("6", .bot, .pending),
        ]
        let bubbles = BotChat.messages(chat)
        XCTAssertEqual(bubbles.map(\.id), ["1", "2", "3", "4", "5"])  // the pending reply is the typing bubble
        XCTAssertEqual(bubbles.map(\.author), [.me, .bot, .me, .app, .me])
        XCTAssertEqual(bubbles[0].date, at("2026-10-15T20:00:00+08:00"))
        XCTAssertEqual(bubbles[1].date, at("2026-10-15T20:00:30+08:00"))  // when it was written
        XCTAssertTrue(bubbles[3].failed)
        XCTAssertTrue(bubbles[3].text.contains("还没登录"))
        XCTAssertTrue(BotChat.isReplying(chat))
        XCTAssertFalse(BotChat.isReplying(Array(chat.prefix(5))))
    }

    func testMockBotsAnswerAfterADelay() async throws {
        final class Clock: @unchecked Sendable { var now = Date(timeIntervalSince1970: 1_800_000_000) }
        let clock = Clock()
        let backend = MockBackend(now: clock.now, clock: { clock.now })
        let accepted = try await backend.sendChat(botID: "homework", text: " 在吗 ", botName: "作业Bot", choice: .automatic)
        XCTAssertEqual(accepted.message.text, "在吗")
        XCTAssertEqual(accepted.reply.status, .pending)
        do {
            _ = try await backend.sendChat(botID: "homework", text: "还在吗", botName: nil, choice: .automatic)
            XCTFail("one reply at a time")
        } catch APIError.conflict {}
        do {
            _ = try await backend.sendChat(botID: "qq_screen", text: "在吗", botName: nil, choice: .automatic)
            XCTFail("a message source does not chat")
        } catch APIError.notFound {}
        clock.now += MockBackend.replyDelay
        let chat = try await backend.chat(botID: "homework")
        XCTAssertEqual(chat.map(\.status), [.ready, .ready])
        XCTAssertEqual(chat[1].text, MockBackend.replyText)
    }

    func testIntrosNameWhatEachBotLooksAfter() {
        let look = StickerLook(sticker: nil)
        XCTAssertEqual(Bot(id: Bot.assistantID, name: "助理Bot", kind: .assistant, look: look).intro, "“个人助理”")
        XCTAssertEqual(Bot(id: "campus", name: "校园Bot", role: "请假与学校活动", look: look).intro, "“请假”与“学校活动”")
        XCTAssertEqual(Bot(id: "finance", name: "记账Bot", role: "记账", look: look).intro, "“记账”与“预算”")
        // A plugin the App does not know yet: its name; a new message source: 消息来源.
        XCTAssertEqual(Bot(id: "photos", name: "照片", role: "照片分类", look: look).intro, "照片分类")
        XCTAssertEqual(Bot(id: "sms", name: "短信", role: "消息来源", kind: .source, look: look).intro, "消息来源")
        // The tag on a Bot's page has one format for every Bot.
        XCTAssertEqual(Bot(id: "grades", name: "成绩Bot", role: "学分成绩", look: look).tag, "学分 · 成绩")
        XCTAssertEqual(Bot(id: Bot.assistantID, name: "助理Bot", kind: .assistant, look: look).tag, "个人助理")
        var moning = Bot(id: "homework", name: "作业Bot", role: "作业 · 课表", look: look)
        moning.joined = ["timetable"]
        XCTAssertEqual(moning.tag, "作业 · 课表")
        XCTAssertEqual(Bot(id: "photos", name: "照片", role: "照片分类", look: look).tag, "照片分类")
    }

    func testCatalogReadsCharacterNames() throws {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("stickers-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try Data([0]).write(to: folder.appendingPathComponent("cat.png"))
        try Data(#"{"cat": "小猫Bot"}"#.utf8).write(to: folder.appendingPathComponent("characters.json"))
        XCTAssertEqual(StickerCatalog.load(folder: folder).characters, ["cat": "小猫Bot"])
        // The repository's own: every named character has its sticker, and the assistant is 助理Bot.
        let repository = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Design/stickers")
        let catalog = StickerCatalog.load(folder: repository)
        XCTAssertEqual(
            catalog.characters,
            [
                "aemeath": "助理Bot", "red_ribbon": "成绩Bot", "luosela": "校园Bot",
                "moning": "作业Bot", "shouanren": "用量Bot", "lily_veil": "健康Bot",
                "orange_daisy": "知识库Bot", "carlotta": "记账Bot",
            ])
        for sticker in catalog.characters.keys {
            XCTAssertTrue(catalog.contains(sticker), sticker)
        }
        XCTAssertEqual(BotList.build(plugins: [], catalog: catalog).first?.name, "助理Bot")
        XCTAssertEqual(catalog.look(role: Bot.knowledgeID).sticker, "orange_daisy")
    }
}
