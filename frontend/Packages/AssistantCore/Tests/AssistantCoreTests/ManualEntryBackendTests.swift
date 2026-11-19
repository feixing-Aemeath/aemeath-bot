import XCTest

@testable import AssistantCore
@testable import AssistantUI

final class ManualEntryBackendTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    let now = APIDateCoding.date(from: "2027-02-17T10:00:00+08:00")!

    func testMockCreatesUserTodosAndDeduplicatesOnlyPending() async throws {
        let backend = MockBackend(now: now, calendar: calendar, clock: { self.now })
        let request = TodoNew(
            title: "  给花浇水  ", notes: "小盆", missed: true,
            missedSource: MissedSource(plugin: "wechat_screen", chatName: "种花小组"))
        let result = try await backend.createTodo(request)
        XCTAssertFalse(result.duplicate)
        XCTAssertEqual(result.todo.title, "给花浇水")
        XCTAssertEqual(result.todo.createdBy, "user")
        XCTAssertEqual(result.todo.sourcePlugin, "app")
        XCTAssertNotNil(result.todo.reminderID)
        let repeated = try await backend.createTodo(request)
        XCTAssertTrue(repeated.duplicate)
        XCTAssertEqual(repeated.todo.id, result.todo.id)
        _ = try await backend.updateTodo(id: result.todo.id, .complete())
        let fresh = try await backend.createTodo(request)
        XCTAssertFalse(fresh.duplicate)
        XCTAssertNotEqual(fresh.todo.id, result.todo.id)
        let stats = try await backend.extractionStats(weeks: 4)
        XCTAssertEqual(stats.weeks.first?.weekStart, "2027-02-15")
        XCTAssertEqual(stats.weeks.first?.missed, 2)  // duplicate submission not counted
        XCTAssertEqual(stats.weeks.last?.summary, "这周没有数据")
        do {
            _ = try await backend.createTodo(TodoNew(title: "", hasTime: true))
            XCTFail()
        } catch { XCTAssertEqual(error as? APIError, .invalidRequest) }
    }

    func testMockInboxIdempotencyAndJobAlreadyRunning() async throws {
        let clock = TestClock(now)
        let backend = MockBackend(now: now, calendar: calendar, clock: { clock.now })
        let request = InboxItem(text: "请核对旅途安排", note: "只做摘要", missed: true)
        let first = try await backend.submitInbox(request)
        XCTAssertTrue(first.jobStarted)
        let duplicate = try await backend.submitInbox(request)
        XCTAssertEqual(duplicate.messageID, first.messageID)
        XCTAssertFalse(duplicate.jobStarted)
        let next = try await backend.submitInbox(InboxItem(text: "另一个消息"))
        XCTAssertFalse(next.jobStarted)
        XCTAssertNotEqual(next.messageID, first.messageID)
        clock.advance(MockBackend.jobDuration + 1)
        _ = try await backend.runs()
        let digests = try await backend.digests()
        XCTAssertTrue(digests.contains { $0.plugin == "share" && $0.summary.contains(request.text) })
        let stats = try await backend.extractionStats(weeks: 1)
        XCTAssertEqual(stats.weeks.first?.missed, 1)
        clock.advance(601)
        let later = try await backend.submitInbox(request)
        XCTAssertNotEqual(later.messageID, first.messageID)
    }

    func testMockSourcesAndOpenConversationRules() async throws {
        let clock = TestClock(now)
        let backend = MockBackend(now: now, calendar: calendar, clock: { clock.now })
        let plugins = try await backend.plugins()
        let choices = ManualEntryText.conversations(plugins)
        XCTAssertFalse(choices.isEmpty)
        XCTAssertTrue(choices.allSatisfy { ["qq_screen", "wechat_screen"].contains($0.plugin) })
        let today = try await backend.today()
        let item = try XCTUnwrap(today.unsureItems.first)
        XCTAssertTrue(item.canOpen)
        do {
            _ = try await backend.openChat(pluginID: "feishu", chatName: "群")
            XCTFail()
        } catch { XCTAssertEqual(error as? APIError, .notFound) }
        do {
            _ = try await backend.openChat(pluginID: "qq_screen", chatName: "不在名单")
            XCTFail()
        } catch { XCTAssertEqual(error as? APIError, .screenOpen("screen_open_not_whitelisted")) }
        let accepted = try await backend.openChat(pluginID: item.plugin!, chatName: item.chatName!)
        XCTAssertTrue(accepted.accepted)
        do {
            _ = try await backend.openChat(pluginID: item.plugin!, chatName: item.chatName!)
            XCTFail()
        } catch { XCTAssertEqual(error as? APIError, .screenOpen("screen_open_daily_limit")) }
        clock.advance(MockBackend.jobDuration + 1)
        let read = try await backend.today()
        XCTAssertTrue(read.unsureItems.isEmpty)
        let todo = try XCTUnwrap(today.todos.first)
        let sources = try await backend.todoSources(id: todo.id)
        XCTAssertEqual(sources.items.first?.text, todo.sourceExcerpt)
        XCTAssertEqual(sources.items.map(\.messageID), todo.sources?.map(\.messageID))
        let manual = try await backend.createTodo(TodoNew(title: "自己的记录"))
        let absent = try await backend.todoSources(id: manual.todo.id)
        XCTAssertTrue(absent.items.isEmpty)
    }

    func testMockDeleteFeedbackCountedOnceAndOnlyForAssistant() async throws {
        let backend = MockBackend(now: now, calendar: calendar, clock: { self.now })
        let items = try await backend.todos(status: .pending)
        let agent = try XCTUnwrap(items.first)
        _ = try await backend.updateTodo(id: agent.id, .delete(reason: .notATodo))
        _ = try await backend.updateTodo(id: agent.id, .delete(reason: .notATodo))
        let manual = try await backend.createTodo(TodoNew(title: "买纸"))
        _ = try await backend.updateTodo(id: manual.todo.id, .delete(reason: .notATodo))
        let stats = try await backend.extractionStats(weeks: 1)
        XCTAssertEqual(stats.weeks.first?.notATodo, 1)
    }

    @MainActor
    func testInboxHistoryAndUnsureHidingSurviveStoreRecreation() async throws {
        let clock = TestClock(Date())
        let backend = MockBackend(now: clock.now, calendar: calendar, clock: { clock.now })
        let defaults = UserDefaults(suiteName: "ManualEntry-\(UUID().uuidString)")!
        let store = AppStore(backend: backend)
        store.manualDefaults = defaults
        await store.refresh()
        let item = try XCTUnwrap(store.visibleUnsure.first)
        store.dismissUnsure(item)
        XCTAssertTrue(store.visibleUnsure.isEmpty)
        try await store.submitInbox(InboxItem(text: "新消息正文", note: "请看看"))
        let bot = try XCTUnwrap(store.bots.first { $0.isAssistant })
        XCTAssertTrue(store.isReplying(bot))
        XCTAssertEqual(store.botState(bot), .working)
        XCTAssertTrue(store.messages(bot).contains { $0.author == .me && $0.text.contains("新消息正文") })
        let reloaded = AppStore(backend: backend)
        reloaded.manualDefaults = defaults
        await reloaded.refresh()
        XCTAssertEqual(reloaded.inboxReceipts.count, 1)
        XCTAssertTrue(reloaded.visibleUnsure.isEmpty)
        XCTAssertTrue(reloaded.isReplying(bot))
        clock.advance(MockBackend.jobDuration + 2)
        await reloaded.refresh()
        XCTAssertFalse(reloaded.inboxReceipts[0].waiting)
        XCTAssertFalse(reloaded.isReplying(bot))
        XCTAssertTrue(reloaded.messages(bot).contains { $0.author == .me && $0.text.contains("请看看") })
    }

    @MainActor
    func testCreatedAndEditedTodosAreSharedByAssistantTimeline() async throws {
        let backend = MockBackend(calendar: calendar)
        let store = AppStore(backend: backend)
        store.manualDefaults = UserDefaults(suiteName: "ManualEntry-\(UUID().uuidString)")!
        await store.refresh()
        let created = try await store.createTodo(TodoNew(title: "给阳台浇水", dueAt: Date(), hasTime: true))
        let bot = try XCTUnwrap(store.bots.first { $0.isAssistant })
        let summary = try XCTUnwrap(store.messages(bot).first { $0.title == "今天的总结" })
        XCTAssertTrue(summary.todos.contains { $0.id == created.todo.id })
        await store.toggleDone(created.todo)
        let changed = try XCTUnwrap(
            store.messages(bot).first { $0.title == "今天的总结" }?.todos.first { $0.id == created.todo.id })
        XCTAssertEqual(changed.status, .completed)
        await store.delete(changed)
        XCTAssertFalse(store.messages(bot).flatMap(\.todos).contains { $0.id == changed.id })
    }
}
