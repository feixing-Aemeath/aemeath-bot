import XCTest
#if os(macOS)
    import AppKit
#endif
@testable import AssistantCore
@testable import AssistantUI

final class AppStoreTests: XCTestCase {
    var session: URLSession!
    var completed = false
    var failPatch = false
    var refusePatch = false
    var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "AppStoreTests-\(UUID().uuidString)")!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        session = URLSession(configuration: configuration)
        StubProtocol.requests = []
        StubProtocol.asyncHandler = nil
        StubProtocol.handler = { [self] request in
            switch request.url!.path {
            case "/api/v1/status": return (200, Fixtures.status)
            case "/api/v1/today": return (200, Fixtures.today)
            case "/api/v1/todos":
                return (200, "{\"items\":[\(todoJSON)],\"total\":1}")
            case let path where path.hasPrefix("/api/v1/todos/"):
                if failPatch { return (500, "{}") }
                if refusePatch { return (409, "{}") }
                completed = StubProtocol.body(of: request)?.contains("completed") == true
                return (200, todoJSON)
            case "/api/v1/digests": return (200, Fixtures.digests)
            case "/api/v1/runs": return (200, Fixtures.runs)
            case "/api/v1/plugins": return (200, Fixtures.plugins)
            case "/api/v1/health/reports": return (200, Fixtures.healthReports)
            case "/api/v1/settings": return (200, Fixtures.settings)
            default: return (404, "{}")
            }
        }
    }

    override func tearDown() {
        session.invalidateAndCancel()
        StubProtocol.handler = nil
        StubProtocol.asyncHandler = nil
        super.tearDown()
    }

    var todoJSON: String {
        completed ? Fixtures.todo.replacingOccurrences(of: "\"status\": \"pending\"", with: "\"status\": \"completed\"") : Fixtures.todo
    }

    func makeStore(useMockData: Bool = true) -> AppStore {
        AppStore(backend: LiveBackend(client: APIClient(
            baseURL: URL(string: "https://mini.example.test")!,
            tokens: StaticTokenProvider("synthetic"), session: session)),
            connection: ConnectionSettings(baseURL: "https://mini.example.test", useMockData: useMockData),
            watchCompletions: WatchCompletions(defaults: defaults), preferenceDefaults: defaults)
    }

    private func stubPreferences(_ respond: @escaping (URLRequest) -> (Int, String)) {
        let other = StubProtocol.handler!
        StubProtocol.handler = { request in
            request.url?.path == "/api/v1/app/bot-preferences" ? respond(request) : other(request)
        }
    }

    private let emptyPreferences = #"{"version":0,"entries":{}}"#

    @MainActor
    func testOfflineEditsSurviveRelaunchAndMergeAfterConflict() async {
        stubPreferences { _ in (503, "") }
        var offline: AppStore? = makeStore(useMockData: false)
        offline?.botPreferences["health"].customName = "我的名字"
        await offline?.refresh()
        offline = nil

        let winner = #"{"version":1,"entries":{"new_bot":{"custom_name":"","pinned":true,"visible":false,"updated_at":"2026-11-18T01:00:00.000000Z"}}}"#
        var versions: [Int] = []
        var methods: [String] = []
        stubPreferences { [emptyPreferences] request in
            methods.append(request.httpMethod!)
            guard request.httpMethod == "PUT" else { return (200, versions.isEmpty ? emptyPreferences : winner) }
            let body = Data((StubProtocol.body(of: request) ?? "").utf8)
            let payload = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            let entries = payload?["entries"] as? [String: [String: Any]]
            versions.append(payload?["expected_version"] as? Int ?? -1)
            XCTAssertEqual(entries?["health"]?["custom_name"] as? String, "我的名字")
            XCTAssertNotNil(entries?["health"]?["updated_at"] as? String)
            if versions.count == 1 { return (409, winner) }
            XCTAssertEqual(entries?["new_bot"]?["visible"] as? Bool, false)
            let snapshot: [String: Any] = ["version": 2, "entries": entries ?? [:]]
            let data = try! JSONSerialization.data(withJSONObject: snapshot)
            return (200, String(decoding: data, as: UTF8.self))
        }
        let restored = makeStore(useMockData: false)
        await restored.refresh()
        XCTAssertEqual(methods, ["GET", "PUT", "GET", "PUT"])
        XCTAssertEqual(versions, [0, 1])
        XCTAssertEqual(restored.botPreferences["health"].customName, "我的名字")
        XCTAssertTrue(restored.botPreferences["new_bot"].pinned)
        XCTAssertEqual(BotPreferences.load(from: defaults), restored.botPreferences)
    }

    @MainActor
    func testSecondConflictStopsAfterOneRetry() async {
        var local = BotPreferences()
        local["health"].pinned = true
        local.save(to: defaults)
        var puts = 0
        stubPreferences { [emptyPreferences] request in
            if request.httpMethod == "GET" { return (200, emptyPreferences) }
            puts += 1
            return (409, emptyPreferences)
        }
        let store = makeStore(useMockData: false)
        await store.refresh()
        XCTAssertEqual(puts, 2)
        XCTAssertTrue(BotPreferences.load(from: defaults)["health"].pinned)
    }

    @MainActor
    func testRapidLocalEditsAreSavedImmediatelyAndDebouncedIntoOnePut() async throws {
        let pushed = expectation(description: "debounced PUT")
        var puts = 0
        stubPreferences { [emptyPreferences] request in
            if request.httpMethod == "GET" { return (200, emptyPreferences) }
            let body = StubProtocol.body(of: request) ?? ""
            XCTAssertTrue(body.contains(#""custom_name":"最后""#))
            XCTAssertTrue(body.contains(#""visible":false"#))
            puts += 1
            pushed.fulfill()
            return (200, emptyPreferences)
        }
        let store = makeStore(useMockData: false)
        store.botPreferences["health"].customName = "先改"
        try await Task.sleep(for: .milliseconds(100))
        store.botPreferences["health"].customName = "最后"
        store.botPreferences["health"].visible = false
        XCTAssertEqual(BotPreferences.load(from: defaults), store.botPreferences)
        await store.refresh()
        XCTAssertEqual(puts, 0)
        await fulfillment(of: [pushed], timeout: 5)
        XCTAssertEqual(puts, 1)
    }

    @MainActor
    func testMergeOverLimitKeepsLocalEditsAndCanRecoverWhenSpaceOpens() async throws {
        let server = MockBackend()
        var remote = BotPreferences()
        remote.setOrder((0..<100).map { "bot_\($0)" })
        _ = try await server.updateBotPreferences(remote.entries, expectedVersion: 0)
        var local = BotPreferences()
        local["health"].pinned = true
        local.save(to: defaults)
        let store = AppStore(backend: server, connection: .init(useMockData: false), preferenceDefaults: defaults)
        await store.refresh()
        XCTAssertEqual(BotPreferences.load(from: defaults), local)
        var smaller = remote.entries
        smaller.removeValue(forKey: "bot_99")
        _ = try await server.updateBotPreferences(smaller, expectedVersion: 1)
        await store.refresh()
        let saved = try await server.botPreferences()
        XCTAssertEqual(saved.entries.count, 100)
        XCTAssertEqual(saved.entries["health"], local["health"])
    }

    @MainActor
    func testMissingEndpointAndMockModeKeepLocalPreferences() async throws {
        var methods: [String] = []
        stubPreferences { request in
            methods.append(request.httpMethod!)
            return (404, "")
        }
        let legacy = makeStore(useMockData: false)
        await legacy.refresh()
        legacy.botPreferences["health"].pinned = true
        let mock = makeStore()
        mock.botPreferences["health"].customName = "模拟"
        await mock.refresh()
        try await Task.sleep(for: .milliseconds(1200))
        XCTAssertEqual(methods, ["GET"])
        XCTAssertNil(legacy.message)
        XCTAssertEqual(BotPreferences.load(from: defaults)["health"].customName, "模拟")
    }

    @MainActor
    func testRefreshPullsBotPreferencesWithoutEchoing() async {
        stubPreferences { _ in
            (200, #"{"version":2,"entries":{"health":{"pinned":true,"custom_name":"小助手","visible":false,"updated_at":"2026-11-18T02:00:00.123456Z"}}}"#)
        }
        let store = makeStore(useMockData: false)
        await store.refresh()
        XCTAssertEqual(store.botPreferences["health"].customName, "小助手")
        XCTAssertFalse(store.botPreferences["health"].visible)
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "PUT" })
    }

    #if os(macOS)
    @MainActor
    func testMacPullsAnotherDevicesPreferencesAtLaunchAndOnForeground() async throws {
        let server = MockBackend()
        var phone = BotPreferences()
        phone["health"].pinned = true
        _ = try await server.updateBotPreferences(phone.entries, expectedVersion: 0)
        let mac = AppStore(backend: server, connection: .init(useMockData: false),
                           watchCompletions: WatchCompletions(defaults: defaults), preferenceDefaults: defaults)
        let started = expectation(description: "launch refresh")
        mac.onRefresh = { started.fulfill() }
        mac.start()
        defer { mac.stop() }
        await fulfillment(of: [started], timeout: 5)
        mac.onRefresh = nil
        XCTAssertTrue(mac.botPreferences["health"].pinned)

        phone["health"].visible = false
        _ = try await server.updateBotPreferences(phone.entries, expectedVersion: 1)
        let foreground = expectation(description: "foreground pull")
        let subscription = mac.$botPreferences.sink { prefs in
            if !prefs["health"].visible { foreground.fulfill() }
        }
        defer { subscription.cancel() }
        NotificationCenter.default.post(name: NSApplication.didBecomeActiveNotification, object: nil)
        await fulfillment(of: [foreground], timeout: 5)
        XCTAssertEqual(mac.botPreferences, phone)
    }
    #endif

    @MainActor
    func testWatchCompletionRefreshesBeforeApplyingOnColdStart() async {
        let store = makeStore()
        await store.handleWatchRequest(.setDone(id: "ab0904cc-b9d3-4732-a6aa-534f255a6722", done: true))
        XCTAssertTrue(completed)
        XCTAssertEqual(store.todos.first?.status, .completed)
        let requests = StubProtocol.requests
        let patch = requests.firstIndex { $0.httpMethod == "PATCH" }
        XCTAssertNotNil(patch)
        if let patch {
            XCTAssertTrue(requests[..<patch].contains { $0.url?.path == "/api/v1/todos" })
        }
    }

    @MainActor
    func testWatchCompletionRetriesAfterBackendRecovers() async {
        let store = makeStore()
        await store.refresh()
        failPatch = true
        await store.handleWatchRequest(.setDone(id: store.todos[0].id, done: true))
        XCTAssertFalse(completed)
        failPatch = false
        let relaunched = makeStore()
        await relaunched.refresh()
        XCTAssertTrue(completed)
        XCTAssertEqual(relaunched.todos.first?.status, .completed)
        let count = StubProtocol.requests.filter { $0.httpMethod == "PATCH" }.count
        await makeStore().refresh()
        XCTAssertEqual(StubProtocol.requests.filter { $0.httpMethod == "PATCH" }.count, count)
    }

    /// Deleted on the Mac in the meantime: the backend refuses, and asking again cannot help.
    @MainActor
    func testRefusedWatchCompletionIsNotRetriedOnEveryRefresh() async {
        let store = makeStore()
        await store.refresh()
        refusePatch = true
        await store.handleWatchRequest(.setDone(id: store.todos[0].id, done: true))
        let count = StubProtocol.requests.filter { $0.httpMethod == "PATCH" }.count
        XCTAssertEqual(count, 1)
        await store.refresh()
        XCTAssertEqual(StubProtocol.requests.filter { $0.httpMethod == "PATCH" }.count, count)
    }

    @MainActor
    func testWatchCompletionSurvivesFullyOfflineColdStart() async {
        let handler = StubProtocol.handler!
        StubProtocol.handler = { _ in (503, "{}") }
        await makeStore().handleWatchRequest(.setDone(id: "ab0904cc-b9d3-4732-a6aa-534f255a6722", done: true))
        XCTAssertFalse(completed)
        XCTAssertFalse(StubProtocol.requests.contains { $0.httpMethod == "PATCH" })
        StubProtocol.handler = handler
        let relaunched = makeStore()
        await relaunched.refresh()
        XCTAssertTrue(completed)
        XCTAssertEqual(relaunched.todos.first?.status, .completed)
    }

    @MainActor
    func testSwitchToOfflineBackendDropsOldContentAndLateCardResponse() async {
        let store = makeStore()
        await store.refresh()
        XCTAssertFalse(store.todos.isEmpty)
        let started = expectation(description: "old card request suspended")
        let gate = RefreshGate()
        StubProtocol.asyncHandler = { _ in
            await gate.wait(started)
            return (200, #"[{"type":"text","title":"旧连接","data":{"text":"示例"}}]"#)
        }
        let cards = Task { await store.loadCards("old-plugin") }
        await fulfillment(of: [started], timeout: 5)
        await store.activate(ConnectionSettings(baseURL: "https://other.example.test", useMockData: false),
                            backend: MockBackend(online: false))
        await gate.release()
        await cards.value
        XCTAssertNil(store.status)
        XCTAssertNil(store.today)
        XCTAssertNil(store.watchSnapshot())
        XCTAssertTrue(store.todos.isEmpty)
        XCTAssertTrue(store.digests.isEmpty)
        XCTAssertTrue(store.runs.isEmpty)
        XCTAssertTrue(store.plugins.isEmpty)
        XCTAssertTrue(store.healthReports.isEmpty)
        XCTAssertNil(store.settings)
        XCTAssertNil(store.lastRefresh)
        XCTAssertNil(store.extractionsToday)
        XCTAssertTrue(store.cards.isEmpty)
    }

    @MainActor
    func testPendingWatchTickWaitsForItsOwnBackendAcrossRelaunch() async throws {
        let id = "ab0904cc-b9d3-4732-a6aa-534f255a6722"
        let legacy: [[String: Any]] = [["id": id, "done": true, "revision": UUID().uuidString]]
        defaults.set(try JSONSerialization.data(withJSONObject: legacy), forKey: "watch.pendingCompletions")
        let store = makeStore()
        let original = store.backend
        let first = store.connection
        await store.activate(ConnectionSettings(baseURL: "https://other.example.test", useMockData: false),
                             backend: MockBackend())
        XCTAssertTrue(store.pendingWatchCompletions.isEmpty)
        let relaunched = makeStore()
        await relaunched.activate(first, backend: original)
        XCTAssertTrue(completed)
        XCTAssertTrue(relaunched.pendingWatchCompletions.isEmpty)
    }

    @MainActor
    func testColdPhoneKeepsLastWatchSnapshotUntilTodayArrives() async throws {
        let handler = StubProtocol.handler!
        StubProtocol.handler = { _ in (503, "{}") }
        let store = makeStore()
        XCTAssertNil(store.watchSnapshot())
        await store.refresh()
        XCTAssertNil(store.watchSnapshot())
        StubProtocol.handler = handler
        await store.refresh()
        XCTAssertEqual(try XCTUnwrap(store.watchSnapshot()).todos.count, 1)
        StubProtocol.handler = { _ in (503, "{}") }
        await store.refresh()
        XCTAssertEqual(store.watchSnapshot()?.todos.count, 1)
    }

    @MainActor
    func testWatchPatchAcceptedAfterAnotherRefresh() async throws {
        let store = makeStore()
        await store.refresh()
        let id = try XCTUnwrap(store.todos.first?.id)
        let started = expectation(description: "watch PATCH in flight")
        let gate = RefreshGate()
        let handler = StubProtocol.handler!
        StubProtocol.asyncHandler = { request in
            if request.httpMethod == "PATCH" { await gate.wait(started) }
            return handler(request)
        }
        let tick = Task { await store.handleWatchRequest(.setDone(id: id, done: true)) }
        await fulfillment(of: [started], timeout: 5)
        await store.refresh()
        await gate.release()
        await tick.value
        XCTAssertTrue(completed)
        XCTAssertTrue(store.pendingWatchCompletions.isEmpty)
        XCTAssertEqual(store.todos.first?.status, .completed)
        XCTAssertEqual(store.watchSnapshot()?.todos.first?.done, true)
    }

    @MainActor
    func testRefreshInFlightWhenTodoSaved() async throws {
        let store = makeStore()
        await store.refresh()
        let todo = try XCTUnwrap(store.todos.first)
        let started = expectation(description: "refresh waiting for its last endpoint")
        let gate = RefreshGate()
        let handler = StubProtocol.handler!
        StubProtocol.asyncHandler = { request in
            switch request.url?.path {
            case "/api/v1/digests": await gate.wait(started)
            case "/api/v1/todos": return (200, Fixtures.todoPage)
            default: break
            }
            return handler(request)
        }
        let refresh = Task { await store.refresh() }
        await fulfillment(of: [started], timeout: 5)
        await store.toggleDone(todo)
        XCTAssertEqual(store.todos.first?.status, .completed)
        await gate.release()
        await refresh.value
        XCTAssertEqual(store.todos.first?.status, .completed)
        XCTAssertEqual(store.today?.todos.first?.status, .completed)
        XCTAssertEqual(store.todos.count, 2)
        // The next read can legitimately supersede this device's edit.
        StubProtocol.asyncHandler = nil
        completed = false
        await store.refresh()
        XCTAssertEqual(store.todos.first?.status, .pending)
    }

    @MainActor
    func testEditorReceivesSaveFailureAndCanRetry() async throws {
        let store = makeStore()
        await store.refresh()
        let todo = try XCTUnwrap(store.todos.first)
        refusePatch = true
        do {
            try await store.saveEdits(todo, .complete())
            XCTFail("a rejected edit must keep the editor open")
        } catch {
            XCTAssertEqual(error as? APIError, .conflict)
        }
        XCTAssertEqual(store.todos.first?.status, .pending)
        refusePatch = false
        try await store.saveEdits(todo, .complete())
        XCTAssertEqual(store.today?.todos.first?.status, .completed)
    }

    @MainActor
    func testRejectedTokenImmediatelyMarksConnectionUnavailable() async {
        let store = makeStore()
        let handler = StubProtocol.handler!
        for code in [401, 403] {
            StubProtocol.handler = { _ in (code, "{}") }
            await store.refresh()
            XCTAssertFalse(store.online)
            XCTAssertEqual(store.authorizationIssue, AppStore.describe(code == 401 ? APIError.unauthorized : .forbidden))
            StubProtocol.handler = handler
            await store.refresh()
            XCTAssertTrue(store.online)
            XCTAssertNil(store.authorizationIssue)
        }
    }

    @MainActor
    func testStopSuspendsPendingChatPolling() async throws {
        let store = makeStore()
        let bot = try XCTUnwrap(store.bots.first(where: \.isAssistant))
        let pending = #"{"id":"reply","bot_id":"assistant","author":"bot","text":"","status":"pending","created_at":"2026-10-16T08:00:00Z","updated_at":"2026-10-16T08:00:00Z"}"#
        StubProtocol.handler = { _ in (200, #"{"items":[\#(pending)],"total":1}"#) }
        await store.loadChat(bot)
        XCTAssertTrue(store.isReplying(bot))
        store.stop()
        // A view's already scheduled load can also finish after the app went to the background.
        await store.loadChat(bot)
        let unwanted = expectation(description: "background chat poll")
        unwanted.isInverted = true
        StubProtocol.handler = { _ in
            unwanted.fulfill()
            return (503, "{}")
        }
        await fulfillment(of: [unwanted], timeout: 2)
        store.stop()
    }

    @MainActor
    func testSendReceiptAfterChatRefreshDoesNotDuplicateOrRevertReply() async throws {
        let store = makeStore()
        let bot = try XCTUnwrap(store.bots.first(where: \.isAssistant))
        let posted = expectation(description: "send accepted, receipt delayed")
        let gate = RefreshGate()
        let me = #"{"id":"m","bot_id":"assistant","author":"me","text":"问题","status":"ready","created_at":"2026-10-16T08:00:00Z","updated_at":"2026-10-16T08:00:00Z"}"#
        let pending = #"{"id":"r","bot_id":"assistant","author":"bot","text":"","status":"pending","created_at":"2026-10-16T08:00:01Z","updated_at":"2026-10-16T08:00:01Z"}"#
        let ready = pending.replacingOccurrences(of: #""status":"pending""#, with: #""status":"ready""#)
        StubProtocol.asyncHandler = { request in
            if request.httpMethod == "POST" {
                await gate.wait(posted)
                return (202, #"{"message":\#(me),"reply":\#(pending)}"#)
            }
            return (200, #"{"items":[\#(ready),\#(me)],"total":2}"#)
        }
        let sending = Task { await store.send("问题", to: bot) }
        await fulfillment(of: [posted], timeout: 5)
        await store.loadChat(bot)
        await gate.release()
        let sent = await sending.value
        XCTAssertTrue(sent)
        XCTAssertEqual(store.chats[bot.id]?.map(\.id), ["m", "r"])
        XCTAssertFalse(store.isReplying(bot))
        store.stop()
    }

    @MainActor
    func testFailedCardRefreshKeepsLastSuccessfulList() async {
        let store = makeStore()
        StubProtocol.handler = { _ in (200, #"[{"type":"text","title":"卡片","data":{"text":"示例"}}]"#) }
        await store.loadCards("plugin")
        let cached = store.cards["plugin"]
        XCTAssertEqual(cached?.count, 1)
        StubProtocol.handler = { _ in (503, "{}") }
        await store.loadCards("plugin")
        XCTAssertEqual(store.cards["plugin"], cached)
        StubProtocol.handler = { _ in (200, "[]") }
        await store.loadCards("plugin")
        XCTAssertEqual(store.cards["plugin"], [])
    }

    @MainActor
    func testOlderRefreshCannotOverwriteNewerData() async {
        let started = expectation(description: "first today request suspended")
        let gate = RefreshGate()
        let handler = StubProtocol.handler!
        StubProtocol.asyncHandler = { request in
            if request.url?.path == "/api/v1/today" {
                let first = await gate.isFirst()
                if first { await gate.wait(started) }
                return (200, Fixtures.today.replacingOccurrences(of: "今天暂无摘要。", with: first ? "OLD" : "NEW"))
            }
            return handler(request)
        }
        let store = makeStore()
        let older = Task { await store.refresh() }
        await fulfillment(of: [started], timeout: 5)
        await store.refresh()
        XCTAssertEqual(store.today?.summary, "NEW")
        let stamp = store.lastRefresh
        await gate.release()
        await older.value
        XCTAssertEqual(store.today?.summary, "NEW")
        XCTAssertEqual(store.lastRefresh, stamp)
    }

    @MainActor
    func testHealthReportsFailureDoesNotDiscardOtherRefreshResults() async {
        let handler = StubProtocol.handler!
        StubProtocol.handler = { request in
            request.url?.path == "/api/v1/health/reports" ? (500, "{}") : handler(request)
        }
        let store = makeStore()
        await store.refresh()
        XCTAssertNotNil(store.status)
        XCTAssertNotNil(store.today)
        XCTAssertEqual(store.todos.count, 1)
        XCTAssertFalse(store.plugins.isEmpty)
        XCTAssertTrue(store.online)
        XCTAssertTrue(store.settled)
    }

    @MainActor
    func testFailedTodayRefreshKeepsSnapshotTimestamp() async throws {
        let store = makeStore()
        await store.refresh()
        let stamp = try XCTUnwrap(store.todayUpdatedAt)
        let lastRefresh = store.lastRefresh
        let handler = StubProtocol.handler!
        StubProtocol.handler = { request in
            request.url?.path == "/api/v1/today" ? (500, "{}") : handler(request)
        }
        await store.refresh()
        XCTAssertEqual(store.todayUpdatedAt, stamp)
        let snapshot = WatchSnapshot.make(
            today: store.today, online: store.online, state: .idle,
            now: stamp.addingTimeInterval(86400), calendar: store.calendar, updatedAt: store.todayUpdatedAt)
        XCTAssertEqual(snapshot.generatedAt, stamp)
        StubProtocol.handler = { _ in (503, "{}") }
        let beforeOffline = store.lastRefresh
        await store.refresh()
        await store.refresh()
        XCTAssertEqual(store.lastRefresh, beforeOffline)
        XCTAssertEqual(store.todayUpdatedAt, stamp)
        XCTAssertFalse(store.online)
        XCTAssertNotNil(lastRefresh)
    }

    /// The iPhone said 「连不上 Mac」 whenever one refresh failed, e.g. right after waking up.
    @MainActor
    func testOneRefreshReachingNothingIsNotOfflineAndTheNextComesSoon() async {
        let store = makeStore()
        await store.refresh()
        XCTAssertEqual(AppStore.pollDelay(misses: store.misses, jobRunning: false), 30)
        let handler = StubProtocol.handler!
        StubProtocol.handler = { _ in (503, "{}") }
        await store.refresh()
        XCTAssertTrue(store.online)
        XCTAssertEqual(AppStore.pollDelay(misses: store.misses, jobRunning: false), 2)
        await store.refresh()
        XCTAssertFalse(store.online)
        XCTAssertEqual(AppStore.pollDelay(misses: store.misses, jobRunning: true), 10)
        StubProtocol.handler = handler
        await store.refresh()
        XCTAssertTrue(store.online)
        XCTAssertEqual(AppStore.pollDelay(misses: store.misses, jobRunning: true), 2)
    }

    func testOnlyASaveLostOnTheWayIsSentAgain() async throws {
        var calls = 0
        let value = try await AppStore.againOnTransport { () async throws -> Int in
            calls += 1
            if calls == 1 { throw APIError.transport("lost") }
            return 7
        }
        XCTAssertEqual(value, 7)
        XCTAssertEqual(calls, 2)
        calls = 0
        do {
            _ = try await AppStore.againOnTransport { () async throws -> Int in
                calls += 1
                throw APIError.conflict
            }
            XCTFail("a refusal is not sent again")
        } catch {}
        XCTAssertEqual(calls, 1)
    }

    /// 作业Bot's 「还没交」 lists the todo; ticking it reads that card again, so it leaves.
    @MainActor
    func testTickingATodoReadsTheCardsThatListItAgain() async {
        let handler = StubProtocol.handler!
        StubProtocol.handler = { [self] request in
            switch request.url!.path {
            case "/api/v1/plugins/homework/cards":
                let todos = completed ? "" : todoJSON
                return (200, "[{\"type\": \"todo_list\", \"title\": \"还没交\", \"data\": {\"todos\": [\(todos)]}}]")
            case "/api/v1/plugins/weather/cards":
                return (200, "[{\"type\": \"text\", \"title\": \"今日提醒\", \"data\": {\"text\": \"晴\"}}]")
            default: return handler(request)
            }
        }
        let store = makeStore()
        await store.refresh()
        await store.loadCards("homework")
        await store.loadCards("weather")
        guard case .todoList(let listed)? = store.cards["homework"]?.first?.content,
            let todo = listed.first
        else { return XCTFail("the homework card lists the todo") }
        StubProtocol.requests = []
        await store.toggleDone(todo)
        XCTAssertTrue(completed)
        XCTAssertNil(store.message)
        guard case .todoList(let after)? = store.cards["homework"]?.first?.content else {
            return XCTFail("the homework card is still there")
        }
        XCTAssertTrue(after.isEmpty)
        let paths = StubProtocol.requests.compactMap { $0.url?.path }
        XCTAssertFalse(paths.contains("/api/v1/plugins/weather/cards"))
    }
}

private actor RefreshGate {
    private var count = 0
    private var continuation: CheckedContinuation<Void, Never>?

    func isFirst() -> Bool {
        count += 1
        return count == 1
    }

    func wait(_ started: XCTestExpectation) async {
        await withCheckedContinuation {
            continuation = $0
            started.fulfill()
        }
    }

    func release() {
        continuation?.resume()
        continuation = nil
    }
}

final class ChatRefreshTests: XCTestCase {
    /// A line written on another device (or a Bot speaking first) shows on a page that is
    /// already open at the next refresh, and the timeline worked out before it is dropped.
    @MainActor
    func testRefreshReadsAnOpenChatWrittenElsewhere() async {
        let backend = MockBackend()
        let mac = AppStore(backend: backend)
        let phone = AppStore(backend: backend)
        let bot = Bot(id: Bot.assistantID, name: "助理Bot", kind: .assistant, look: StickerLook(sticker: nil))
        await mac.refresh()
        await mac.loadChat(bot)
        let lines = mac.chats[bot.id]?.count ?? 0
        let said = mac.messages(bot).count
        await phone.send("明天有什么要交的？", to: bot)
        await mac.refresh()
        XCTAssertEqual(mac.chats[bot.id]?.count, lines + 2)
        XCTAssertEqual(mac.messages(bot).count, said + 1)  // mine; the reply is still pending
    }
}
