import XCTest

@testable import AssistantCore

/// A clock the tests move by hand.
final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var current: Date

    init(_ start: Date) {
        current = start
    }

    var now: Date {
        lock.lock()
        defer { lock.unlock() }
        return current
    }

    func advance(_ seconds: TimeInterval) {
        lock.lock()
        current = current.addingTimeInterval(seconds)
        lock.unlock()
    }
}

final class MockBackendTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    let start = APIDateCoding.date(from: "2026-10-15T21:00:00+08:00")!

    func backend(_ clock: TestClock) -> MockBackend {
        MockBackend(now: clock.now, calendar: calendar, clock: { clock.now })
    }

    func testTodayHasOpenTodosDueTodayOrEarlierWithSources() async throws {
        let mock = backend(TestClock(start))
        let today = try await mock.today()
        XCTAssertEqual(today.date, "2026-10-15")
        XCTAssertFalse(today.summary.isEmpty)
        XCTAssertEqual(today.todos.map(\.id), ["t3", "t2", "t1"])  // by deadline, overdue first
        XCTAssertTrue(today.todos.allSatisfy { $0.status == .pending && !($0.sources ?? []).isEmpty })
        XCTAssertTrue(TodoSourceText.describe(today.todos[2]).hasPrefix("QQ · 软件工程2班"))
    }

    func testCompletingEditingAndDeletingTodos() async throws {
        let mock = backend(TestClock(start))
        let done = try await mock.updateTodo(id: "t1", .complete())
        XCTAssertEqual(done.status, .completed)
        var ids = try await mock.today().todos.map(\.id)
        XCTAssertFalse(ids.contains("t1"))

        let due = APIDateCoding.date(from: "2026-10-20T09:00:00+08:00")!
        let edited = try await mock.updateTodo(
            id: "t2", TodoPatch(title: "交第三次作业", due: .set(due), hasTime: true))
        XCTAssertEqual(edited.title, "交第三次作业")
        XCTAssertEqual(edited.dueAt, due)
        XCTAssertTrue(edited.hasTime)
        ids = try await mock.today().todos.map(\.id)
        XCTAssertFalse(ids.contains("t2"))  // no longer due today

        let cleared = try await mock.updateTodo(id: "t2", TodoPatch(due: .clear))
        XCTAssertNil(cleared.dueAt)
        XCTAssertFalse(cleared.hasTime)

        let deleted = try await mock.updateTodo(id: "t3", .delete())
        XCTAssertEqual(deleted.status, .deleted)
        XCTAssertNil(deleted.reminderID)  // the reminder goes too
        let remaining = try await mock.todos(status: nil).map(\.id)
        XCTAssertFalse(remaining.contains("t3"))
        do {
            _ = try await mock.updateTodo(id: "t3", TodoPatch(status: .pending))
            XCTFail("a deleted todo cannot come back")
        } catch {
            XCTAssertEqual(error as? APIError, .conflict)
        }
        do {
            _ = try await mock.updateTodo(id: "missing", .complete())
            XCTFail("unknown todo")
        } catch {
            XCTAssertEqual(error as? APIError, .notFound)
        }
    }

    func testRunNowRunsForAWhileThenSucceeds() async throws {
        let clock = TestClock(start)
        let mock = backend(clock)
        let accepted = try await mock.runJob("extract_todos")
        XCTAssertEqual(accepted.jobID, "extract_todos")
        var runs = try await mock.runs()
        XCTAssertEqual(runs.first?.status, .running)
        XCTAssertEqual(runs.first?.harness, "claude_code")
        var status = try await mock.status()
        XCTAssertEqual(MascotState.forAssistant(status: status, runs: runs, now: clock.now), .working)
        do {
            _ = try await mock.runJob("extract_todos")
            XCTFail("the same job cannot run twice at once")
        } catch {
            XCTAssertEqual(error as? APIError, .conflict)
        }
        clock.advance(MockBackend.jobDuration + 0.5)
        runs = try await mock.runs()
        XCTAssertEqual(runs.first?.status, .succeeded)
        status = try await mock.status()
        XCTAssertEqual(status.lastRun?.status, .succeeded)
        XCTAssertEqual(MascotState.forAssistant(status: status, runs: runs, now: clock.now), .done)
        do {
            _ = try await mock.runJob("fetch:wechat_screen")  // disabled plugin
            XCTFail("disabled plugins have no fetch job")
        } catch {
            XCTAssertEqual(error as? APIError, .notFound)
        }
    }

    func testPluginsCardsAndSwitches() async throws {
        let mock = backend(TestClock(start))
        let plugins = try await mock.plugins()
        XCTAssertEqual(
            plugins.map(\.id),
            [
                "qq_screen", "feishu", "wechat_screen", "grades", "campus", "homework", "weather", "timetable",
                "token_usage", "health", "goodnotes", "class_photos",
            ])
        XCTAssertEqual(plugins[0].mascot?.sticker, "red_ribbon")
        XCTAssertEqual(plugins.first { $0.id == "health" }?.mascot?.sticker, "lily_veil")
        XCTAssertNil(plugins[1].mascot?.sticker)  // placeholder
        // Every character plugin has a sticker the stickers folder names (课表 has 作业's, like
        // 作业), and cards unless it only brings 知识库Bot material (GoodNotes, 课堂照片).
        let named = ["red_ribbon", "luosela", "moning", "shouanren", "lily_veil", "aemeath", "orange_daisy"]
        for plugin in plugins where !plugin.provides.contains("source") {
            XCTAssertTrue(named.contains(plugin.mascot?.sticker ?? ""), plugin.id)
            guard plugin.provides.contains("cards") else { continue }
            let cards = try await mock.cards(pluginID: plugin.id)
            XCTAssertFalse(cards.isEmpty, plugin.id)
            for card in cards {
                if case .unsupported = card.content { XCTFail("mock card \(card.title) unsupported") }
            }
        }
        let health = try await mock.cards(pluginID: "health")
        XCTAssertTrue(health.contains { $0.type == .chart })
        let off = try await mock.updatePlugin(id: "health", PluginPatch(enabled: false))
        XCTAssertEqual(off.state, .disabled)
        let noCards = try await mock.cards(pluginID: "health")
        XCTAssertTrue(noCards.isEmpty)
        let on = try await mock.updatePlugin(id: "wechat_screen", PluginPatch(enabled: true))
        XCTAssertEqual(on.state, .enabled)
    }

    func testSettingsAndReports() async throws {
        let mock = backend(TestClock(start))
        var patch = SettingsPatch()
        patch.extractionCron = "0 8-22/2 * * *"
        patch.harnessOrder = ["codex", "claude_code"]
        let settings = try await mock.updateSettings(patch)
        XCTAssertEqual(settings.extractionCron, "0 8-22/2 * * *")
        XCTAssertEqual(settings.sourceCron, "*/30 * * * *")
        XCTAssertEqual(settings.harnessOrder.first, "codex")
        var broken = SettingsPatch()
        broken.sourceCron = "every 30 minutes"
        do {
            _ = try await mock.updateSettings(broken)
            XCTFail("a cron needs five fields")
        } catch {
            XCTAssertEqual(error as? APIError, .invalidRequest)
        }
        let reports = try await mock.healthReports()
        XCTAssertEqual(reports.first?.kind, .morning)
        XCTAssertEqual(reports.first?.status, .ready)
        XCTAssertTrue(reports.contains { $0.kind == .weekly })
        XCTAssertTrue(reports.contains { $0.status == .failed })
    }

    func testOffline() async throws {
        let mock = backend(TestClock(start))
        await mock.setOnline(false)
        do {
            _ = try await mock.status()
            XCTFail("offline")
        } catch {
            guard case .transport = error as? APIError else { return XCTFail("\(error)") }
        }
    }

    func testDigestPageFromMockData() async throws {
        let mock = backend(TestClock(start))
        let digests = try await mock.digests()
        let todos = try await mock.todos(status: nil)
        let runs = try await mock.runs()
        let sections = DigestSections.build(
            digests: digests, todos: todos, runs: runs, calendar: calendar)
        XCTAssertEqual(sections.first?.plugin, "qq_screen")
        let today = try XCTUnwrap(sections.first?.days.first)
        XCTAssertEqual(today.date, "2026-10-15")
        XCTAssertEqual(today.notes.count, 3)
        XCTAssertFalse(today.reads.isEmpty)
        XCTAssertFalse(today.todos.isEmpty)
    }
}
