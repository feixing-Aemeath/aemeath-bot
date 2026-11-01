import XCTest

@testable import AssistantCore

final class WatchSyncTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    let now = APIDateCoding.date(from: "2026-10-15T21:00:00+08:00")!

    func snapshot() -> WatchSnapshot {
        WatchSnapshot.make(
            today: MockData.today(now: now, calendar: calendar), online: true, state: .idle,
            now: now, calendar: calendar)
    }

    func testSnapshotFromToday() {
        let snapshot = snapshot()
        XCTAssertEqual(snapshot.todos.count, 3)
        XCTAssertEqual(snapshot.remaining, 3)
        XCTAssertTrue(snapshot.todos.contains { $0.overdue })
        XCTAssertTrue(snapshot.todos.allSatisfy { !$0.due.isEmpty && !$0.source.isEmpty })
        XCTAssertFalse(snapshot.summary.isEmpty)
        let empty = WatchSnapshot.make(today: nil, online: false, state: .offline, now: now, calendar: calendar)
        XCTAssertEqual(empty.remaining, 0)
        XCTAssertEqual(empty.state, .offline)
    }

    func testCoursesAndHealthFromTheCardsOfToday() throws {
        var today = MockData.today(now: now, calendar: calendar)
        XCTAssertNil(snapshot().courses)  // 课表 off: no page data
        XCTAssertNil(snapshot().health)
        today.cards += [
            Card(
                type: .list, title: "今天的课 · 推测",
                data: .object([
                    "items": .array([
                        .object(["title": .string("大学英语（1）"), "subtitle": .string("08:00–09:35")])
                    ])
                ])),
            Card(
                type: .stat, title: "今日准备度",
                data: .object([
                    "value": .number(7), "unit": .string("/ 10"), "label": .string("睡眠 8 · 恢复 6"),
                    "delta": .string("昨天 6"),
                ])),
        ]
        let lily = PluginMascot(sticker: "lily_veil", accent: "#8068B2")
        let plugins = [
            PluginView(
                id: "health", name: "健康", version: "1", icon: "heart", description: "",
                platforms: ["any"], permissions: [], schedule: nil, provides: ["cards"],
                configSchema: .object([:]), mascot: lily, enabled: true, config: .object([:]),
                lastRunAt: nil, state: .enabled, error: nil)
        ]
        let snapshot = WatchSnapshot.make(
            today: today, online: true, state: .idle, now: now, calendar: calendar, plugins: plugins)
        let courses = try XCTUnwrap(snapshot.courses)
        XCTAssertEqual(courses.title, "今天的课 · 推测")
        XCTAssertEqual(courses.items, [.init(title: "大学英语（1）", time: "08:00–09:35")])
        XCTAssertNil(courses.mascot)
        XCTAssertTrue(courses.estimated)
        XCTAssertEqual(courses.current(atMinute: 7 * 60), 0)  // before it: next
        XCTAssertEqual(courses.current(atMinute: 9 * 60), 0)  // during it
        XCTAssertNil(courses.current(atMinute: 10 * 60))  // after the last
        let health = try XCTUnwrap(snapshot.health)
        XCTAssertEqual(health.readiness, .init(value: "7", unit: "/ 10", label: "睡眠 8 · 恢复 6", delta: "昨天 6"))
        XCTAssertNil(health.sleep)
        XCTAssertEqual(health.mascot, lily)

        // A day without courses is a text card of the same title.
        today.cards[today.cards.count - 2] = Card(
            type: .text, title: "今天的课", data: .object(["text": .string("没有课。")]))
        let free = WatchSnapshot.make(today: today, online: true, state: .idle, now: now, calendar: calendar)
        XCTAssertEqual(free.courses?.items, [])

        // A snapshot from an App version before these pages still decodes.
        var old = try JSONSerialization.jsonObject(with: JSONEncoder.assistant().encode(snapshot)) as! [String: Any]
        old["courses"] = nil
        old["health"] = nil
        let data = try JSONSerialization.data(withJSONObject: old)
        let decoded = try JSONDecoder.assistant().decode(WatchSnapshot.self, from: data)
        XCTAssertEqual(decoded.todos, snapshot.todos)
        XCTAssertNil(decoded.courses)
    }

    func testTomorrowsCoursesOnceTodaysAreOver() throws {
        func card(_ title: String, _ courses: [(String, String)]) -> Card {
            courses.isEmpty
                ? Card(type: .text, title: title, data: .object(["text": .string("没有课。")]))
                : Card(
                    type: .list, title: title,
                    data: .object([
                        "style": .string("timeline"),
                        "items": .array(
                            courses.map { .object(["title": .string($0.0), "subtitle": .string($0.1)]) }),
                    ]))
        }
        func at(_ time: String) -> Date { APIDateCoding.date(from: "2026-10-\(time):00+08:00")! }
        var today = MockData.today(now: now, calendar: calendar)
        today.cards += [
            card("今天的课", [("大学英语（1）", "08:00–09:35"), ("高等数学I（A）", "15:55–17:30")]),
            card("明天的课 · 推测", [("线性代数", "10:00–11:35")]),
        ]
        let snapshot = WatchSnapshot.make(today: today, online: true, state: .idle, now: now, calendar: calendar)
        XCTAssertEqual(snapshot.tomorrowCourses?.items, [.init(title: "线性代数", time: "10:00–11:35")])
        XCTAssertTrue(snapshot.tomorrowCourses?.estimated ?? false)

        // During the day: today's.
        var page = try XCTUnwrap(snapshot.coursePage(at: at("15T16:00"), calendar: calendar))
        XCTAssertTrue(page.isToday)
        XCTAssertEqual(page.courses.items.count, 2)
        // After the last one: tomorrow's.
        page = try XCTUnwrap(snapshot.coursePage(at: at("15T17:31"), calendar: calendar))
        XCTAssertFalse(page.isToday)
        XCTAssertTrue(page.hadCoursesToday)
        XCTAssertEqual(page.courses.items.first?.title, "线性代数")
        // Next morning, before a new snapshot: they are today's.
        page = try XCTUnwrap(snapshot.coursePage(at: at("16T07:00"), calendar: calendar))
        XCTAssertTrue(page.isToday)
        XCTAssertEqual(page.courses.items.first?.title, "线性代数")
        // A snapshot two days old knows nothing about the day.
        XCTAssertNil(snapshot.coursePage(at: at("17T07:00"), calendar: calendar))

        // A day without courses shows tomorrow's at once.
        today.cards[today.cards.count - 2] = card("今天的课", [])
        let free = WatchSnapshot.make(today: today, online: true, state: .idle, now: now, calendar: calendar)
        page = try XCTUnwrap(free.coursePage(at: at("15T08:00"), calendar: calendar))
        XCTAssertFalse(page.isToday)
        XCTAssertFalse(page.hadCoursesToday)

        // Without tomorrow's card the day's last courses stay.
        today.cards.removeLast()
        let last = WatchSnapshot.make(today: today, online: true, state: .idle, now: now, calendar: calendar)
        page = try XCTUnwrap(last.coursePage(at: at("15T20:00"), calendar: calendar))
        XCTAssertTrue(page.isToday)
        XCTAssertEqual(page.courses.items, [])
    }

    func testWeatherOnTheAssistantsPage() throws {
        func forecast(_ title: String, _ value: String, _ label: String, _ symbol: String) -> Card {
            Card(
                type: .stat, title: title,
                data: .object([
                    "value": .string(value), "unit": .string("°C"), "label": .string(label),
                    "symbol": .string(symbol),
                ]))
        }
        func at(_ time: String) -> Date { APIDateCoding.date(from: "2026-10-\(time):00+08:00")! }
        var today = MockData.today(now: now, calendar: calendar)
        XCTAssertNil(snapshot().weather)
        today.cards += [
            // 健康's stat cards have no symbol and are not forecasts.
            Card(type: .stat, title: "昨晚睡眠", data: .object(["value": .string("7 小时")])),
            forecast("上海今天", "11–23", "晴 · 降水概率 0%", "sun.max.fill"),
            Card(
                type: .list, title: "出门提醒",
                data: .object([
                    "items": .array([
                        .object(["title": .string("不用带伞"), "subtitle": .string("降水概率 0%")]),
                        .object(["title": .string("外套或卫衣")]),
                    ])
                ])),
            forecast("明天", "12–24", "小雨 · 降水概率 70% · 带伞", "cloud.rain.fill"),
        ]
        let snapshot = WatchSnapshot.make(today: today, online: true, state: .idle, now: now, calendar: calendar)
        let weather = try XCTUnwrap(snapshot.weather)
        XCTAssertEqual(weather.place, "上海")
        XCTAssertEqual(
            weather.today, .init(temperatures: "11–23", unit: "°C", label: "晴 · 降水概率 0%", symbol: "sun.max.fill"))
        XCTAssertEqual(weather.advice, ["不用带伞", "外套或卫衣"])
        XCTAssertEqual(weather.tomorrow?.symbol, "cloud.rain.fill")

        let generated = snapshot.generatedAt
        var shown = try XCTUnwrap(weather.shown(at: at("15T08:00"), since: generated, calendar: calendar))
        XCTAssertTrue(shown.isToday)
        XCTAssertEqual(shown.day.temperatures, "11–23")
        XCTAssertEqual(shown.advice.count, 2)
        // In the evening, tomorrow's.
        shown = try XCTUnwrap(weather.shown(at: at("15T19:30"), since: generated, calendar: calendar))
        XCTAssertFalse(shown.isToday)
        XCTAssertEqual(shown.day.temperatures, "12–24")
        XCTAssertEqual(shown.advice, [])
        // Next morning before a new snapshot: what was tomorrow's is today's.
        shown = try XCTUnwrap(weather.shown(at: at("16T07:00"), since: generated, calendar: calendar))
        XCTAssertTrue(shown.isToday)
        XCTAssertEqual(shown.day.temperatures, "12–24")
        XCTAssertNil(weather.shown(at: at("17T07:00"), since: generated, calendar: calendar))
    }

    func testTickingOffBeforeTheIPhoneConfirms() {
        let snapshot = snapshot()
        let first = snapshot.todos[0].id
        let ticked = snapshot.setting(first, done: true)
        XCTAssertEqual(ticked.remaining, 2)
        XCTAssertTrue(ticked.todos[0].done)
        XCTAssertFalse(ticked.todos[0].overdue)
        XCTAssertEqual(ticked.setting(first, done: false).remaining, 3)
        XCTAssertEqual(snapshot.setting("unknown", done: true), snapshot)
    }

    func testSnapshotTravelsAsMessage() throws {
        let snapshot = snapshot()
        let message = try snapshot.message()
        XCTAssertNotNil(message[WatchSnapshot.key] as? Data)
        XCTAssertEqual(WatchSnapshot(message: message), snapshot)
        XCTAssertNil(WatchSnapshot(message: ["snapshot": Data("garbage".utf8)]))
        XCTAssertNil(WatchSnapshot(message: [:]))
    }

    func testRequests() {
        for request in [WatchRequest.refresh, .setDone(id: "t1", done: true), .setDone(id: "t2", done: false)] {
            XCTAssertEqual(WatchRequest(message: request.message), request)
        }
        XCTAssertNil(WatchRequest(message: ["request": "delete_everything"]))
        XCTAssertNil(WatchRequest(message: ["request": "set_done", "id": "t1"]))
        XCTAssertNil(WatchRequest(message: [:]))
    }

    func testComplicationData() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "test-\(UUID().uuidString)"))
        XCTAssertNil(ComplicationData.load(from: defaults))
        let data = ComplicationData(remaining: 4, updated: now)
        data.save(to: defaults)
        XCTAssertEqual(ComplicationData.load(from: defaults), data)
        // Without an App Group in the Info.plist (tests, previews) the standard defaults are used.
        XCTAssertEqual(ComplicationData.defaults(bundle: Bundle(for: WatchSyncTests.self)), .standard)
    }
}
