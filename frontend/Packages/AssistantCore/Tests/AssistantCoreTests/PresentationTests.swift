import XCTest

@testable import AssistantCore

final class PresentationTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    let now = APIDateCoding.date(from: "2026-10-15T21:00:00+08:00")!

    func at(_ text: String) -> Date { APIDateCoding.date(from: text)! }

    func run(
        _ job: String, _ status: RunStatus, started: String, seconds: Double? = 30,
        harness: String? = nil, error: String? = nil
    ) -> Run {
        let start = at(started)
        return Run(
            id: UUID().uuidString, job: job, harness: harness, startedAt: start,
            finishedAt: seconds.map { start.addingTimeInterval($0) }, status: status, error: error,
            attempt: 1)
    }

    func todo(
        _ id: String, plugin: String, created: String, sources: [TodoSource]? = nil,
        status: TodoStatus = .pending
    ) -> Todo {
        Todo(
            id: id, title: "待办\(id)", sourcePlugin: plugin, status: status,
            createdAt: at(created), updatedAt: at(created), sources: sources)
    }

    func plugin(_ id: String, _ state: PluginState, error: String? = nil) -> PluginView {
        PluginView(
            id: id, name: id, version: "1", icon: "message", description: "", platforms: ["any"],
            permissions: [], schedule: nil, provides: ["source"], configSchema: .object([:]),
            mascot: nil, enabled: state == .enabled, config: .object([:]), lastRunAt: nil,
            state: state, error: error)
    }

    func testPluginConfigEditKeepsTheRestOfTheConfiguration() {
        var qq = plugin("qq_screen", .enabled)
        qq.configSchema = .object([
            "properties": .object(["whitelist": .object([:]), "preview_only": .object([:]), "helper": .object([:])])
        ])
        qq.config = .object(["helper": .string("/x/screen-helper"), "whitelist": .array([.string("班级群")])])
        XCTAssertEqual(PluginConfigEdit.lists(of: qq).map(\.key), ["whitelist"])
        XCTAssertTrue(PluginConfigEdit.hasPreviewMode(qq))
        XCTAssertTrue(PluginConfigEdit.previewOnly(qq))  // not set: the schema's default
        XCTAssertEqual(PluginConfigEdit.names(qq, "whitelist"), ["班级群"])
        let names = PluginConfigEdit.adding("  软件工程2班 ", to: PluginConfigEdit.names(qq, "whitelist"))
        XCTAssertEqual(names, ["班级群", "软件工程2班"])
        XCTAssertEqual(PluginConfigEdit.adding("班级群", to: names), names)
        XCTAssertEqual(PluginConfigEdit.adding("   ", to: names), names)
        let config = PluginConfigEdit.setting(qq, "whitelist", to: .array(names.map(JSONValue.string)))
        XCTAssertEqual(config["helper"]?.stringValue, "/x/screen-helper")
        XCTAssertEqual(config["whitelist"]?.arrayValue?.count, 2)
        // The homework plugin's course groups; no preview mode.
        var homework = plugin("homework", .enabled)
        homework.configSchema = .object(["properties": .object(["course_chats": .object([:])])])
        XCTAssertEqual(PluginConfigEdit.lists(of: homework).map(\.title), ["课程群"])
        XCTAssertFalse(PluginConfigEdit.hasPreviewMode(homework))
    }

    func testNames() {
        XCTAssertEqual(Names.plugins("qq_screen,feishu"), "QQ · 飞书")
        XCTAssertEqual(Names.plugin("future_plugin"), "future_plugin")
        XCTAssertEqual(Names.harness("claude_code"), "Claude Code")
        XCTAssertEqual(Names.harness("codex"), "Codex")
        XCTAssertEqual(Names.harness(nil), "—")
        XCTAssertEqual(Names.job("extract_todos"), "提炼待办")
        XCTAssertEqual(Names.job("fetch:qq_screen"), "读取QQ")
        XCTAssertEqual(Names.job("plugin:health:morning_report"), "健康 · 晨间简报")
        XCTAssertEqual(Names.job("plugin:health:something_new"), "健康 · something_new")
        XCTAssertTrue(Names.job("plugin:token_usage:limits").hasSuffix(" · 读取用量限额"))
        XCTAssertEqual(Names.job("daily_notes"), "整理每日文件夹")
        XCTAssertEqual(Names.job("plugin:timetable:read"), "课表 · 读取")
        XCTAssertEqual(Names.job("unknown_job"), "unknown_job")
        XCTAssertEqual(Names.job("chat:assistant"), "回复对话")
        XCTAssertEqual(Names.job("chat:homework"), "回复对话 · 作业")
        XCTAssertEqual(Names.plugins("chat"), "对话")
    }

    func testRelativeTimes() {
        XCTAssertEqual(RelativeTime.describe(now.addingTimeInterval(-20), now: now, calendar: calendar), "刚刚")
        XCTAssertEqual(RelativeTime.describe(now.addingTimeInterval(-600), now: now, calendar: calendar), "10 分钟前")
        XCTAssertEqual(RelativeTime.describe(at("2026-10-15T18:30:00+08:00"), now: now, calendar: calendar), "2 小时前")
        XCTAssertEqual(RelativeTime.describe(at("2026-10-14T21:05:00+08:00"), now: now, calendar: calendar), "昨天 21:05")
        XCTAssertEqual(RelativeTime.describe(at("2026-10-12T08:30:00+08:00"), now: now, calendar: calendar), "10月12日 08:30")
        XCTAssertEqual(RelativeTime.duration(45), "45 秒")
        XCTAssertEqual(RelativeTime.duration(125), "2 分 05 秒")
        XCTAssertEqual(RelativeTime.duration(3780), "1 小时 3 分")
    }

    func testTodoSourcesNameTheChatAndSender() throws {
        let decoded = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todoWithSources.utf8))
        XCTAssertEqual(decoded.sources?.first?.chatName, "软件工程2班")
        XCTAssertEqual(decoded.sources?.first?.sender, "李老师")
        XCTAssertEqual(TodoSourceText.describe(decoded), "QQ · 软件工程2班 · 李老师")
        // Older todos (and todos inside cards) have no sources: plugin names only.
        let plain = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todoWithoutDue.utf8))
        XCTAssertNil(plain.sources)
        XCTAssertEqual(TodoSourceText.describe(plain), "QQ · 飞书")
        // Several chats: the first one, and how many places in total.
        let sent = at("2026-10-15T20:00:00+08:00")
        let merged = todo(
            "m", plugin: "feishu,qq_screen", created: "2026-10-15T20:00:00+08:00",
            sources: [
                TodoSource(messageID: "a", plugin: "qq_screen", chatName: "班级群", sender: "李老师", sentAt: sent),
                TodoSource(messageID: "b", plugin: "feishu", chatName: "实验室", sender: "王老师", sentAt: sent),
                TodoSource(messageID: "c", plugin: "qq_screen", chatName: "班级群", sender: "班长", sentAt: sent),
            ])
        XCTAssertEqual(TodoSourceText.describe(merged), "QQ · 班级群 · 李老师 等 2 处")
        // A private chat: the sender is the chat.
        let direct = todo(
            "d", plugin: "qq_screen", created: "2026-10-15T20:00:00+08:00",
            sources: [TodoSource(messageID: "a", plugin: "qq_screen", chatName: "室友", sender: "室友", sentAt: sent)])
        XCTAssertEqual(TodoSourceText.describe(direct), "QQ · 室友")
    }

    func testSyncState() throws {
        var todo = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todo.utf8))
        XCTAssertEqual(SyncState(todo), .pending)
        todo.syncStatus = .synced
        XCTAssertEqual(SyncState(todo), .synced)
        XCTAssertEqual(SyncState(todo).text, "已同步到提醒事项")
        todo.syncError = "apple_helper_failed"
        XCTAssertEqual(SyncState(todo), .failed("apple_helper_failed"))
        // The watchdog's notes stay in the App.
        todo.sourcePlugin = "watchdog"
        XCTAssertEqual(SyncState(todo), .appOnly)
        XCTAssertEqual(SyncState(todo).text, "只在 App 里，不进提醒事项")
    }

    func testRunTexts() {
        let failed = run("extract_todos", .failed, started: "2026-10-15T20:50:00+08:00", seconds: 20, error: "quota_exhausted")
        XCTAssertEqual(RunText.status(failed.status), "失败")
        XCTAssertEqual(RunText.error(failed.error), "额度用完")
        XCTAssertEqual(RunText.error("something_new"), "something_new")
        XCTAssertNil(RunText.error(nil))
        XCTAssertEqual(RunText.duration(failed, now: now), "20 秒")
        let running = run("extract_todos", .running, started: "2026-10-15T20:58:00+08:00", seconds: nil)
        XCTAssertEqual(RunText.duration(running, now: now), "2 分 00 秒")
    }

    func testPluginStatusLine() {
        let runs = [
            run("fetch:qq_screen", .succeeded, started: "2026-10-15T20:50:00+08:00"),
            run("extract_todos", .succeeded, started: "2026-10-15T20:51:00+08:00"),
            run("fetch:feishu", .failed, started: "2026-10-15T18:00:00+08:00", error: "feishu_history_failed"),
        ]
        let todos = [
            todo("1", plugin: "qq_screen", created: "2026-10-15T20:52:00+08:00"),
            todo("2", plugin: "feishu,qq_screen", created: "2026-10-15T20:53:00+08:00"),
            todo("3", plugin: "qq_screen", created: "2026-10-15T20:54:00+08:00", status: .deleted),
            todo("4", plugin: "qq_screen", created: "2026-10-15T19:00:00+08:00"),  // before the run
            todo("5", plugin: "feishu", created: "2026-10-15T20:55:00+08:00"),
        ]
        func line(_ plugin: PluginView) -> String {
            PluginStatusLine.describe(plugin, runs: runs, todos: todos, now: now, calendar: calendar)
        }
        XCTAssertEqual(line(plugin("qq_screen", .enabled)), "上次运行在 9 分钟前，提取了 2 条待办")
        XCTAssertEqual(line(plugin("feishu", .enabled)), "上次运行在 2 小时前，读取飞书消息失败")
        XCTAssertEqual(line(plugin("wechat_screen", .enabled)), "还没有运行过")
        XCTAssertEqual(line(plugin("wechat_screen", .disabled)), "已停用")
        XCTAssertEqual(line(plugin("photos", .unsupported)), "这台电脑上不能运行")
        XCTAssertEqual(line(plugin("feishu", .error, error: "plugin_setup_failed")), "出错：插件启动失败")
        let busy = [run("plugin:health:morning_report", .running, started: "2026-10-15T20:58:15+08:00", seconds: nil)]
        XCTAssertEqual(
            PluginStatusLine.describe(plugin("health", .enabled), runs: busy, todos: [], now: now, calendar: calendar),
            "正在运行，已经 1 分 45 秒")
        // A plugin that is not a message source has no todos extracted from it.
        var grades = plugin("grades", .enabled)
        grades.provides = ["cards"]
        let synced = [run("plugin:grades:sync", .succeeded, started: "2026-10-15T20:00:00+08:00")]
        XCTAssertEqual(
            PluginStatusLine.describe(grades, runs: synced, todos: todos, now: now, calendar: calendar),
            "上次运行在 59 分钟前")
    }

    func testKnowledgeStatusLine() {
        func line(_ inputs: [PluginView], _ runs: [Run] = []) -> String {
            KnowledgeStatusLine.describe(inputs: inputs, runs: runs, now: now, calendar: calendar)
        }
        let goodnotes = plugin("goodnotes", .enabled), video = plugin("video_notes", .enabled)
        let photos = plugin("class_photos", .disabled)
        XCTAssertEqual(line([goodnotes, photos, video]), "资料来源：GoodNotes、视频")
        XCTAssertEqual(line([plugin("goodnotes", .disabled), photos]), "资料来源都还没打开")
        XCTAssertEqual(line([]), "整理知识库里的资料")
        XCTAssertEqual(
            line([goodnotes, plugin("class_photos", .error, error: "plugin_setup_failed")]), "课堂照片出错：插件启动失败")
        let organizing = [run("knowledge_organize", .running, started: "2026-10-15T20:57:00+08:00", seconds: nil)]
        XCTAssertEqual(line([goodnotes], organizing), "正在整理知识库，已经 3 分 00 秒")
        XCTAssertEqual(Names.job("knowledge_organize"), "整理知识库")
        XCTAssertEqual(Names.job("chat:knowledge"), "回复对话 · 知识库")
    }

    func testDigestSectionsGroupBySourceAndDay() {
        let digests = [
            Digest(id: "1", date: "2026-10-15", plugin: "qq_screen", summary: "班级群：交表（已建待办）\n\n宿舍群：停水通知", createdAt: now),
            Digest(id: "2", date: "2026-10-14", plugin: "qq_screen", summary: "讲座宣传", createdAt: now),
            Digest(id: "3", date: "2026-10-15", plugin: "feishu", summary: "实验室：交材料", createdAt: now),
        ]
        let runs = [
            run("fetch:qq_screen", .succeeded, started: "2026-10-15T20:00:00+08:00"),
            run("fetch:qq_screen", .failed, started: "2026-10-15T09:00:00+08:00", error: "screen_history_gap"),
            run("fetch:qq_screen", .succeeded, started: "2026-10-14T23:30:00+08:00"),
            run("extract_todos", .succeeded, started: "2026-10-15T20:05:00+08:00"),
        ]
        let todos = [
            todo("a", plugin: "qq_screen", created: "2026-10-15T20:06:00+08:00"),
            todo("b", plugin: "qq_screen,feishu", created: "2026-10-15T20:07:00+08:00"),
            todo("c", plugin: "qq_screen", created: "2026-10-14T23:40:00+08:00"),
        ]
        let sections = DigestSections.build(digests: digests, todos: todos, runs: runs, calendar: calendar)
        XCTAssertEqual(sections.map(\.plugin), ["qq_screen", "feishu"])  // "QQ" sorts before "飞书"
        let qq = sections[0]
        XCTAssertEqual(qq.name, "QQ")
        XCTAssertEqual(qq.days.map(\.date), ["2026-10-15", "2026-10-14"])
        XCTAssertEqual(qq.days[0].notes, ["班级群：交表（已建待办）", "宿舍群：停水通知"])
        XCTAssertEqual(qq.days[0].reads.map(\.status), [.failed, .succeeded])  // oldest first
        XCTAssertEqual(qq.days[0].todos.map(\.id), ["a", "b"])
        XCTAssertEqual(qq.days[1].todos.map(\.id), ["c"])  // by the backend's day, not UTC
        XCTAssertEqual(sections[1].days[0].todos.map(\.id), ["b"])
        XCTAssertTrue(sections[1].days[0].reads.isEmpty)
    }

    func testCronText() {
        XCTAssertEqual(CronText.describe("*/30 * * * *"), "每 30 分钟")
        XCTAssertEqual(CronText.describe("0 7-23/3 * * *"), "每天 7:00–23:00，每 3 小时")
        XCTAssertEqual(CronText.describe("15 8 * * *"), "每天 8:15")
        XCTAssertEqual(CronText.describe("5 * * * *"), "每小时的第 5 分")
        XCTAssertEqual(CronText.describe("0 21 * * sun"), "0 21 * * sun")  // shown as is
        XCTAssertTrue(CronText.isValid("0 7-23/3 * * *"))
        XCTAssertFalse(CronText.isValid("0 7 * *"))
        XCTAssertTrue(CronText.describe("0 7").hasPrefix("格式不对"))
    }

    func testMenuBarCount() throws {
        XCTAssertEqual(MenuBarSummary.remaining(nil), 0)
        var today = MockData.today(now: now, calendar: calendar)
        let open = today.todos.count
        today.todos[0].status = .completed  // completed in the App before the next refresh
        XCTAssertEqual(MenuBarSummary.remaining(today), open - 1)
    }

    func testHealthReportsDecode() throws {
        let page = try JSONDecoder.assistant().decode(Page<HealthReport>.self, from: Data(Fixtures.healthReports.utf8))
        XCTAssertEqual(page.total, 2)
        XCTAssertEqual(page.items[0].kind, .morning)
        XCTAssertEqual(page.items[0].status, .ready)
        XCTAssertTrue(page.items[0].body?.contains("\n\n- 静息心率 58") ?? false)
        XCTAssertNil(page.items[1].title)
        XCTAssertEqual(page.items[1].error, "report_not_written")
    }
}

