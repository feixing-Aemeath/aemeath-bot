import XCTest

@testable import AssistantCore

final class DashboardGlanceTests: XCTestCase {
    func testAShortNoteIsTheNotesFirstPart() {
        XCTAssertEqual(Glance(label: "绩点", value: "3.62", note: "标准 4.0 · 按学分加权").shortNote, "标准 4.0")
        XCTAssertEqual(Glance(label: "绩点", value: "3.62", note: "昨天 7.6").shortNote, "昨天 7.6")
        XCTAssertNil(Glance(label: "绩点", value: "3.62").shortNote)
    }

    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
    /// Sunday 2026-11-08 09:41 in Shanghai.
    var now: Date { Date(timeIntervalSince1970: 1_794_102_060) }

    /// The mock backend's cards of a plugin, as /today gives them.
    func cards(_ plugin: String) -> [Card] {
        MockScenario(now: now, calendar: calendar).cards[plugin] ?? []
    }

    func stat(_ title: String, _ data: [String: JSONValue]) -> Card {
        Card(type: .stat, title: title, data: .object(data))
    }

    func testAnOrangeRowComesBeforeEverythingElse() throws {
        // 用量Bot: Claude's week at 82 % says more than the balance or the month's total.
        let glance = try XCTUnwrap(DashboardGlance.lead(cards("token_usage")))
        XCTAssertEqual(glance.label, "Claude 用量限额 · Max")
        XCTAssertEqual(glance.value, "已用 82%")
        XCTAssertEqual(glance.note, "本周 · 周四 08:00 重置")
        XCTAssertEqual(glance.tone, .warn)
        XCTAssertEqual(try XCTUnwrap(glance.progress), 0.82, accuracy: 0.001)
    }

    func testRedBeatsOrangeWhereverItIs() throws {
        let warn = Card(
            type: .list, title: "Codex 用量限额",
            data: .object([
                "items": .array([
                    .object([
                        "title": .string("本周"), "detail": .string("已用 85%"),
                        "progress": .object(["value": .number(85), "max": .number(100), "tone": .string("warn")]),
                    ])
                ])
            ]))
        let alert = stat(
            "11月还能花",
            ["value": .string("-40"), "unit": .string("元"), "bar": .object(["max": .number(100), "tone": .string("alert")])])
        let glance = try XCTUnwrap(DashboardGlance.lead([warn, alert]))
        XCTAssertEqual(glance.label, "11月还能花")
        XCTAssertEqual(glance.tone, .alert)
    }

    func testTheBigNumberLeads() throws {
        let wide = stat(
            "11月还能花",
            [
                "first": .bool(true), "wide": .bool(true), "value": .string("1,161"), "unit": .string("元"),
                "label": .string("已花 839 / 2,000 元 · 还有 23 天\n账单截至 11/6"),
                "bar": .object([
                    "max": .number(2000),
                    "segments": .array([.object(["x": .string("吃饭"), "y": .number(600)]), .object(["x": .string("其他"), "y": .number(239)])]),
                ]),
            ])
        let other = stat("现有资产", ["value": .string("8,000"), "unit": .string("元")])
        let glance = try XCTUnwrap(DashboardGlance.lead([other, wide]))
        XCTAssertEqual(glance.value, "1,161")
        XCTAssertEqual(glance.unit, "元")
        // Only the label's first line, and the bar filled to what was spent.
        XCTAssertEqual(glance.note, "已花 839 / 2,000 元 · 还有 23 天")
        XCTAssertEqual(try XCTUnwrap(glance.progress), 839.0 / 2000, accuracy: 0.0001)
    }

    func testTodaysClassesSayWhatIsOnNow() throws {
        // 作业Bot: 今天的课 is put first; 高等数学 is going on.
        let glance = try XCTUnwrap(DashboardGlance.lead(cards("timetable") + cards("homework")))
        XCTAssertEqual(glance.label, "今天的课")
        XCTAssertEqual(glance.value, "高等数学I（A）")
        XCTAssertEqual(glance.note, "正在进行 · 09:55–12:20")
    }

