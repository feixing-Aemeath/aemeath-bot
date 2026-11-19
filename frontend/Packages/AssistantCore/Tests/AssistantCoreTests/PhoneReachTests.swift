import XCTest

@testable import AssistantCore

final class PhoneReachTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    var now: Date { APIDateCoding.date(from: "2026-11-11T21:00:00+08:00")! }
    func today(_ summary: String = "", cards: [Card] = []) -> Today {
        Today(date: "2026-11-11", summary: summary, todos: [], cards: cards)
    }
    func notices(
        _ input: ReachInput, prefs: ReachPreferences = .init(), ledger: ReachLedger = .init(), at date: Date? = nil
    ) -> [ReachNotice] {
        ReachPolicy.notices(input, preferences: prefs, ledger: ledger, now: date ?? now, calendar: calendar)
    }
    func report(_ id: String = "r", title: String? = "昨晚休息得不错") -> HealthReport {
        HealthReport(
            id: id, date: "2026-11-11", kind: .morning, status: .ready, title: title,
            body: "\n\n今天正常活动。", createdAt: now, updatedAt: now)
    }
    func question(_ id: String = "q", bot: String = "health") -> ChatMessage {
        ChatMessage(
            id: id, botID: bot, author: .bot, text: "今天感觉怎么样？", status: .ready,
            createdAt: now, updatedAt: now, replies: ["不错", "有点累"])
    }
    func weather(_ label: String) -> Card {
        Card(
            type: .stat, title: "明天",
            data: .object(["value": .string("8–15"), "unit": .string("°C"), "label": .string(label)]), plugin: "weather"
        )
    }
    func testDefaultsAndPersistenceOfIndividualSwitchesAndTime() throws {
        let suite = "reach-test-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        var prefs = ReachPreferences.load(defaults)
        XCTAssertTrue(prefs.morning && prefs.unsure && prefs.question)
        XCTAssertFalse(prefs.departure)
        XCTAssertEqual(prefs.hour * 60 + prefs.minute, 450)
        prefs[.unsure] = false
        prefs[.departure] = true
        prefs.hour = 8
        prefs.minute = 10
        prefs.save(defaults)
        XCTAssertEqual(ReachPreferences.load(defaults), prefs)
    }
    func testIndependentMorningBriefsDailyDedupAndBodyFallback() throws {
        let card = Card(
            type: .text, title: "今日提醒", data: .object(["text": .string("**天气**\n带把伞再出门。")]), plugin: "weather")
        let input = ReachInput(today: today(cards: [card]), reports: [report(title: "  ")])
        let first = notices(input)
        XCTAssertEqual(first.count, 2)
        XCTAssertEqual(Set(first.map(\.body)), ["今天正常活动。", "带把伞再出门。"])
        var ledger = ReachLedger()
        for notice in first { ledger.record(notice.id, at: now) }
        var changed = input
        changed.reports = [report("replacement", title: "新标题")]
        XCTAssertTrue(notices(changed, ledger: ledger).isEmpty)
    }
    func testLocalNamesOnNoticesKeepTheBotDestination() {
        var names = BotPreferences()
        names["health"].customName = "小伙伴"
        let notices = ReachPolicy.notices(
            ReachInput(today: nil, reports: [report()], chats: ["health": [question()]]),
            preferences: .init(), ledger: .init(), now: now, calendar: calendar, botPreferences: names)
        XCTAssertEqual(Set(notices.map(\.title)), ["小伙伴的晨间简报到了", "小伙伴想问问你"])
        XCTAssertTrue(notices.allSatisfy { $0.destination == .bot("health") })
    }

    func testMorningRejectsYesterdayWeeklyPendingFailedAndEmpty() {
        var old = report()
        old.date = "2026-11-10"
        var weekly = report()
        weekly.kind = .weekly
        var pending = report()
        pending.status = .pending
        var failed = report()
        failed.status = .failed
        var empty = report(title: nil)
        empty.body = "\n "
        XCTAssertTrue(notices(.init(today: nil, reports: [old, weekly, pending, failed, empty])).isEmpty)
    }
    func testUnsureOnlyNewItemsDespiteReorderAndDisappearReappear() {
        let input = ReachInput(today: today("**还不确定**\n- 地点待确认\n- 截止时间没看清\n**通知**\n- 放假通知"))
        // Two new items are one notification that names the first and counts the rest.
        let first = notices(input)
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(first[0].title, "有 2 件事想请你确认")
        XCTAssertEqual(first[0].body, "地点待确认（还有 1 件）")
        XCTAssertEqual(first[0].covers.count, 2)
        var ledger = ReachLedger()
        first.forEach { notice in ([notice.id] + notice.covers).forEach { ledger.record($0, at: now) } }
        let reordered = ReachInput(today: today("**还不确定**\n- 截止时间没看清\n- 地点待确认\n- 课程有冲突"))
        let later = notices(reordered, ledger: ledger)
        XCTAssertEqual(later.map(\.body), ["课程有冲突"])
        XCTAssertEqual(later.map(\.title), ["有一件事想请你确认"])
        XCTAssertTrue(notices(input, ledger: ledger).isEmpty)
    }
    func testWhatIsOnScreenIsRememberedNotAnnounced() throws {
        let brief = try XCTUnwrap(notices(.init(today: nil, reports: [report()])).first)
        let asked = try XCTUnwrap(notices(.init(today: nil, chats: ["health": [question()]])).first)
        var prefs = ReachPreferences()
        prefs.departure = true
        let weatherNotice = try XCTUnwrap(notices(.init(today: today(cards: [weather("小雨")])), prefs: prefs).first)
        // In the background everything shows.
        for notice in [brief, asked, weatherNotice] {
            XCTAssertFalse(ReachPolicy.staysSilent(notice, appActive: false, openPage: "health"))
        }
        // With the App open a brief is already on 仪表盘, whatever page is open.
        XCTAssertTrue(ReachPolicy.staysSilent(brief, appActive: true, openPage: "dashboard"))
        XCTAssertTrue(ReachPolicy.staysSilent(brief, appActive: true, openPage: nil))
        // A Bot's question is silent only on that Bot's own page.
        XCTAssertTrue(ReachPolicy.staysSilent(asked, appActive: true, openPage: "health"))
        XCTAssertFalse(ReachPolicy.staysSilent(asked, appActive: true, openPage: "dashboard"))
        // One set for tomorrow morning is always set.
        XCTAssertFalse(ReachPolicy.staysSilent(weatherNotice, appActive: true, openPage: "dashboard"))
    }
    func testBeforeItsTimeThisMorningHasItsOwnReminder() throws {
        var prefs = ReachPreferences()
        prefs.departure = true
        let early = try XCTUnwrap(APIDateCoding.date(from: "2026-11-11T02:00:00+08:00"))
        let todayCard = Card(
            type: .stat, title: "上海今天",
            data: .object(["value": .string("4–13"), "unit": .string("°C"), "label": .string("阴")]), plugin: "weather")
        let both = notices(.init(today: today(cards: [todayCard, weather("小雨 · 带伞")])), prefs: prefs, at: early)
        XCTAssertEqual(both.map(\.id), ["departure-2026-11-11", "departure-2026-11-12"])
        XCTAssertTrue(both[0].body.hasPrefix("4–13°C"))
        XCTAssertTrue(both[1].body.contains("带伞"))
        // After that time only tomorrow's is left to set.
        let evening = notices(.init(today: today(cards: [todayCard, weather("小雨 · 带伞")])), prefs: prefs)
        XCTAssertEqual(evening.map(\.id), ["departure-2026-11-12"])
    }
    func testYesterdaySummaryAndRegularNotificationsNeverNotify() {
        var old = today("**还不确定**\n- 明天再确认")
        old.date = "2026-11-10"
        XCTAssertTrue(notices(.init(today: old)).isEmpty)
        XCTAssertTrue(notices(.init(today: today("**通知**\n- 放假安排\n**新待办**\n- 交材料"))).isEmpty)
    }
    func testQuestionsAcrossBotsStableByIDAndOnlyLatestReady() {
        let input = ReachInput(
            today: nil, chats: ["health": [question()], "assistant": [question("a", bot: "assistant")]])
        let first = notices(input)
        XCTAssertEqual(first.count, 2)
        XCTAssertEqual(first.first?.replies, ["不错", "有点累"])
        var ledger = ReachLedger()
        first.forEach { ledger.record($0.id, at: now) }
        XCTAssertTrue(notices(input, ledger: ledger).isEmpty)
        var user = question("user")
        user.author = .me
        user.createdAt = now.addingTimeInterval(1)
        XCTAssertTrue(notices(.init(today: nil, chats: ["health": [question(), user]])).isEmpty)
        var pending = question("next")
        pending.status = .pending
        pending.createdAt = now.addingTimeInterval(1)
        XCTAssertTrue(notices(.init(today: nil, chats: ["health": [question(), pending]])).isEmpty)
    }
    func testEveryCategoryCanBeDisabled() {
        var prefs = ReachPreferences()
        ReachKind.allCases.forEach { prefs[$0] = false }
        let input = ReachInput(
            today: today("**还不确定**\n- 看不清", cards: [weather("带伞")]), reports: [report()],
            chats: ["health": [question()]])
        XCTAssertTrue(notices(input, prefs: prefs).isEmpty)
    }
    func testDepartureIsTomorrowAtChosenLocalTimeAndUpdatesSameID() throws {
        var prefs = ReachPreferences()
        prefs.departure = true
        prefs.hour = 6
        prefs.minute = 45
        let rain = try XCTUnwrap(notices(.init(today: today(cards: [weather("小雨 · 带伞 · 厚外套")])), prefs: prefs).first)
        XCTAssertEqual(DueText.format(try XCTUnwrap(rain.fireAt), "yyyy-MM-dd HH:mm", calendar), "2026-11-12 06:45")
        XCTAssertTrue(rain.body.contains("厚外套"))
        let sunny = try XCTUnwrap(notices(.init(today: today(cards: [weather("晴 · 不用带伞")])), prefs: prefs).first)
        XCTAssertEqual(rain.id, sunny.id)
        XCTAssertNotEqual(rain.body, sunny.body)
        var ledger = ReachLedger()
        ledger.record(rain.id, at: now)
        XCTAssertEqual(notices(.init(today: today(cards: [weather("晴")])), prefs: prefs, ledger: ledger).count, 1)
    }
    func testDepartureDoesNotReuseTodaysAdviceOrOldWeather() {
        var prefs = ReachPreferences()
        prefs.departure = true
        let card = Card(
            type: .list, title: "出门提醒", data: .object(["items": .array([.object(["title": .string("不用带伞")])])]),
            plugin: "weather")
        XCTAssertTrue(notices(.init(today: today(cards: [card])), prefs: prefs).isEmpty)
        var old = today(cards: [weather("带伞")])
        old.date = "2026-11-10"
        XCTAssertTrue(notices(.init(today: old), prefs: prefs).isEmpty)
    }
    func testReplyRequiresSameUnansweredQuestionAndValidIndex() {
        XCTAssertEqual(ReachPolicy.reply(1, questionID: "q", chat: [question()], now: now, calendar: calendar), "有点累")
        XCTAssertNil(ReachPolicy.reply(-1, questionID: "q", chat: [question()], now: now, calendar: calendar))
        XCTAssertNil(ReachPolicy.reply(2, questionID: "q", chat: [question()], now: now, calendar: calendar))
        XCTAssertNil(ReachPolicy.reply(0, questionID: "old", chat: [question()], now: now, calendar: calendar))
        XCTAssertNil(
            ReachPolicy.reply(
                0, questionID: "q", chat: [question()], now: now.addingTimeInterval(86400), calendar: calendar))
    }
    func testOfferedReplyMustStillBeAnOptionForThatQuestion() {
        XCTAssertEqual(
            ReachPolicy.reply("有点累", questionID: "q", chat: [question()], now: now, calendar: calendar), "有点累")
        XCTAssertNil(ReachPolicy.reply("删除全部", questionID: "q", chat: [question()], now: now, calendar: calendar))
        XCTAssertNil(ReachPolicy.reply("不错", questionID: "old", chat: [question()], now: now, calendar: calendar))
    }
    func testLedgerSurvivesRelaunchAndDropsExpiredEntries() throws {
        let suite = "ledger-test-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        var ledger = ReachLedger()
        ledger.record("old", at: now.addingTimeInterval(-40 * 86400))
        ledger.record("new", at: now)
        ledger.save(defaults, key: "ledger")
        let reloaded = ReachLedger.load(defaults, key: "ledger")
        XCTAssertTrue(reloaded.contains("new"))
        XCTAssertFalse(reloaded.contains("old"))
        XCTAssertEqual(ReachPolicy.identity("场地  未定"), ReachPolicy.identity("场地\n未定"))
        XCTAssertNotEqual(ReachPolicy.identity("场地未定"), ReachPolicy.identity("时间未定"))
    }
    func testDestinationsRoundTripIncludingUnicodeAndRejectUnrelatedURLs() {
        for destination in [PhoneDestination.dashboard, .bot("health"), .bot("课程 & 活动")] {
            XCTAssertEqual(PhoneDestination(url: destination.url), destination)
        }
        XCTAssertNil(PhoneDestination(url: URL(string: "https://example.com/open")!))
        XCTAssertNil(PhoneDestination(url: URL(string: "aemeath://wrong")!))
    }
}

