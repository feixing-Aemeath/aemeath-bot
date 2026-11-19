import XCTest

@testable import AssistantCore

/// What leads the watch's first page, and when it changes. Tuesday 2026-10-27 in Shanghai.
final class WatchMomentTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)

    func at(_ time: String, day: Int = 27) -> Date {
        APIDateCoding.date(from: "2026-10-\(day)T\(time):00+08:00")!
    }

    func todo(
        _ id: String, _ title: String = "交作业", due: Date? = nil, hasTime: Bool? = nil, done: Bool = false,
        source: String = "QQ · 软件工程2班 · 李老师"
    ) -> WatchSnapshot.Item {
        WatchSnapshot.Item(
            id: id, title: title, due: "", overdue: false, source: source, done: done, dueAt: due, hasTime: hasTime)
    }

    let today = WatchSnapshot.Courses(
        title: "今天的课",
        items: [
            .init(title: "大学英语", time: "08:00–09:35"), .init(title: "高等数学", time: "09:55–12:20"),
            .init(title: "数据结构", time: "14:00–15:35"), .init(title: "社团例会", time: "19:00–20:00", detail: "自己的日程"),
        ], mascot: nil)
    let tomorrow = WatchSnapshot.Courses(
        title: "明天的课 · 推测", items: [.init(title: "线性代数", time: "08:00–09:35")], mascot: nil)
    let health = WatchSnapshot.Health(
        readiness: .init(value: "8.2", unit: "/ 10", label: "夜间 HRV 7.9 · 睡眠 9.0", delta: "昨天 7.6"),
        sleep: .init(value: "7小时12分", label: "23:20 入睡 · 06:50 醒来"), mascot: nil)
    let weather = WatchSnapshot.Weather(
        place: "上海", today: .init(temperatures: "9–21", unit: "°C", label: "多云 · 降水概率 10%", symbol: "cloud.sun.fill"),
        advice: ["不用带伞", "外套或卫衣"],
        tomorrow: .init(temperatures: "12–18", unit: "°C", label: "小雨 · 降水概率 70% · 要带伞", symbol: "cloud.drizzle.fill"))

    func snapshot(
        todos: [WatchSnapshot.Item] = [], courses: WatchSnapshot.Courses? = nil, tomorrow: WatchSnapshot.Courses? = nil,
        health: WatchSnapshot.Health? = nil, weather: WatchSnapshot.Weather? = nil, generated: Date? = nil
    ) -> WatchSnapshot {
        WatchSnapshot(
            generatedAt: generated ?? at("06:30"), online: true, state: .idle, summary: "", todos: todos, courses: courses,
            tomorrowCourses: tomorrow, health: health, weather: weather)
    }

    func testAnOldSnapshotDoesNotSayTodayIsFree() {
        XCTAssertEqual(snapshot().moment(at: at("10:00"), calendar: calendar).title, "今天没有安排")
        XCTAssertEqual(snapshot().moment(at: at("10:00", day: 30), calendar: calendar).title, "还没收到今天的安排")
    }

    func testAClassGoingOn() {
        let moment = snapshot(courses: today).moment(at: at("10:42"), calendar: calendar)
        XCTAssertEqual(moment.kind, .inClass)
        XCTAssertEqual(moment.bot, .courses)
        XCTAssertEqual(moment.kicker, "正在上课")
        XCTAssertEqual(moment.title, "高等数学")
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "98", unit: "分钟"))
        XCTAssertEqual(moment.detail, "12:20 下课")
        XCTAssertEqual(moment.progress ?? 0, 47.0 / 145, accuracy: 0.001)
        XCTAssertNil(moment.todo)
        XCTAssertEqual(moment.lines.map(\.text), ["数据结构", "社团例会"])
        XCTAssertEqual(moment.lines.first?.time, "14:00")
        XCTAssertEqual(moment.lines.first?.page, .courses)
    }

    func testSomethingOfOnesOwnIsNotAClass() {
        let moment = snapshot(courses: today).moment(at: at("19:30"), calendar: calendar)
        XCTAssertEqual(moment.kicker, "正在进行")
        XCTAssertEqual(moment.detail, "20:00 结束")
    }

    func testTheNextClassLeadsHalfAnHourBefore() {
        var moment = snapshot(courses: today).moment(at: at("13:45"), calendar: calendar)
        XCTAssertEqual(moment.kind, .nextClass)
        XCTAssertEqual(moment.kicker, "下一节课")
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "15", unit: "分钟"))
        XCTAssertEqual(moment.detail, "14:00 上课")
        XCTAssertEqual(moment.progress ?? 0, 0.5, accuracy: 0.001)
        // A todo that can be done now beats a class hours away, which stays on the line under it.
        let waiting = snapshot(todos: [todo("gym", due: at("00:00"), hasTime: false)], courses: today)
        moment = waiting.moment(at: at("12:40"), calendar: calendar)
        XCTAssertEqual(moment.kind, .todo)
        XCTAssertEqual(moment.lines.first?.text, "数据结构")
        // With nothing to do, the class leads however far it is.
        moment = snapshot(courses: today).moment(at: at("12:40"), calendar: calendar)
        XCTAssertEqual(moment.kind, .nextClass)
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "80", unit: "分钟"))
        XCTAssertNil(moment.progress)
    }

    func testADeadlineWithinTheHourBeatsTheClass() {
        let urgent = todo("a", "交表", due: at("11:30"), hasTime: true)
        let moment = snapshot(todos: [urgent], courses: today).moment(at: at("10:42"), calendar: calendar)
        XCTAssertEqual(moment.kind, .deadline)
        XCTAssertTrue(moment.urgent)
        XCTAssertEqual(moment.kicker, "快截止了")
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "48", unit: "分钟"))
        XCTAssertEqual(moment.detail, "11:30 截止")
        XCTAssertEqual(moment.todo?.id, "a")
        XCTAssertEqual(moment.note, "软件工程2班")
        XCTAssertEqual(moment.bot, .assistant)
        // The class going on is shown under it, with when it ends.
        XCTAssertEqual(moment.lines.first?.time, "到 12:20")
        XCTAssertEqual(moment.lines.first?.text, "高等数学")
    }

    func testTheOrderAmongDeadlinesAndWhatIsLate() {
        let late = todo("late", "补交材料", due: at("12:00", day: 25), hasTime: true)
        let soon = todo("soon", "今晚的", due: at("15:00"), hasTime: true)
        let far = todo("far", "很晚的", due: at("22:00"), hasTime: true)
        let plain = todo("plain", "没时间的", due: at("00:00"), hasTime: false)
        // Three hours or closer: the deadline; further: what is already late first.
        var moment = snapshot(todos: [late, soon, far]).moment(at: at("12:30"), calendar: calendar)
        XCTAssertEqual(moment.todo?.id, "soon")
        XCTAssertFalse(moment.urgent)
        XCTAssertEqual(moment.kicker, "今天截止")
        moment = snapshot(todos: [late, far]).moment(at: at("12:30"), calendar: calendar)
        XCTAssertEqual(moment.kind, .overdue)
        XCTAssertEqual(moment.todo?.id, "late")
        XCTAssertTrue(moment.urgent)
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "2", unit: "天"))
        XCTAssertEqual(moment.detail, "10月25日 截止")
        XCTAssertEqual(moment.progress, 1)
        // A far deadline beats a todo without a time.
        moment = snapshot(todos: [plain, far]).moment(at: at("12:30"), calendar: calendar)
        XCTAssertEqual(moment.todo?.id, "far")
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "9.5", unit: "小时"))
        moment = snapshot(todos: [plain]).moment(at: at("12:30"), calendar: calendar)
        XCTAssertEqual(moment.kind, .todo)
        XCTAssertEqual(moment.kicker, "今天要做")
        XCTAssertNil(moment.figure)
        // Today's list only has today's deadlines, but the words stay true if another slips in.
        let later = todo("later", due: at("09:00", day: 28), hasTime: true)
        moment = snapshot(todos: [later]).moment(at: at("12:30"), calendar: calendar)
        XCTAssertEqual(moment.kicker, "之后截止")
        XCTAssertEqual(moment.detail, "10月28日 09:00 截止")
        // Ticked off, it is out of the running.
        let done = snapshot(todos: [todo("x", due: at("11:30"), hasTime: true, done: true)], courses: today)
        XCTAssertEqual(done.moment(at: at("10:42"), calendar: calendar).kind, .inClass)
    }

    func testMorningReadiness() {
        let waking = snapshot(
            todos: [todo("late", due: at("12:00", day: 25), hasTime: true)], courses: today, health: health, generated: at("06:50"))
        var moment = waking.moment(at: at("07:10"), calendar: calendar)
        XCTAssertEqual(moment.kind, .readiness)
        XCTAssertEqual(moment.bot, .health)
        XCTAssertEqual(moment.title, "状态很好")
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "8.2", unit: "/ 10"))
        XCTAssertEqual(moment.detail, "比昨天高 0.6")
        XCTAssertEqual(moment.progress ?? 0, 0.82, accuracy: 0.001)
        XCTAssertEqual(moment.lines.first?.text, "睡了 7小时12分")
        // Half an hour before the first class, the class.
        XCTAssertEqual(waking.moment(at: at("07:35"), calendar: calendar).kind, .nextClass)
        // After ten it no longer leads.
        XCTAssertEqual(waking.moment(at: at("10:10"), calendar: calendar).kind, .inClass)
        // Yesterday's score does not lead today.
        moment = snapshot(courses: nil, health: health, generated: at("20:00", day: 26)).moment(at: at("07:10"), calendar: calendar)
        XCTAssertNotEqual(moment.kind, .readiness)
        // Nor does one that is not a number.
        let unknown = WatchSnapshot.Health(readiness: .init(value: "数据不足"), sleep: nil, mascot: nil)
        XCTAssertNotEqual(snapshot(health: unknown).moment(at: at("07:10"), calendar: calendar).kind, .readiness)
    }

    func testTomorrowFromTheEvening() {
        let over = snapshot(courses: today, tomorrow: tomorrow, weather: weather, generated: at("18:00"))
        var moment = over.moment(at: at("20:16"), calendar: calendar)
        XCTAssertEqual(moment.kind, .tomorrow)
        XCTAssertEqual(moment.kicker, "明天第一节")
        XCTAssertEqual(moment.title, "线性代数")
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "08:00"))
        XCTAssertEqual(moment.detail, "按上周课表推测")
        // A free afternoon: a clear day, with tomorrow under it.
        let free = WatchSnapshot.Courses(title: "今天的课", items: [], mascot: nil)
        let clear = snapshot(courses: free, tomorrow: tomorrow, weather: weather, generated: at("13:00"))
        moment = clear.moment(at: at("14:30"), calendar: calendar)
        XCTAssertEqual(moment.kind, .clear)
        XCTAssertEqual(moment.title, "今天没有安排")
        XCTAssertEqual(moment.kicker, "10月27日 周二")
        XCTAssertEqual(moment.figure, WatchMoment.Figure(value: "9–21°"))
        XCTAssertEqual(moment.symbol, "cloud.sun.fill")
        XCTAssertEqual(moment.lines.first?.time, "明天 08:00")
    }

    func testAClearDaySaysWhy() {
        let ticked = snapshot(todos: [todo("a", done: true)], courses: WatchSnapshot.Courses(title: "今天的课", items: [], mascot: nil))
        XCTAssertEqual(ticked.moment(at: at("14:30"), calendar: calendar).title, "都做完了")
        let classes = WatchSnapshot.Courses(title: "今天的课", items: [.init(title: "英语", time: "08:00–09:35")], mascot: nil)
        XCTAssertEqual(snapshot(courses: classes).moment(at: at("14:30"), calendar: calendar).title, "今天的课上完了")
        XCTAssertEqual(snapshot().moment(at: at("14:30"), calendar: calendar).title, "今天没有安排")
    }

    func testFiguresSayHowLong() {
        let start = at("10:00")
        func figure(_ minutes: Double) -> String { WatchFigures.until(start.addingTimeInterval(minutes * 60), from: start).text }
        XCTAssertEqual(figure(0.5), "<1 分钟")
        XCTAssertEqual(figure(1), "1 分钟")
        XCTAssertEqual(figure(1.2), "2 分钟")
        XCTAssertEqual(figure(99), "99 分钟")
        XCTAssertEqual(figure(100), "1.5 小时")
        XCTAssertEqual(figure(120), "2 小时")
        XCTAssertEqual(figure(160), "2.5 小时")
        XCTAssertEqual(figure(60 * 50), "2 天")
        XCTAssertEqual(WatchFigures.until(start, from: start).text, "0 分钟")

        func since(_ time: Date, hasTime: Bool) -> String { WatchFigures.since(time, hasTime: hasTime, to: start, calendar: calendar).text }
        XCTAssertEqual(since(at("09:20"), hasTime: true), "40 分钟")
        XCTAssertEqual(since(at("06:00"), hasTime: true), "4 小时")
        XCTAssertEqual(since(at("12:00", day: 25), hasTime: true), "2 天")
        // A date-only deadline yesterday is one day late, whatever the hour.
        XCTAssertEqual(since(at("00:00", day: 26), hasTime: false), "1 天")
    }

    func testTheListPutsLateFirstAndKeepsAPlaceForTicked() {
        let items = [
            todo("plain", due: at("00:00"), hasTime: false), todo("evening", due: at("22:00"), hasTime: true),
            todo("late", due: at("12:00", day: 25), hasTime: true), todo("none"), todo("noon", due: at("12:00"), hasTime: true),
        ]
        let list = snapshot(todos: items)
        XCTAssertEqual(list.orderedTodos(at: at("10:00"), calendar: calendar).map(\.id), ["late", "noon", "evening", "plain", "none"])
        // Ticking does not move a row under the finger.
        let ticked = list.setting("noon", done: true)
        XCTAssertEqual(ticked.orderedTodos(at: at("10:00"), calendar: calendar).map(\.id), ["late", "noon", "evening", "plain", "none"])
        // Past its time a deadline becomes late.
        XCTAssertEqual(list.orderedTodos(at: at("13:00"), calendar: calendar).first?.id, "late")
        XCTAssertEqual(list.orderedTodos(at: at("13:00"), calendar: calendar)[1].id, "noon")
        XCTAssertTrue(list.isOverdue(items[4], at: at("13:00"), calendar: calendar))
        XCTAssertFalse(list.isOverdue(items[4], at: at("10:00"), calendar: calendar))
    }

    func testDeadlinesAreWordedAfreshHoursLater() {
        let item = todo("a", due: at("17:00"), hasTime: true)
        let list = snapshot(todos: [item])
        XCTAssertEqual(list.dueWords(of: item, at: at("10:00"), calendar: calendar), "今天 17:00")
        XCTAssertEqual(list.dueWords(of: item, at: at("18:00"), calendar: calendar), "已逾期 · 今天 17:00")
        XCTAssertEqual(list.dueWords(of: item, at: at("09:00", day: 28), calendar: calendar), "已逾期 · 10月27日 17:00")
        // Without the deadline (a snapshot from an older iPhone build) the words it came with stay.
        let old = WatchSnapshot.Item(id: "b", title: "x", due: "今天 17:00", overdue: true, source: "", done: false)
        XCTAssertEqual(list.dueWords(of: old, at: at("10:00"), calendar: calendar), "今天 17:00")
        XCTAssertTrue(snapshot(todos: [old]).isOverdue(old, at: at("10:00"), calendar: calendar))
        XCTAssertEqual(snapshot(todos: [old]).moment(at: at("12:40"), calendar: calendar).kind, .overdue)
    }

    func testClassesAreOnTheCalendar() {
        let snapshot = snapshot(courses: today, tomorrow: tomorrow, generated: at("07:00"))
        let current = snapshot.currentClass(at: at("10:42"), calendar: calendar)
        XCTAssertEqual(current?.title, "高等数学")
        XCTAssertEqual(current?.isRunning(at: at("10:42")), true)
        XCTAssertEqual(current?.start, at("09:55"))
        XCTAssertEqual(current?.end, at("12:20"))
        // After the last one, tomorrow's, on tomorrow's date.
        let next = snapshot.currentClass(at: at("20:30"), calendar: calendar)
        XCTAssertEqual(next?.title, "线性代数")
        XCTAssertEqual(next?.isToday, false)
        XCTAssertEqual(next?.start, at("08:00", day: 28))
        XCTAssertEqual(next?.estimated, true)
        XCTAssertNil(self.snapshot().currentClass(at: at("10:42"), calendar: calendar))
    }

    func testComplicationsChangeAtTheEdgesOfTheDay() {
        let snapshot = snapshot(
            todos: [todo("a", due: at("17:00"), hasTime: true), todo("b", due: at("21:30"), hasTime: true, done: true)],
            courses: today, tomorrow: tomorrow, generated: at("07:00"))
        let changes = snapshot.changes(after: at("10:42"), hours: 24, calendar: calendar)
        XCTAssertEqual(changes, changes.sorted())
        XCTAssertEqual(Set(changes).count, changes.count)
        XCTAssertTrue(changes.allSatisfy { $0 > at("10:42") && $0 <= at("10:42", day: 28) })
        // The class's end, the half hour before the next, its start, its end.
        for edge in [at("12:20"), at("13:30"), at("14:00"), at("15:35")] { XCTAssertTrue(changes.contains(edge), "\(edge)") }
        // Three hours and one hour before the deadline, and the deadline.
        for edge in [at("14:00"), at("16:00"), at("17:00")] { XCTAssertTrue(changes.contains(edge), "\(edge)") }
        // A ticked-off one no longer has any.
        XCTAssertFalse(changes.contains(at("21:30")))
        // Midnight, the morning window, tomorrow's class.
        for edge in [at("00:00", day: 28), at("05:00", day: 28), at("10:00", day: 28), at("08:00", day: 28)] {
            XCTAssertTrue(changes.contains(edge), "\(edge)")
        }
        // What the moment says changes across such an edge.
        XCTAssertEqual(snapshot.moment(at: at("13:29"), calendar: calendar).kind, .deadline)
        XCTAssertEqual(snapshot.moment(at: at("13:30"), calendar: calendar).kind, .nextClass)
    }

    func testReadinessInWordsAndParts() {
        let stat = WatchSnapshot.Health.Stat(
            value: "6.4", unit: "/ 10", label: "夜间 HRV 7.9 · 静息心率 昨晚没有 HRV · 睡眠 9.0 · 你记的健身 App 7 · 体感 6", delta: "昨天 7.6")
        XCTAssertEqual(stat.score, 6.4)
        XCTAssertEqual(stat.band, "状态不错")
        XCTAssertEqual(stat.change, "比昨天低 1.2")
        XCTAssertEqual(stat.parts, [.init(name: "HRV", score: 7.9), .init(name: "睡眠", score: 9)])

        XCTAssertEqual(WatchSnapshot.Health.Stat(value: "8", delta: "昨天 8").change, "和昨天一样")
        XCTAssertEqual(WatchSnapshot.Health.Stat(value: "8", delta: "比近 30 天多 18 分").change, "比近 30 天多 18 分")
        XCTAssertNil(WatchSnapshot.Health.Stat(value: "8").change)
        XCTAssertEqual(WatchSnapshot.Health.Stat(value: "4.2").band, "状态一般")
        XCTAssertEqual(WatchSnapshot.Health.Stat(value: "3.9").band, "状态偏低")
        XCTAssertNil(WatchSnapshot.Health.Stat(value: "数据不足").band)
        XCTAssertNil(WatchSnapshot.Health.Stat(value: "数据不足").score)
    }

    func testSleepSpan() {
        var stat = WatchSnapshot.Health.Stat(value: "7小时12分", label: "23:20 入睡 · 06:50 醒来")
        XCTAssertEqual(stat.sleepSpan?.start, "23:20")
        XCTAssertEqual(stat.sleepSpan?.end, "06:50")
        stat.label = "主睡眠 6小时（23:40–06:10） · 小睡 1 次 30分"
        XCTAssertEqual(stat.sleepSpan?.start, "23:40")
        XCTAssertEqual(stat.sleepSpan?.end, "06:10")
        stat.label = "手表没戴，或者数据还没从 iPhone 上传"
        XCTAssertNil(stat.sleepSpan)
    }

    func testWeatherInALine() {
        let shown = WatchSnapshot.Weather.Shown(
            day: .init(temperatures: "9–21", unit: "°C", label: "多云 · 降水概率 10%", symbol: nil), isToday: true, advice: ["不用带伞", "外套或卫衣"])
        XCTAssertEqual(shown.line, "9–21° · 不用带伞")
        let evening = WatchSnapshot.Weather.Shown(
            day: .init(temperatures: "12–18", unit: "°C", label: "小雨 · 降水概率 70% · 要带伞", symbol: nil), isToday: false, advice: [])
        XCTAssertEqual(evening.line, "明天 12–18° · 小雨 · 要带伞")
    }

    func testPagesOpenFromLinks() {
        for page in WatchPage.allCases { XCTAssertEqual(WatchPage(url: page.url), page) }
        XCTAssertNil(WatchPage(url: URL(string: "aemeath-watch://finance")!))
        XCTAssertNil(WatchPage(url: URL(string: "https://example.com/now")!))
    }

    #if DEBUG
        func testMockStatesAreWhatTheyClaim() throws {
            func moment(_ mock: WatchMock) throws -> WatchMoment {
                let now = mock.now(on: at("12:00"), calendar: calendar)
                return try XCTUnwrap(mock.snapshot(now: now, calendar: calendar)).moment(at: now, calendar: calendar)
            }
            XCTAssertEqual(try moment(.inClass).kind, .inClass)
            XCTAssertEqual(try moment(.soon).kind, .nextClass)
            XCTAssertEqual(try moment(.deadline).kind, .deadline)
            XCTAssertEqual(try moment(.overdue).kind, .overdue)
            XCTAssertEqual(try moment(.morning).kind, .readiness)
            XCTAssertEqual(try moment(.classesOver).kind, .tomorrow)
            XCTAssertEqual(try moment(.allDone).title, "都做完了")
            XCTAssertEqual(try moment(.free).title, "今天没有安排")
            XCTAssertEqual(try moment(.todo).kind, .todo)
            XCTAssertFalse(try XCTUnwrap(WatchMock.macOffline.snapshot(now: at("10:42"), calendar: calendar)).online)
            XCTAssertNil(WatchMock.noData.snapshot(now: at("10:42"), calendar: calendar))
            XCTAssertEqual(WatchMock(argument: "1"), .inClass)
            XCTAssertNil(WatchMock(argument: "nonsense"))
        }
    #endif
}
