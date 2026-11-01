import XCTest

@testable import AssistantCore

final class CardTests: XCTestCase {
    func card(_ json: String) throws -> Card {
        try JSONDecoder.assistant().decode(Card.self, from: Data(json.utf8))
    }

    func testText() throws {
        let content = try card(
            #"{"type": "text", "title": "晨间简报", "data": {"text": "睡得不错", "headline": "10月15日"}}"#
        ).content
        XCTAssertEqual(content, .text(CardText(text: "睡得不错", headline: "10月15日")))
    }

    func testStatWithValueAndCounter() throws {
        let stat = try card(
            #"{"type": "stat", "title": "步数", "data": {"value": 6240, "unit": "步", "delta": -312.5, "trend": "down"}}"#
        ).content
        XCTAssertEqual(
            stat, .stat(CardStat(value: "6240", unit: "步", label: nil, delta: "-312.5", trend: .down)))
        // A plain counter such as {"count": 1} from simple plugins still shows.
        let counter = try card(#"{"type": "stat", "title": "待同步", "data": {"count": 1}}"#).content
        XCTAssertEqual(
            counter, .stat(CardStat(value: "1", unit: nil, label: "count", delta: nil, trend: nil)))
        let broken = try card(#"{"type": "stat", "title": "x", "data": {"a": 1, "b": 2}}"#).content
        XCTAssertEqual(broken, .unsupported(type: "stat", reason: "missing value"))
    }

    func testTheWideNumberWithItsBar() throws {
        let hero = try card(
            #"{"type": "stat", "title": "10月还能花", "data": {"first": true, "wide": true, "value": "-105", "unit": "元", "bar": {"max": 2000, "tone": "alert", "segments": [{"x": "会员订阅", "y": 944.47, "label": "944 元"}, {"x": "吃饭", "y": 344.5}]}}}"#
        )
        XCTAssertTrue(hero.isWide)
        XCTAssertFalse(hero.isTile)  // a row of its own on the iPhone
        guard case .stat(let stat) = hero.content else { return XCTFail() }
        XCTAssertTrue(stat.wide)
        XCTAssertEqual(
            stat.bar,
            CardStat.Bar(
                max: 2000,
                segments: [.init(x: "会员订阅", y: 944.47, label: "944 元"), .init(x: "吃饭", y: 344.5)],
                tone: .alert))
        // A bar without its total is no bar; a plain stat is still a tile.
        let plain = try card(#"{"type": "stat", "title": "x", "data": {"value": 1, "bar": {"segments": []}}}"#)
        guard case .stat(let tile) = plain.content else { return XCTFail() }
        XCTAssertNil(tile.bar)
        XCTAssertTrue(plain.isTile)
    }

    func testBarsAgainstATarget() throws {
        let months = try card(
            #"{"type": "chart", "title": "每月花销", "data": {"kind": "bar", "unit": "元", "points": [{"x": "9月", "y": 2260.16, "label": "2,260"}, {"x": "10月", "y": 1161}], "target": {"value": 2000, "label": "生活费 2,000"}}}"#
        ).content
        guard case .chart(let chart) = months else { return XCTFail() }
        XCTAssertEqual(chart.target, CardChart.Target(value: 2000, label: "生活费 2,000"))
        XCTAssertEqual(chart.series.first?.points.map(\.label), ["2,260", nil])
    }

    func testList() throws {
        let content = try card(
            #"{"type": "list", "title": "今日摘要", "data": {"items": ["放假通知", {"title": "讲座", "subtitle": "周三 14:00"}, {"no_title": true}]}}"#
        ).content
        XCTAssertEqual(
            content,
            .list([
                CardListItem(title: "放假通知", subtitle: nil, detail: nil),
                CardListItem(title: "讲座", subtitle: "周三 14:00", detail: nil),
            ]))
    }

    func testListStylesItemStatesAndSymbols() throws {
        let timeline = try card(
            #"{"type": "list", "title": "今天的课", "data": {"style": "timeline", "items": [{"title": "大学英语", "subtitle": "08:00–09:35", "state": "done"}, {"title": "高数", "subtitle": "09:55–12:20", "state": "later"}]}}"#
        )
        XCTAssertEqual(timeline.listStyle, .timeline)
        guard case .list(let items) = timeline.content else { return XCTFail() }
        // A state this App does not know is left out, like any unknown field.
        XCTAssertEqual(items.map(\.state), [.done, nil])
        let plain = try card(#"{"type": "list", "title": "x", "data": {"style": "mosaic", "items": []}}"#)
        XCTAssertEqual(plain.listStyle, .rows)
        let weather = try card(
            #"{"type": "stat", "title": "上海今天", "data": {"value": "9–21", "symbol": "cloud.sun.fill"}}"#)
        XCTAssertEqual(weather.symbol, "cloud.sun.fill")
        XCTAssertNil(plain.symbol)
    }

    func testATableWithItsTextAndWhatGoesFirst() throws {
        let week = try card(
            #"{"type": "text", "title": "这周课表", "data": {"text": "**周二** 08:00 大学英语", "first": true, "footnote": "今天读到这周。", "table": {"columns": ["", "周一", "周二"], "rows": [["08:00", "", "大学英语"]], "highlight": 2}}}"#
        )
        XCTAssertTrue(week.first)
        XCTAssertFalse(week.isTile)
        guard case .text(let text) = week.content else { return XCTFail() }
        XCTAssertEqual(text.table, CardTable(columns: ["", "周一", "周二"], rows: [["08:00", "", "大学英语"]], highlight: 2))
        XCTAssertEqual(text.footnote, "今天读到这周。")
        // A row short of a cell, or a highlight past the columns: the text, or no highlight.
        let broken = try card(
            #"{"type": "text", "title": "x", "data": {"text": "t", "table": {"columns": ["", "周一"], "rows": [["08:00"]]}}}"#)
        guard case .text(let plain) = broken.content else { return XCTFail() }
        XCTAssertNil(plain.table)
        let far = try card(
            #"{"type": "text", "title": "x", "data": {"text": "t", "table": {"columns": ["", "周一"], "rows": [["08:00", "课"]], "highlight": 5}}}"#)
        guard case .text(let unmarked) = far.content else { return XCTFail() }
        XCTAssertNil(unmarked.table?.highlight)
        XCTAssertTrue(try card(#"{"type": "stat", "title": "HRV", "data": {"value": 60}}"#).isTile)
    }

    func testChartSingleAndMultipleSeries() throws {
        let single = try card(
            #"{"type": "chart", "title": "静息心率", "data": {"kind": "bar", "unit": "bpm", "points": [{"x": "10-14", "y": 56}, {"x": "10-15", "y": 55.5}]}}"#
        ).content
        guard case .chart(let chart) = single else { return XCTFail("\(single)") }
        XCTAssertEqual(chart.kind, .bar)
        XCTAssertEqual(chart.series.first?.points.map(\.y), [56, 55.5])
        let donut = try card(
            #"{"type": "chart", "title": "总共使用的 token", "data": {"kind": "donut", "value": "17.0B", "unit": "tokens", "label": "Claude 15.7B · GPT 71.8M · DeepSeek 1.2B", "points": [{"x": "Claude", "y": 15701861056}, {"x": "GPT", "y": 71800000}, {"x": "DeepSeek", "y": 1200000000}]}}"#
        ).content
        guard case .chart(let ring) = donut else { return XCTFail("\(donut)") }
        XCTAssertEqual(ring.kind, .donut)
        XCTAssertEqual(ring.value, "17.0B")
        XCTAssertEqual(ring.label, "Claude 15.7B · GPT 71.8M · DeepSeek 1.2B")
        XCTAssertEqual(ring.series.first?.points.map(\.x), ["Claude", "GPT", "DeepSeek"])
        let multiple = try card(
            #"{"type": "chart", "title": "睡眠", "data": {"series": [{"name": "深睡", "points": [{"x": 1, "y": 80}]}, {"name": "REM", "points": [{"x": 1, "y": 95}]}]}}"#
        ).content
        guard case .chart(let many) = multiple else { return XCTFail("\(multiple)") }
        XCTAssertEqual(many.kind, .line)
        XCTAssertEqual(many.series.map(\.name), ["深睡", "REM"])
        XCTAssertEqual(many.series[0].points[0].x, "1")
    }

    func testPhotoGrid() throws {
        let content = try card(
            #"{"type": "photo_grid", "title": "今日照片", "data": {"photos": [{"id": "p1", "caption": "晚霞", "path": "/Users/x/Pictures/a.heic"}, {"caption": "无 ID"}]}}"#
        ).content
        XCTAssertEqual(
            content,
            .photoGrid([
                CardPhoto(id: "p1", caption: "晚霞", path: "/Users/x/Pictures/a.heic"),
                CardPhoto(id: "1", caption: "无 ID", path: nil),
            ]))
    }

    func testTodoList() throws {
        let content = try card(
            #"{"type": "todo_list", "title": "AI 待办", "data": {"todos": [\#(Fixtures.todo)]}}"#
        ).content
        guard case .todoList(let todos) = content else { return XCTFail("\(content)") }
        XCTAssertEqual(todos.map(\.title), ["完成信息核对"])
        XCTAssertNotNil(todos[0].dueAt)
        let broken = try card(#"{"type": "todo_list", "title": "x", "data": {"todos": [{"id": 1}]}}"#)
        XCTAssertEqual(
            broken.content, .unsupported(type: "todo_list", reason: "missing or invalid todos"))
    }

    func testUnknownTypesAndMissingFieldsNeverFail() throws {
        let future = try card(#"{"type": "map", "title": "位置", "data": {"lat": 1}}"#)
        XCTAssertEqual(future.type, .unknown("map"))
        XCTAssertEqual(future.content, .unsupported(type: "map", reason: "unknown card type"))
        XCTAssertEqual(
            try card(#"{"type": "text", "title": "x", "data": {}}"#).content,
            .unsupported(type: "text", reason: "missing text"))
        XCTAssertEqual(
            try card(#"{"type": "chart", "title": "x", "data": {"points": "oops"}}"#).content,
            .unsupported(type: "chart", reason: "missing points"))
    }

    func testHealthPluginCardsFromTheBackend() throws {
        let cards = try JSONDecoder.assistant().decode(
            [Card].self, from: Data(Fixtures.healthCards.utf8))
        XCTAssertEqual(
            cards.map(\.title),
            ["健康预警", "今日准备度", "昨晚睡眠", "最近 7 晚睡眠分期", "作息规律", "静息心率", "HRV", "训练负荷"])
        for card in cards {
            if case .unsupported(_, let reason) = card.content {
                XCTFail("\(card.title) unsupported: \(reason)")
            }
        }
        guard case .list(let alerts) = cards[0].content else { return XCTFail("alerts") }
        XCTAssertEqual(alerts.map(\.title), ["静息心率高于基线", "HRV 低于基线"])
        XCTAssertEqual(alerts[0].detail, "连续 3 天")
        guard case .stat(let readiness) = cards[1].content else { return XCTFail("readiness") }
        XCTAssertEqual(readiness.value, "4.5")
        XCTAssertEqual(readiness.unit, "/ 10")
        XCTAssertEqual(readiness.delta, "昨天 4.5")
        guard case .chart(let stages) = cards[3].content else { return XCTFail("stages") }
        XCTAssertEqual(stages.kind, .bar)
        XCTAssertEqual(stages.unit, "小时")
        XCTAssertEqual(stages.series.map(\.name), ["深睡", "核心", "REM", "清醒"])
        XCTAssertTrue(stages.series.allSatisfy { $0.points.count == 7 })
        guard case .stat(let restingHR) = cards[5].content else { return XCTFail("resting HR") }
        XCTAssertEqual(restingHR.value, "64.7")
        XCTAssertEqual(restingHR.delta, "+8.2")
        XCTAssertEqual(restingHR.trend, .up)
        guard case .stat(let hrv) = cards[6].content else { return XCTFail("HRV") }
        XCTAssertEqual(hrv.value, "39")
        XCTAssertEqual(hrv.unit, "毫秒（RMSSD）")
        XCTAssertEqual(hrv.trend, .down)
    }

    func testCardTypeRoundTrip() throws {
        for raw in ["list", "stat", "chart", "photo_grid", "text", "todo_list", "custom"] {
            let original = Card(type: CardType(rawValue: raw), title: raw, data: .null)
            let data = try JSONEncoder.assistant().encode(original)
            XCTAssertTrue(String(decoding: data, as: UTF8.self).contains(#""type":"\#(raw)""#))
            XCTAssertEqual(try JSONDecoder.assistant().decode(Card.self, from: data), original)
        }
    }
}