    func testBeforeTheFirstClassItSaysWhatIsNext() throws {
        let morning = Card(
            type: .list, title: "今天的课",
            data: .object([
                "style": .string("timeline"), "first": .bool(true),
                "items": .array([.object(["title": .string("大学英语（1）"), "subtitle": .string("08:00–09:35")])]),
            ]))
        let glance = try XCTUnwrap(DashboardGlance.lead([morning]))
        XCTAssertEqual(glance.value, "大学英语（1）")
        XCTAssertEqual(glance.note, "接下来 · 08:00–09:35")
    }

    func testADayThatIsOverFallsToTheNextFigure() throws {
        let over = Card(
            type: .list, title: "今天的课",
            data: .object([
                "style": .string("timeline"), "first": .bool(true),
                "items": .array([.object(["title": .string("大学英语（1）"), "state": .string("done")])]),
            ]))
        // Tomorrow's guess has no states and is not first: never the figure.
        let tomorrow = Card(
            type: .list, title: "明天的课 · 推测",
            data: .object(["style": .string("timeline"), "items": .array([.object(["title": .string("体育")])])]))
        let homework = stat("这周要交的作业", ["value": .number(3), "unit": .string("项"), "label": .string("最近一项：今天")])
        let glance = try XCTUnwrap(DashboardGlance.lead([over, tomorrow, homework]))
        XCTAssertEqual(glance.label, "这周要交的作业")
        XCTAssertEqual(glance.value, "3")
        XCTAssertEqual(glance.note, "最近一项：今天")
        XCTAssertNil(DashboardGlance.lead([tomorrow]))
    }

    func testTheFirstStatWithItsChange() throws {
        // 健康Bot (健康预警 with nothing to say is left out by the cards' own filter).
        let health = cards("health").filter { !($0.type == .list && $0.data["count"]?.doubleValue == 0) }
        let glance = try XCTUnwrap(DashboardGlance.lead(health))
        XCTAssertEqual(glance.label, "今日准备度")
        XCTAssertEqual(glance.value, "8.2")
        XCTAssertEqual(glance.unit, "/ 10")
        XCTAssertEqual(glance.note, "昨天 7.6")
        // Without a change, the label.
        let campus = try XCTUnwrap(DashboardGlance.lead(cards("campus")))
        XCTAssertEqual(campus.label, "第二课堂学分")
        XCTAssertEqual(campus.note, "毕业前还差 2.5 分")
    }

    func testARingIsItsNumberOverItsFullMark() throws {
        // 成绩Bot has no stat: the first ring, 平均绩点.
        let glance = try XCTUnwrap(DashboardGlance.lead(cards("grades")))
        XCTAssertEqual(glance.label, "平均绩点")
        XCTAssertEqual(glance.value, "3.62")
        XCTAssertEqual(glance.unit, "/ 4")
        XCTAssertEqual(glance.note, "标准 4.0 · 按学分加权 · 上学期 3.48")
        XCTAssertEqual(try XCTUnwrap(glance.progress), 0.905, accuracy: 0.001)
        XCTAssertNil(glance.tone)
    }

    func testADonutsTotalAndNothingElse() throws {
        let donut = Card(
            type: .chart, title: "总共使用的 token",
            data: .object([
                "kind": .string("donut"), "value": .string("26.5B"), "unit": .string("tokens"),
                "points": .array([.object(["x": .string("Claude"), "y": .number(1)])]),
            ]))
        XCTAssertEqual(DashboardGlance.lead([donut])?.value, "26.5B")
        // Plain rows, a line chart or prose: nothing to sum up.
        let rows = Card(type: .list, title: "请假记录", data: .object(["items": .array([.string("10月28日 事假半天")])]))
        let prose = Card(type: .text, title: "读取方式", data: .object(["text": .string("仅预览模式")]))
        XCTAssertNil(DashboardGlance.lead([rows, prose]))
        XCTAssertNil(DashboardGlance.lead([]))
    }