final class TodoDraftTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)

    func at(_ text: String) -> Date { APIDateCoding.date(from: text)! }

    func testOnlyChangesAreSent() throws {
        let todo = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todo.utf8))
        var draft = TodoDraft(todo, calendar: calendar)
        XCTAssertTrue(draft.patch(from: todo, calendar: calendar).isEmpty)
        draft.title = "  完成信息核对（改）  "
        XCTAssertEqual(draft.patch(from: todo, calendar: calendar), TodoPatch(title: "完成信息核对（改）"))
        draft.title = "   "
        XCTAssertFalse(draft.isValid)
    }

    func testDeadlines() throws {
        let todo = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todo.utf8))
        var draft = TodoDraft(todo, calendar: calendar)
        // Date only: local midnight, no time.
        draft.hasTime = false
        XCTAssertEqual(
            draft.patch(from: todo, calendar: calendar),
            TodoPatch(due: .set(at("2026-10-16T00:00:00+08:00")), hasTime: false))
        // A new time.
        draft.hasTime = true
        draft.due = at("2026-10-17T09:30:00+08:00")
        XCTAssertEqual(
            draft.patch(from: todo, calendar: calendar),
            TodoPatch(due: .set(at("2026-10-17T09:30:00+08:00")), hasTime: true))
        // No deadline any more.
        draft.hasDue = false
        XCTAssertEqual(draft.patch(from: todo, calendar: calendar), TodoPatch(due: .clear))
        // A todo without a deadline gets one; a new draft suggests today 18:00.
        let open = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todoWithoutDue.utf8))
        var fresh = TodoDraft(open, now: at("2026-10-15T10:00:00+08:00"), calendar: calendar)
        XCTAssertFalse(fresh.hasDue)
        XCTAssertEqual(fresh.due, at("2026-10-15T18:00:00+08:00"))
        fresh.hasDue = true
        fresh.hasTime = true
        XCTAssertEqual(
            fresh.patch(from: open, calendar: calendar),
            TodoPatch(due: .set(at("2026-10-15T18:00:00+08:00")), hasTime: true))
    }
}

final class AgentTextTests: XCTestCase {
    func testHeadlinesAndLastRun() {
        let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
        let now = APIDateCoding.date(from: "2026-10-15T21:00:00+08:00")!
        XCTAssertEqual(AgentText.headline(.offline), "连不上 Mac")
        XCTAssertEqual(AgentText.headline(.idle), "Mac 在线，空闲中")
        XCTAssertEqual(AgentText.lastRun(nil, now: now, calendar: calendar), "还没有运行记录")
        let run = Run(
            id: "r", job: "extract_todos", harness: "claude_code",
            startedAt: now.addingTimeInterval(-180), finishedAt: now.addingTimeInterval(-160),
            status: .failed, error: "quota_exhausted", attempt: 1)
        XCTAssertEqual(
            AgentText.lastRun(run, now: now, calendar: calendar), "最近一次：提炼待办 · 失败 · 3 分钟前 · 额度用完")
    }
}
