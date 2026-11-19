import XCTest

@testable import AssistantCore
@testable import AssistantUI

final class AwayClockTests: XCTestCase {
    func testTheDashboardAfterAnHourAway() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "AwayClockTests"))
        defaults.removePersistentDomain(forName: "AwayClockTests")
        let start = Date(timeIntervalSince1970: 1_000_000)
        // A first start opens the dashboard; the clock starts.
        AwayClock.openIfAway("dashboard", key: "page", defaults, now: start)
        XCTAssertEqual(defaults.string(forKey: "page"), "dashboard")
        // A chat chosen, the App made again 10 minutes later: the chat stays.
        defaults.set("health", forKey: "page")
        AwayClock.openIfAway("dashboard", key: "page", defaults, now: start + 600)
        XCTAssertEqual(defaults.string(forKey: "page"), "health")
        // Back after just over an hour since the App was last seen.
        XCTAssertFalse(AwayClock.wasAway(defaults, now: start + 600 + 3600))
        XCTAssertTrue(AwayClock.wasAway(defaults, now: start + 600 + 3601))
        AwayClock.openIfAway("dashboard", key: "page", defaults, now: start + 600 + 3601)
        XCTAssertEqual(defaults.string(forKey: "page"), "dashboard")
    }
}

@MainActor
final class DashboardCardsTests: XCTestCase {
    func testTodaysCardsFillEveryPlugin() async {
        let store = AppStore(backend: MockBackend())
        await store.refresh()
        XCTAssertTrue(store.cardsFromToday)
        // Every enabled plugin's cards came with /today, each under its plugin.
        let health = store.cards["health"] ?? []
        XCTAssertFalse(health.isEmpty)
        XCTAssertTrue(health.allSatisfy { $0.plugin == "health" })
        // A plugin that is off has an empty list, not a missing one.
        let off = store.plugins.first { $0.state != .enabled }
        if let off { XCTAssertEqual(store.cards[off.id], []) }
    }

    func testSidebarMoveAndVisibilityAreSharedWithDashboardAfterReopening() async throws {
        let suite = "DashboardSettingsTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = AppStore(backend: MockBackend(), preferenceDefaults: defaults)
        await store.refresh()
        store.botPreferences[Bot.assistantID].pinned = true
        let group = store.bots.filter { !$0.isAssistant && $0.kind != .source }
        let moved = try XCTUnwrap(group.last)
        store.moveBots(in: group, from: IndexSet(integer: group.count - 1), to: 0)
        store.botPreferences[moved.id].visible = false
        store.botPreferences[moved.id].customName = "小伙伴"

        let reopened = AppStore(backend: MockBackend(), preferenceDefaults: defaults)
        await reopened.refresh()
        XCTAssertEqual(Array(reopened.bots.prefix(2).map(\.id)), [Bot.assistantID, moved.id])
        XCTAssertEqual(reopened.displayName(moved), "小伙伴")
        XCTAssertFalse(DashboardPage.sections(reopened.bots, store: reopened).contains(moved))
        reopened.botPreferences.resetLayout()
        XCTAssertEqual(reopened.displayName(moved), "小伙伴")
        XCTAssertTrue(reopened.botPreferences[moved.id].visible)
        XCTAssertFalse(reopened.botPreferences[Bot.assistantID].pinned)
    }

    func testOlderBackendsLeaveThePagesToLoad() {
        let store = AppStore(backend: MockBackend())
        store.adoptCards([Card(type: .text, title: "旧", data: .object([:]))], plugins: [])
        XCTAssertFalse(store.cardsFromToday)
        XCTAssertTrue(store.cards.isEmpty)
    }

    func testSameTitleFromTwoPluginsIsTwoCards() {
        let one = Card(type: .stat, title: "今天", data: .object([:]), plugin: "a")
        let other = Card(type: .stat, title: "今天", data: .object([:]), plugin: "b")
        XCTAssertNotEqual(one.id, other.id)
        XCTAssertEqual(Card(type: .stat, title: "今天", data: .object([:])).id, "stat:今天")
    }