    func testTodosLeftOverdueOrNearest() throws {
        func todo(_ id: String, due: Date?, hasTime: Bool = true, status: TodoStatus = .pending) -> Todo {
            Todo(
                id: id, title: id, dueAt: due, hasTime: hasTime, sourcePlugin: "qq_screen", status: status,
                createdAt: now, updatedAt: now)
        }
        let at17 = now.addingTimeInterval(7 * 3600 + 19 * 60)
        let yesterday = now.addingTimeInterval(-22 * 3600)
        let late = DashboardGlance.todos(
            [todo("a", due: at17), todo("b", due: yesterday), todo("c", due: nil, status: .completed)],
            now: now, calendar: calendar)
        XCTAssertEqual(late.value, "2")
        XCTAssertEqual(late.unit, "件")
        XCTAssertEqual(late.note, "1 件已逾期")
        XCTAssertEqual(late.tone, .alert)
        let fine = DashboardGlance.todos([todo("a", due: at17), todo("b", due: nil)], now: now, calendar: calendar)
        XCTAssertEqual(fine.note, "最近截止 今天 17:00")
        XCTAssertNil(fine.tone)
        let done = DashboardGlance.todos([todo("a", due: at17, status: .completed)], now: now, calendar: calendar)
        XCTAssertEqual(done.value, "0")
        XCTAssertEqual(done.note, "今天的都做完了")
    }

    func testTodaysNumbersFirstAndTheBriefLast() {
        // 天气: 今日提醒 (text), 上海今天 (tile), 出门提醒 (list), 明天 (tile).
        let (tiles, rest) = DashboardGlance.todayOrder(cards("weather"))
        XCTAssertEqual(tiles.map(\.title), ["上海今天", "明天"])
        XCTAssertEqual(rest.map(\.title), ["出门提醒", "今日提醒"])
    }
}

final class DashboardPackingTests: XCTestCase {
    func testTheFirstRowsOfTheGrid() {
        // 健康 in three columns: 准备度 + 睡眠, the chart, 作息 + 静息心率 in the first row; HRV and
        // 负荷 fold.
        let lily = [true, true, false, true, true, true, true]
        XCTAssertEqual(CardPacking.firstRows(tiles: lily, columns: 3, rows: 1), [0, 1, 2, 3, 4])
        XCTAssertEqual(CardPacking.firstRows(tiles: lily, columns: 3, rows: 2), Array(0..<7))
        // On the iPhone, two rows: the tile pair and the chart.
        XCTAssertEqual(CardPacking.firstRows(tiles: lily, columns: 1, rows: 2), [0, 1, 2])
        // A tile paired with one further down brings it along, and the cards shown pack the same.
        let moved = [true, false, false, false, false, true]
        let shown = CardPacking.firstRows(tiles: moved, columns: 3, rows: 1)
        XCTAssertEqual(shown, [0, 1, 2, 5])
        XCTAssertEqual(CardPacking.grid(tiles: shown.map { moved[$0] }, columns: 3).count, 1)
        XCTAssertEqual(CardPacking.firstRows(tiles: [], columns: 3, rows: 1), [])
        XCTAssertEqual(CardPacking.firstRows(tiles: [false], columns: 3, rows: 0), [])
    }

    func testTilesGoInEvenRows() {
        // Seven tiles where six fit: four and three. Where all seven fit: one row.
        XCTAssertEqual(CardPacking.balancedColumns(count: 7, width: 980, minWidth: 150, spacing: 10), 4)
        XCTAssertEqual(CardPacking.balancedColumns(count: 7, width: 1120, minWidth: 150, spacing: 10), 7)
        XCTAssertEqual(CardPacking.balancedColumns(count: 8, width: 980, minWidth: 150, spacing: 10), 4)
        XCTAssertEqual(CardPacking.balancedColumns(count: 3, width: 980, minWidth: 150, spacing: 10), 3)
        XCTAssertEqual(CardPacking.balancedColumns(count: 0, width: 980, minWidth: 150, spacing: 10), 1)
        XCTAssertEqual(CardPacking.balancedColumns(count: 5, width: 100, minWidth: 150, spacing: 10), 1)
    }

    func testTheIPhonesFirstCoupleOfCards() {
        // 健康: the pair of tiles is the couple; the chart after them would make three.
        XCTAssertEqual(CardPacking.firstRows(tiles: [true, true, false, true, true], holding: 2), [0, 1])
        // 成绩: two cards, one a row each.
        XCTAssertEqual(CardPacking.firstRows(tiles: [false, false, false], holding: 2), [0, 1])
        // A lone tile, then a chart: two rows of one.
        XCTAssertEqual(CardPacking.firstRows(tiles: [false, true, false, false], holding: 2), [0, 1])
        // The first row shows whatever it holds; nothing is nothing.
        XCTAssertEqual(CardPacking.firstRows(tiles: [true, true], holding: 1), [0, 1])
        XCTAssertEqual(CardPacking.firstRows(tiles: [], holding: 2), [])
    }
}
