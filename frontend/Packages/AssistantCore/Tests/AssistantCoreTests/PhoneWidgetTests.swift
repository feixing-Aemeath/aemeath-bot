import XCTest

@testable import AssistantCore

final class PhoneWidgetTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    var now: Date { APIDateCoding.date(from: "2026-11-11T09:00:00+08:00")! }
    func item(_ id: String, due: Date? = nil, done: Bool = false) -> WatchSnapshot.Item {
        .init(
            id: id, title: id, due: "", overdue: false, source: "", done: done, dueAt: due,
            hasTime: due == nil ? nil : true)
    }
    func snapshot(_ items: [WatchSnapshot.Item] = []) -> WatchSnapshot {
        .init(
            generatedAt: now, online: true, state: .idle, summary: "", todos: items,
            courses: .init(title: "今天的课", items: [.init(title: "正在上的课", time: "08:00–09:35")], mascot: nil))
    }
    func testReusesWatchMomentRankingAndRoutesToCourses() {
        let data = snapshot([item("稍后交", due: now.addingTimeInterval(7200))])
        let content = PhoneWidgetContent(snapshot: data, now: now, calendar: calendar)
        XCTAssertEqual(content.title, data.moment(at: now, calendar: calendar).title)
        XCTAssertEqual(content.title, "正在上的课")
        XCTAssertEqual(content.destination, .bot("timetable"))
        let urgent = snapshot([item("十分钟后截止", due: now.addingTimeInterval(600))])
        XCTAssertEqual(PhoneWidgetContent(snapshot: urgent, now: now, calendar: calendar).title, "十分钟后截止")
    }
    func testTopThreeExcludesCompletedAndRecomputesOverdue() {
        let data = snapshot([
            item("完成", done: true), item("随时做"), item("明天", due: now.addingTimeInterval(86400)),
            item("已过期", due: now.addingTimeInterval(-600)), item("快截止", due: now.addingTimeInterval(600)),
        ])
        let content = PhoneWidgetContent(snapshot: data, now: now, calendar: calendar)
        XCTAssertEqual(content.remaining, 3)
        XCTAssertEqual(content.todos.map(\.id), ["已过期", "快截止", "随时做"])
        XCTAssertTrue(content.todos[0].overdue)
        XCTAssertFalse(content.todos[1].overdue)
    }
    func testCrossingMidnightDoesNotCallOldDataToday() {
        let data = snapshot([item("旧待办")])
        for date in [now.addingTimeInterval(16 * 3600), now.addingTimeInterval(2 * 86400)] {
            let content = PhoneWidgetContent(snapshot: data, now: date, calendar: calendar)
            XCTAssertTrue(content.stale)
            XCTAssertNil(content.remaining)
            XCTAssertTrue(content.todos.isEmpty)
            XCTAssertTrue(content.detail.contains("11月11日"))
        }
    }
    func testNoSnapshotAndRoundTripInAppGroup() throws {
        XCTAssertNil(PhoneWidgetContent(snapshot: nil, now: now, calendar: calendar).remaining)
        let suite = "widget-test-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        XCTAssertNil(PhoneWidgetSnapshot.load(from: defaults))
        PhoneWidgetSnapshot.save(snapshot(), to: defaults)
        XCTAssertEqual(PhoneWidgetSnapshot.load(from: defaults), snapshot())
        XCTAssertNil(WatchSnapshot.load(from: defaults))
    }
    func testSuccessfulFetchOfYesterdaysOverviewStillMarksSnapshotOld() {
        let old = Today(date: "2026-11-10", summary: "", todos: [], cards: [])
        let data = PhoneWidgetSnapshot.make(
            today: old, updatedAt: now, online: true, plugins: [], now: now, calendar: calendar)
        let content = PhoneWidgetContent(snapshot: data, now: now, calendar: calendar)
        XCTAssertTrue(content.stale)
        XCTAssertNil(content.remaining)
        XCTAssertTrue(content.detail.contains("11月10日"))
    }
    func testOlderSameDaySnapshotRemainsUsableAndChangesAtDeadline() {
        var data = snapshot([item("要交的材料", due: now.addingTimeInterval(60))])
        data.courses = nil
        let before = PhoneWidgetContent(snapshot: data, now: now, calendar: calendar)
        let after = PhoneWidgetContent(snapshot: data, now: now.addingTimeInterval(120), calendar: calendar)
        XCTAssertFalse(before.stale)
        XCTAssertFalse(before.todos[0].overdue)
        XCTAssertTrue(after.todos[0].overdue)
        XCTAssertTrue(data.changes(after: now, hours: 24, calendar: calendar).contains(now.addingTimeInterval(60)))
    }
}