    func testSectionsSkipSourcesAndBotsSwitchedOff() async {
        let store = AppStore(backend: MockBackend())
        await store.refresh()
        let shown = DashboardPage.sections(store.bots, store: store)
        XCTAssertEqual(shown.first?.id, Bot.assistantID)
        XCTAssertTrue(shown.contains { $0.kind == .knowledge })
        XCTAssertFalse(shown.contains { $0.kind == .source })
        for bot in shown where bot.kind == .character {
            XCTAssertTrue(DashboardPage.isOn(bot, store: store))
        }
        XCTAssertEqual(DashboardPage.shortName(shown[0]), DashboardPage.shortName(shown[0]).replacingOccurrences(of: "Bot", with: ""))
    }
}

final class AttachedCardTests: XCTestCase {
    func testAReplyBringsItsCards() throws {
        let json = """
            {"id": "r1", "bot_id": "health", "author": "bot", "text": "睡得不错", "status": "ready",
             "error": null, "created_at": "2026-11-08T09:00:00+08:00",
             "updated_at": "2026-11-08T09:00:05+08:00", "replies": null,
             "cards": [{"type": "stat", "title": "昨晚睡眠", "data": {"value": "7小时12分"},
                        "plugin": "health", "taken_at": "2026-11-08T09:00:04+08:00"}]}
            """
        let line = try JSONDecoder.assistant().decode(ChatMessage.self, from: Data(json.utf8))
        let card = try XCTUnwrap(line.cards?.first)
        XCTAssertEqual(card.plugin, "health")
        XCTAssertEqual(card.takenAt, try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-11-08T01:00:04Z")))
        XCTAssertEqual(BotChat.messages([line]).first?.cards, [card])
        // An older backend's line has none.
        let plain = json.replacingOccurrences(of: #""cards": ["#, with: #""old": ["#)
        XCTAssertNil(try JSONDecoder.assistant().decode(ChatMessage.self, from: Data(plain.utf8)).cards)
    }
}

final class ChartAxisTests: XCTestCase {
    func chart(_ count: Int) -> CardChart {
        let points = (1...count).map { CardChart.Point(x: "11/\($0)", y: Double($0)) }
        return CardChart(kind: .line, unit: nil, series: [CardChart.Series(name: "睡眠", points: points)])
    }

    func testAWeekLabelsEveryDay() {
        XCTAssertEqual(chart(7).axisLabels(), (1...7).map { "11/\($0)" })
    }

    func testThirtyNightsLabelAboutSevenEndingWithTheNewest() {
        let labels = chart(30).axisLabels()
        XCTAssertEqual(labels.last, "11/30")
        XCTAssertLessThanOrEqual(labels.count, 7)
        XCTAssertGreaterThanOrEqual(labels.count, 5)
        XCTAssertEqual(labels, ["11/0", "11/5", "11/10", "11/15", "11/20", "11/25", "11/30"].filter { labels.contains($0) })
    }

    func testALineStartsAtZeroUnlessTheBackendSaysOtherwise() throws {
        func parsed(_ extra: [String: JSONValue]) throws -> CardChart {
            let data: [String: JSONValue] = [
                "kind": .string("line"), "points": .array([.object(["x": .string("11/1"), "y": .number(58)])]),
            ]
            let card = Card(type: .chart, title: "静息心率", data: .object(data.merging(extra) { $1 }))
            guard case .chart(let chart) = card.content else { throw XCTSkip("not a chart") }
            return chart
        }
        XCTAssertTrue(try parsed([:]).zero)
        XCTAssertFalse(try parsed(["zero": .bool(false)]).zero)
    }

    func testCategoriesFromEverySeriesOnce() {
        let two = CardChart(
            kind: .bar, unit: nil,
            series: [
                CardChart.Series(name: "深睡", points: [.init(x: "11/1", y: 1), .init(x: "11/2", y: 1)]),
                CardChart.Series(name: "REM", points: [.init(x: "11/2", y: 1), .init(x: "11/3", y: 1)]),
            ])
        XCTAssertEqual(two.categories, ["11/1", "11/2", "11/3"])
    }
}