final class ReachRefreshTests: XCTestCase {
    func testRefreshReadsUnopenedBotsAndSurvivesOneEndpointFailure() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        let session = URLSession(configuration: config)
        defer {
            session.invalidateAndCancel()
            StubProtocol.handler = nil
        }
        StubProtocol.requests = []
        StubProtocol.asyncHandler = nil
        StubProtocol.handler = { request in
            let path = request.url!.path
            if path == "/api/v1/today" { return (200, Fixtures.today) }
            if path == "/api/v1/plugins" { return (200, Fixtures.plugins) }
            if path == "/api/v1/health/reports" { return (500, "") }
            if path.contains("/bots/") { return (200, "{\"items\":[],\"total\":0,\"next_before\":null}") }
            XCTFail("Unexpected endpoint: \(path)")
            return (500, "")
        }
        let backend = LiveBackend(
            client: APIClient(
                baseURL: URL(string: "https://example.invalid")!, tokens: StaticTokenProvider("test"), session: session)
        )
        let loaded = try await ReachRefresh.load(backend: backend)
        XCTAssertEqual(loaded.input.today?.date, "2026-10-16")
        XCTAssertNotNil(loaded.input.chats["assistant"])
        XCTAssertNotNil(loaded.input.chats["knowledge"])
        XCTAssertNil(loaded.input.chats["feishu"])
        XCTAssertTrue(loaded.input.reports.isEmpty)
    }

    func testCancelledRefreshDoesNotReturnPublishableData() async {
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return try await ReachRefresh.load(backend: MockBackend())
        }
        do {
            _ = try await task.value
            XCTFail("Cancelled refresh must not publish")
        } catch is CancellationError {
        } catch { XCTFail("Unexpected error: \(error)") }
    }
}
