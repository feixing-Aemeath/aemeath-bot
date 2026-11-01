import XCTest

@testable import AssistantCore

final class ModelTests: XCTestCase {
    func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.assistant().decode(type, from: Data(json.utf8))
    }

    func iso(_ text: String) -> Date {
        APIDateCoding.date(from: text)!
    }

    func testTodoFromBackend() throws {
        let todo = try decode(Todo.self, Fixtures.todo)
        XCTAssertEqual(todo.id, "ab0904cc-b9d3-4732-a6aa-534f255a6722")
        XCTAssertEqual(todo.title, "完成信息核对")
        XCTAssertEqual(todo.dueAt, iso("2026-10-16T17:00:00+08:00"))
        XCTAssertTrue(todo.hasTime)
        XCTAssertEqual(todo.sourceRefs.count, 1)
        XCTAssertEqual(todo.confidence, 0.93, accuracy: 1e-9)
        XCTAssertEqual(todo.status, .pending)
        XCTAssertNil(todo.reminderID)
        XCTAssertEqual(todo.syncStatus, .pending)
    }

    func testTodoPageAndOptionalFields() throws {
        let page = try decode(Page<Todo>.self, Fixtures.todoPage)
        XCTAssertEqual(page.total, 2)
        let second = page.items[1]
        XCTAssertNil(second.dueAt)
        XCTAssertEqual(second.status, .completed)
        XCTAssertEqual(second.reminderID, "fake-reminders-t2")
        XCTAssertEqual(second.sourcePlugin, "qq_screen,feishu")
    }

    func testStatusTodayDigestsRunsSettingsPlugins() throws {
        let status = try decode(Status.self, Fixtures.status)
        XCTAssertTrue(status.online)
        XCTAssertEqual(status.appleMode, "fake")
        XCTAssertEqual(status.agents["codex"]?.available, false)
        XCTAssertEqual(status.lastRun?.error, "quota_exhausted")
        XCTAssertEqual(status.lastRun?.status, .failed)
        XCTAssertEqual(status.pendingSync, 2)

        let today = try decode(Today.self, Fixtures.today)
        XCTAssertEqual(today.date, "2026-10-16")
        XCTAssertEqual(today.todos.count, 1)
        XCTAssertEqual(today.cards.map(\.type), [.text, .unknown("sparkline")])

        let digests = try decode(Page<Digest>.self, Fixtures.digests)
        XCTAssertEqual(digests.items.first?.date, "2026-10-15")

        let runs = try decode(Page<Run>.self, Fixtures.runs)
        XCTAssertNil(runs.items[0].finishedAt)
        XCTAssertNil(runs.items[0].harness)
        XCTAssertEqual(runs.items[0].status, .running)
        XCTAssertEqual(runs.items[0].attempt, 2)

        let settings = try decode(RuntimeSettings.self, Fixtures.settings)
        XCTAssertEqual(settings.harnessOrder, ["claude_code", "codex"])
        XCTAssertEqual(settings.retryBaseSeconds, 2)
        XCTAssertEqual(settings.extractionCron, "0 7-23/3 * * *")

        let plugins = try decode([PluginView].self, Fixtures.plugins)
        XCTAssertEqual(plugins[0].state, .disabled)
        XCTAssertEqual(plugins[0].configSchema["properties"]?["whitelist"]?["type"], .string("array"))
        XCTAssertEqual(plugins[0].config, .object([:]))
        XCTAssertEqual(plugins[0].mascot, PluginMascot(sticker: nil, accent: "#22B8CF"))

        XCTAssertEqual(try decode(JobAccepted.self, Fixtures.jobAccepted).jobID, "memory_index")
        XCTAssertEqual(try decode(HealthAccepted.self, Fixtures.healthAccepted).accepted, 1)
    }

    func testDateFormats() {
        let reference = Date(timeIntervalSince1970: 1_791_536_400)  // 2026-10-09T09:00:00Z
        XCTAssertEqual(APIDateCoding.date(from: "2026-10-09T09:00:00Z"), reference)
        XCTAssertEqual(APIDateCoding.date(from: "2026-10-09T17:00:00+08:00"), reference)
        XCTAssertEqual(APIDateCoding.date(from: "2026-10-09T09:00:00.000000+00:00"), reference)
        let micro = APIDateCoding.date(from: "2026-10-09T09:00:00.453973Z")!
        XCTAssertEqual(micro.timeIntervalSince(reference), 0.453, accuracy: 0.001)
        XCTAssertEqual(APIDateCoding.date(from: "2026-10-09T09:00:00.4Z")!
            .timeIntervalSince(reference), 0.4, accuracy: 0.001)
        XCTAssertNil(APIDateCoding.date(from: "2026-10-09"))
        XCTAssertNil(APIDateCoding.date(from: "not a date"))
        XCTAssertEqual(APIDateCoding.string(from: reference), "2026-10-09T09:00:00Z")
    }

    func encoded<T: Encodable>(_ value: T) throws -> String {
        String(decoding: try JSONEncoder.assistant().encode(value), as: UTF8.self)
    }

    func testTodoPatchSendsOnlyChanges() throws {
        XCTAssertEqual(try encoded(TodoPatch.complete()), #"{"status":"completed"}"#)
        XCTAssertEqual(try encoded(TodoPatch(due: .clear)), #"{"due_at":null}"#)
        let due = iso("2026-10-16T17:00:00+08:00")
        XCTAssertEqual(
            try encoded(TodoPatch(title: "改名", due: .set(due), hasTime: true)),
            #"{"due_at":"2026-10-16T09:00:00Z","has_time":true,"title":"改名"}"#)
        XCTAssertTrue(TodoPatch().isEmpty)
        XCTAssertFalse(TodoPatch.delete().isEmpty)
    }

    func testOtherPatchesOmitUnsetFields() throws {
        var settings = SettingsPatch()
        settings.confidenceThreshold = 0.9
        XCTAssertEqual(try encoded(settings), #"{"confidence_threshold":0.9}"#)
        XCTAssertEqual(try encoded(PluginPatch(enabled: true)), #"{"enabled":true}"#)
        let config = PluginPatch(config: .object(["whitelist": .array([.string("班级群")])]))
        XCTAssertEqual(try encoded(config), #"{"config":{"whitelist":["班级群"]}}"#)
    }

    func testHealthBatchEncoding() throws {
        let start = iso("2026-10-15T01:00:00Z")
        let batch = HealthBatch(samples: [
            HealthSample(
                id: "s1", type: "step_count", value: 42, unit: "count", startAt: start,
                endAt: start.addingTimeInterval(3600), source: "iPhone")
        ])
        let json = try encoded(batch)
        XCTAssertTrue(json.contains(#""start_at":"2026-10-15T01:00:00Z""#), json)
        XCTAssertTrue(json.contains(#""end_at":"2026-10-15T02:00:00Z""#), json)
    }

    func testDueText() throws {
        let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
        let now = iso("2026-10-15T10:00:00+08:00")
        var todo = try decode(Todo.self, Fixtures.todo)  // due 2026-10-16 17:00 +08:00
        XCTAssertEqual(DueText.describe(todo, now: now, calendar: calendar), "明天 17:00")
        todo.dueAt = iso("2026-10-15T09:30:00+08:00")
        XCTAssertEqual(DueText.describe(todo, now: now, calendar: calendar), "已逾期 · 今天 09:30")
        todo.hasTime = false
        todo.dueAt = iso("2026-10-15T00:00:00+08:00")
        XCTAssertEqual(DueText.describe(todo, now: now, calendar: calendar), "今天")
        todo.dueAt = iso("2026-10-20T00:00:00+08:00")
        XCTAssertEqual(DueText.describe(todo, now: now, calendar: calendar), "10月20日")
        todo.dueAt = iso("2026-10-14T00:00:00+08:00")
        XCTAssertEqual(DueText.describe(todo, now: now, calendar: calendar), "已逾期 · 10月14日")
        todo.status = .completed
        XCTAssertEqual(DueText.describe(todo, now: now, calendar: calendar), "10月14日")
        todo.dueAt = nil
        XCTAssertEqual(DueText.describe(todo, now: now, calendar: calendar), "无截止时间")
    }

    func testMockDataIsConsistent() {
        let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
        let now = iso("2026-10-15T10:00:00+08:00")
        let today = MockData.today(now: now, calendar: calendar)
        XCTAssertEqual(today.date, "2026-10-15")
        XCTAssertFalse(today.todos.isEmpty)
        XCTAssertTrue(today.todos.allSatisfy { $0.status == .pending })
        for card in today.cards {
            if case .unsupported = card.content { XCTFail("mock card \(card.title) unsupported") }
        }
    }
}

final class ChatChoiceTests: XCTestCase {
    func status(_ agents: [String: AgentStatus]) -> Status {
        Status(online: true, platform: "darwin", appleMode: "real", agents: agents, lastRun: nil, pendingSync: 0)
    }

    func testOptionsComeFromTheAvailableAgents() {
        let options = ChatOption.build(
            status: status([
                "claude_code": AgentStatus(available: true, models: [AgentModel(id: "opus", name: "Opus")]),
                "codex": AgentStatus(available: true),
            ]))
        XCTAssertEqual(options.map(\.id), ["", "claude_code/opus", "codex"])
        XCTAssertEqual(options.map(\.short), ["自动", "Opus", "Codex"])
        XCTAssertEqual(options[2].title, "默认模型")
        // An Agent whose CLI is missing is not offered; without a status only 自动 is.
        let missing = ChatOption.build(status: status(["codex": AgentStatus(available: false)]))
        XCTAssertEqual(missing.map(\.id), [""])
        XCTAssertEqual(ChatOption.build(status: nil).map(\.short), ["自动"])
        XCTAssertEqual(ChatOption.label(of: ChatChoice(harness: "codex", model: "gpt-x"), in: missing), "gpt-x")
    }

    func testAChoiceIsKeptAndSentAsTyped() throws {
        let choice = ChatChoice(harness: "claude_code", model: "opus")
        XCTAssertEqual(ChatChoice(stored: choice.stored), choice)
        XCTAssertEqual(ChatChoice(stored: ""), .automatic)
        XCTAssertEqual(ChatChoice(stored: nil), .automatic)
        XCTAssertEqual(ChatChoice(stored: "codex"), ChatChoice(harness: "codex", model: nil))
        let body = try JSONSerialization.jsonObject(
            with: JSONEncoder().encode(ChatSend(text: "在吗", botName: "作业Bot", choice: choice))) as? [String: String]
        XCTAssertEqual(body, ["text": "在吗", "bot_name": "作业Bot", "harness": "claude_code", "model": "opus"])
        let plain = try JSONSerialization.jsonObject(
            with: JSONEncoder().encode(ChatSend(text: "在吗", botName: nil))) as? [String: String]
        XCTAssertEqual(plain, ["text": "在吗"])
    }

    func testOlderStatusWithoutModelsStillDecodes() throws {
        let json = Data(#"{"available": true, "authentication": "unchecked"}"#.utf8)
        let agent = try JSONDecoder().decode(AgentStatus.self, from: json)
        XCTAssertEqual(agent.models, [])
        let listed = Data(#"{"available": true, "models": [{"id": "opus", "name": "Opus", "default": false}]}"#.utf8)
        XCTAssertEqual(try JSONDecoder().decode(AgentStatus.self, from: listed).models.first?.id, "opus")
    }
}
