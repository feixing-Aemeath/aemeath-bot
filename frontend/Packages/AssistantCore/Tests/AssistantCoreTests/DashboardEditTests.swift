import XCTest

@testable import AssistantCore

final class DashboardEditTests: XCTestCase {
    func card(_ json: String) throws -> Card {
        try JSONDecoder.assistant().decode(Card.self, from: Data(json.utf8))
    }

    static let brief = """
        **天气** 多云，9–21°C
        - 外套或卫衣：早晚体感 7°C
        - 注意防晒：紫外线指数 7

        **课程** 今天 1 节课
        - 08:00–09:35 大学英语（1）

        **待办** 今天截止 2 件
        - 17:00 学生信息核对
        - 逾期：补交寝室卫生评比材料

        **注意**
        - 15:00 后可能下雨，和高数课重合
        """

    // MARK: Nothing to report

    func testAFigureWithNothingInIt() {
        for text in ["—", "— / 4", "–", "", "0", "0 学分", "0 / 6", "0.0", "0%"] {
            XCTAssertTrue(DashboardEdit.isNothing(text), text)
        }
        for text in ["3.62", "8.2", "2,000", "已用 0%", "0 元，还有 2,000", "高等数学I（A）", "-105", "00:30"] {
            XCTAssertFalse(DashboardEdit.isNothing(text), text)
        }
    }

    func testAGlanceIsBlankUnlessSomeoneMarkedIt() {
        XCTAssertTrue(Glance(label: "平均绩点", value: "—", unit: "/ 4").isBlank)
        XCTAssertTrue(Glance(label: "这周要交的作业", value: "0", unit: "项", note: "这周没有要交的作业").isBlank)
        XCTAssertFalse(Glance(label: "今日准备度", value: "8.8", unit: "/ 10").isBlank)
        // Nothing spent yet is a zero bar under a number that says something.
        XCTAssertFalse(Glance(label: "11月还能花", value: "2,000", unit: "元", progress: 0).isBlank)
        // A zero the backend marked red still needs a look.
        XCTAssertFalse(Glance(label: "余额", value: "0", unit: "元", tone: .alert).isBlank)
    }

    func testCardsThatSayNothingYet() throws {
        // 成绩 before the first grades: every ring is 「—」 or zero.
        let rings = try card(
            #"{"type": "list", "title": "学业进度", "data": {"style": "rings", "items": [{"title": "平均绩点", "detail": "— / 4", "progress": {"value": 0, "max": 4}}, {"title": "已修学分", "detail": "0 学分", "progress": {"value": 0, "max": 160}}]}}"#
        )
        XCTAssertTrue(rings.isBlank)
        let scored = try card(
            #"{"type": "list", "title": "学业进度", "data": {"style": "rings", "items": [{"title": "平均绩点", "detail": "3.62 / 4", "progress": {"value": 3.62, "max": 4}}, {"title": "体测", "detail": "— / 100", "progress": {"value": 0, "max": 100}}]}}"#
        )
        XCTAssertFalse(scored.isBlank)
        XCTAssertTrue(try card(#"{"type": "stat", "title": "这周的活动", "data": {"value": 0, "unit": "个"}}"#).isBlank)
        XCTAssertTrue(try card(#"{"type": "stat", "title": "平均绩点", "data": {"value": "—", "unit": "/ 4"}}"#).isBlank)
        XCTAssertFalse(try card(#"{"type": "stat", "title": "今日准备度", "data": {"value": "8.8"}}"#).isBlank)
        // A zero with a bar or a change beside it is a reading, not an empty card.
        XCTAssertFalse(
            try card(#"{"type": "stat", "title": "已花", "data": {"value": 0, "unit": "元", "bar": {"max": 2000, "segments": []}}}"#)
                .isBlank)
        // Rows in words (近期活动) and a day's classes say something without a figure.
        XCTAssertFalse(
            try card(#"{"type": "list", "title": "近期活动", "data": {"items": [{"title": "青禾讲座", "detail": "讲座"}]}}"#)
                .isBlank)
        XCTAssertFalse(
            try card(
                #"{"type": "list", "title": "今天的课", "data": {"style": "timeline", "items": [{"title": "大学英语", "subtitle": "08:00–09:35"}]}}"#
            ).isBlank)
        XCTAssertTrue(
            try card(#"{"type": "chart", "title": "各学期绩点", "data": {"kind": "bar", "points": [{"x": "大一上", "y": 0}]}}"#)
                .isBlank)
        XCTAssertTrue(try card(#"{"type": "text", "title": "备注", "data": {"text": "  \n"}}"#).isBlank)
    }

    func testBlankCardsGoLast() throws {
        let cards = [
            try card(#"{"type": "stat", "title": "这周的活动", "data": {"value": 0, "unit": "个"}}"#),
            try card(#"{"type": "list", "title": "近期活动", "data": {"items": [{"title": "青禾讲座", "detail": "讲座"}]}}"#),
            try card(#"{"type": "stat", "title": "第二课堂学分", "data": {"value": "3.5", "unit": "/ 6"}}"#),
        ]
        XCTAssertEqual(DashboardEdit.blankLast(cards).map(\.title), ["近期活动", "第二课堂学分", "这周的活动"])
    }

    // MARK: 一览

    func testTheStripLeadsWithWhatNeedsALook() {
        let parts = DashboardEdit.strip([
            (id: "grades", glance: Glance(label: "平均绩点", value: "—", unit: "/ 4")),
            (id: "health", glance: Glance(label: "今日准备度", value: "8.8", unit: "/ 10")),
            (id: "campus", glance: Glance(label: "这周的活动", value: "0", unit: "个")),
            (id: "finance", glance: Glance(label: "11月还能花", value: "-105", unit: "元", tone: .alert)),
            (id: "token_usage", glance: Glance(label: "Claude 用量限额", value: "已用 88%", tone: .warn, progress: 0.88)),
            (id: "knowledge", glance: nil),
            (id: "homework", glance: Glance(label: "今天的课", value: "高等数学I（A）", note: "接下来 · 09:55–12:20")),
        ])
        // Red, then orange, then the rest as the page has them.
        XCTAssertEqual(parts.tiles, ["finance", "token_usage", "health", "homework"])
        // No figure, 「—」 and a plain zero share one line.
        XCTAssertEqual(parts.quiet, ["grades", "campus", "knowledge"])
    }

    // MARK: 今天

    func testTheBriefsParts() {
        XCTAssertEqual(DashboardEdit.briefParts(Self.brief).map(\.name), ["天气", "课程", "待办", "注意"])
        XCTAssertEqual(DashboardEdit.partName("**天气** 多云，9–21°C"), "天气")
        XCTAssertEqual(DashboardEdit.partName("  **注意**"), "注意")
        XCTAssertNil(DashboardEdit.partName("- **重要**：带伞"))
        XCTAssertNil(DashboardEdit.partName("天气 **多云**"))
        XCTAssertTrue(DashboardEdit.isBrief(Self.brief))
        // The weekly review has other parts and is left alone.
        XCTAssertFalse(DashboardEdit.isBrief("**这周** 同桌，收拾掉 6 件\n- 健康：7 天都有记录\n\n**待办** 逾期 1 件"))
    }

    func testTheBriefWithoutWhatTheCardsBesideItShow() {
        let trimmed = DashboardEdit.trimBrief(Self.brief, dropping: ["待办", "天气"])
        XCTAssertEqual(
            trimmed,
            "**课程** 今天 1 节课\n- 08:00–09:35 大学英语（1）\n\n**注意**\n- 15:00 后可能下雨，和高数课重合")
        // Nothing named: as it was written.
        XCTAssertEqual(DashboardEdit.trimBrief(Self.brief, dropping: []), Self.brief)
        // Nothing left.
        XCTAssertEqual(
            DashboardEdit.trimBrief("**天气** 晴\n- 不用带伞\n\n**待办** 今天没有要交的", dropping: ["待办", "天气"]), "")
        // A line before the first part stays.
        XCTAssertEqual(
            DashboardEdit.trimBrief("早上好\n**待办** 今天截止 1 件\n- 交作业", dropping: ["待办"]), "早上好")
    }

    func testTodaysCardsDoNotSayThingsTwice() throws {
        func text(_ title: String, _ body: String, plugin: String?) -> Card {
            Card(type: .text, title: title, data: .object(["text": .string(body)]), plugin: plugin)
        }
        let today = try card(#"{"type": "stat", "title": "上海今天", "plugin": "weather", "data": {"value": "9–21", "unit": "°C"}}"#)
        let take = try card(
            #"{"type": "list", "title": "出门提醒", "plugin": "weather", "data": {"items": [{"title": "不用带伞"}]}}"#)
        let review = text("本周回顾", "**这周** 同桌，收拾掉 6 件\n- 健康：7 天都有记录", plugin: "weekly_review")
        let brief = text("今日提醒", Self.brief, plugin: "weather")

        let edited = DashboardEdit.todayCards([brief, today, take, review])
        XCTAssertEqual(edited.map(\.title), ["今日提醒", "上海今天", "出门提醒", "本周回顾"])
        guard case .text(let kept) = edited[0].content else { return XCTFail() }
        XCTAssertFalse(kept.text.contains("**待办**"))
        XCTAssertFalse(kept.text.contains("**天气**"))
        XCTAssertTrue(kept.text.hasPrefix("**课程** 今天 1 节课"))
        // The other cards are as they came, and it is still the same card to the page.
        XCTAssertEqual(edited[0].id, brief.id)
        XCTAssertEqual(Array(edited.dropFirst()), [today, take, review])

        // Without the weather's own cards beside it the brief keeps its weather.
        let alone = DashboardEdit.todayCards([brief, review])
        guard case .text(let whole) = alone[0].content else { return XCTFail() }
        XCTAssertTrue(whole.text.hasPrefix("**天气** 多云"))
        XCTAssertFalse(whole.text.contains("**待办**"))

        // A brief that only had those two parts goes.
        let thin = text("今日提醒", "**天气** 晴\n- 不用带伞\n\n**待办** 今天没有要交的", plugin: "weather")
        XCTAssertEqual(DashboardEdit.todayCards([thin, today]).map(\.title), ["上海今天"])
    }

    func testWhatTheSummaryCouldNotSettle() {
        let summary = """
            **新待办**
            - 班级群：学生信息核对，今天 17:00

            **通知**
            - 实验室：周会改到周四下午

            **还不确定**
            - 数据结构课程群：作业提醒，预览，没看到全文
            - **排球课**：体测时间没写清
            """
        XCTAssertEqual(
            DashboardEdit.unsure(in: summary),
            ["数据结构课程群：作业提醒，预览，没看到全文", "排球课：体测时间没写清"])
        XCTAssertEqual(DashboardEdit.unsure(in: "**新待办**\n- 班级群：学生信息核对"), [])
        XCTAssertEqual(DashboardEdit.unsure(in: "班级群有 2 条截止通知"), [])
        // The section ends at the next one.
        XCTAssertEqual(DashboardEdit.unsure(in: "**还不确定**\n- 甲\n\n**通知**\n- 乙"), ["甲"])
    }

    func testOpenTodosThatAreNotTodays() throws {
        func todo(_ id: String, due: String?, created: String, status: String = "pending") throws -> Todo {
            let dueJSON = due.map { "\"\($0)\"" } ?? "null"
            let json = """
                {"id": "\(id)", "title": "t-\(id)", "notes": "", "due_at": \(dueJSON), "has_time": false,
                 "source_plugin": "app", "source_refs": ["m"], "source_excerpt": "x", "confidence": 1,
                 "status": "\(status)", "dedup_key": "k-\(id)", "reminder_id": null, "calendar_event_id": null,
                 "created_by": "user", "created_at": "\(created)", "updated_at": "\(created)",
                 "sync_status": "synced", "sync_error": null}
                """
            return try JSONDecoder.assistant().decode(Todo.self, from: Data(json.utf8))
        }
        let today = try todo("today", due: "2026-11-12T17:00:00+08:00", created: "2026-11-10T08:00:00+08:00")
        let nextWeek = try todo("week", due: "2026-11-19T12:00:00+08:00", created: "2026-11-11T08:00:00+08:00")
        let tomorrow = try todo("tomorrow", due: "2026-11-13T12:00:00+08:00", created: "2026-11-12T08:00:00+08:00")
        let oldNote = try todo("old", due: nil, created: "2026-11-01T08:00:00+08:00")
        let newNote = try todo("new", due: nil, created: "2026-11-12T09:00:00+08:00")
        let done = try todo("done", due: "2026-11-20T12:00:00+08:00", created: "2026-11-12T08:00:00+08:00", status: "completed")
        let later = DashboardEdit.later([oldNote, done, nextWeek, today, newNote, tomorrow], today: [today])
        // The soonest first, then the ones without a date, the newest of those first; not
        // today's own, and nothing that is closed.
        XCTAssertEqual(later.map(\.id), ["tomorrow", "week", "new", "old"])
        XCTAssertTrue(DashboardEdit.later([today, done], today: [today]).isEmpty)
    }

    func testOwnAndForwardedTodosNameNoChatOrSender() throws {
        func todo(_ plugin: String, chat: String, sender: String) throws -> Todo {
            let json = """
                {"id": "x", "title": "t", "notes": "", "due_at": null, "has_time": false,
                 "source_plugin": "\(plugin)", "source_refs": ["m"], "source_excerpt": "x", "confidence": 1,
                 "status": "pending", "dedup_key": "k", "reminder_id": null, "calendar_event_id": null,
                 "created_by": "user", "created_at": "2026-11-12T08:00:00+08:00",
                 "updated_at": "2026-11-12T08:00:00+08:00", "sync_status": "synced", "sync_error": null,
                 "sources": [{"message_id": "m", "plugin": "\(plugin)", "chat_name": "\(chat)",
                              "sender": "\(sender)", "sent_at": "2026-11-12T08:00:00+08:00"}]}
                """
            return try JSONDecoder.assistant().decode(Todo.self, from: Data(json.utf8))
        }
        // The backend files these under a made-up chat and 「我」; the line does not repeat them.
        XCTAssertEqual(TodoSourceText.describe(try todo("app", chat: "手动新建", sender: "我")), "我自己记的")
        XCTAssertEqual(TodoSourceText.describe(try todo("share", chat: "转给助手", sender: "我")), "我转给助手的")
        // A forwarded one keeps the chat the user said it came from.
        XCTAssertEqual(
            TodoSourceText.describe(try todo("share", chat: "某个群", sender: "我")), "我转给助手的 · 某个群")
        // A chat that was read is named with its sender, as before.
        XCTAssertEqual(
            TodoSourceText.describe(try todo("qq_screen", chat: "某个群", sender: "某人")), "QQ · 某个群 · 某人")
    }

    func testAWeekOfAccountingInOneLine() {
        let week = ExtractionWeek(
            weekStart: "2026-11-09", caught: 3, withDue: 3, late: 1, medianLeadHours: 26.5, missed: 1,
            notATodo: 0, recall: 0.75)
        XCTAssertEqual(week.startText, "11月9日起")
        XCTAssertEqual(week.detail, "提前 26.5 小时 · 晚了 1 条 · 你补了 1 条 · 抓到 75%")
        // Only what there is to say: nothing late, nothing added, no deadline to measure.
        let quiet = ExtractionWeek(
            weekStart: "2026-11-02", caught: 2, withDue: 0, late: 0, medianLeadHours: nil, missed: 0,
            notATodo: 1, recall: 1)
        XCTAssertEqual(quiet.detail, "1 条不是待办 · 抓到 100%")
        let empty = ExtractionWeek(
            weekStart: "not a date", caught: 0, withDue: 0, late: 0, medianLeadHours: nil, missed: 0,
            notATodo: 0, recall: nil)
        XCTAssertEqual(empty.detail, "这周没有数据")
        XCTAssertEqual(empty.startText, "not a date")
    }

    // MARK: Small things

    func testAStateInWordsIsNotAFigure() {
        XCTAssertTrue(CardListItem(title: "吃饭", detail: "344 元").detailIsFigure)
        XCTAssertTrue(CardListItem(title: "本周", detail: "已用 88%").detailIsFigure)
        XCTAssertFalse(CardListItem(title: "10月28日 事假半天", detail: "审批中").detailIsFigure)
        // 「二」 is a number to Unicode, not a figure on a card.
        XCTAssertFalse(CardListItem(title: "人工智能前沿讲座", detail: "加二课分").detailIsFigure)
        XCTAssertFalse(CardListItem(title: "青禾讲座").detailIsFigure)
    }

    func testOnlyTheTokenBotIsUpkeep() {
        let look = StickerLook(sticker: nil)
        XCTAssertTrue(Bot(id: "token_usage", name: "用量Bot", look: look).isUpkeep)
        XCTAssertFalse(Bot(id: "health", name: "健康Bot", look: look).isUpkeep)
        XCTAssertFalse(Bot(id: Bot.assistantID, name: "助理Bot", kind: .assistant, look: look).isUpkeep)
    }
}

extension DashboardEditTests {
    func testOutingAdviceKeepsRainHoursWithoutRepeatingForecast() throws {
        let forecast = try card(#"{"type":"stat","plugin":"weather","title":"上海今天","data":{"value":"9–21","unit":"°C","label":"小雨 · 降水概率 70%"}}"#)
        let advice = try card(#"{"type":"list","plugin":"weather","title":"出门提醒","data":{"items":[{"title":"要带伞","subtitle":"15:00–18:00，降水概率 70%"},{"title":"加外套","subtitle":"体感 7–18°C"}]}}"#)
        let edited = DashboardEdit.todayCards([forecast, advice])
        guard case .list(let rows) = edited.last?.content else { return XCTFail() }
        XCTAssertEqual(rows.map(\.subtitle), ["15:00–18:00", "体感 7–18°C"])
        XCTAssertEqual(DashboardEdit.todayCards([advice]), [advice])
    }
}

extension DashboardEditTests {
    func testBriefDropsOnlyAlertsAlreadyListedAndKeepsUnrelatedWarnings() throws {
        let body = "**课程** 今天没有课\n\n**注意**\n- QQ 全文读取有缺口，请查看覆盖卡片\n- 15:00 的雨和上课时间重合"
        let brief = Card(type: .text, title: "今日提醒", data: .object(["text": .string(body)]), plugin: "weather")
        let edited = DashboardEdit.todayCards([brief], systemAlerts: ["QQ全文读取有缺口"])
        XCTAssertEqual(edited.first?.data["text"]?.stringValue, "**课程** 今天没有课\n\n**注意**\n- 15:00 的雨和上课时间重合")
        let only = brief.replacingText("**注意**\n- QQ全文读取有缺口")
        XCTAssertTrue(DashboardEdit.todayCards([only], systemAlerts: ["QQ全文读取有缺口"]).isEmpty)
        XCTAssertEqual(DashboardEdit.todayCards([only]), [only])
    }

    func testTileOwnsItsSourceAndEmptyPanelsHaveNoCards() throws {
        let lead = try card(#"{"type":"stat","title":"开发天数","plugin":"a","data":{"value":38}}"#)
        var other = lead
        other.plugin = "b"
        let blank = try card(#"{"type":"stat","title":"学业进度","data":{"value":"—"}}"#)
        XCTAssertEqual(DashboardEdit.panel([lead, other, blank]).cards, [other, blank])
        XCTAssertEqual(DashboardEdit.panel([lead, blank]).cards, [])
        XCTAssertEqual(DashboardEdit.panel([lead, blank]).glance?.value, "38")
        XCTAssertNil(DashboardEdit.panel([blank]).glance)
        XCTAssertEqual(DashboardEdit.panel([blank]).cards, [])
    }

    func testDashboardListsLimitOnlyThePreview() {
        let all = Array(1...8)
        XCTAssertEqual(DashboardEdit.preview(all, compact: true).items, [1, 2, 3])
        XCTAssertEqual(DashboardEdit.preview(all, compact: true).remaining, 5)
        XCTAssertEqual(DashboardEdit.preview(all, compact: false).items, [1, 2, 3, 4, 5])
        XCTAssertEqual(DashboardEdit.preview(all, compact: false).remaining, 3)
        XCTAssertEqual(DashboardEdit.preview([1, 2, 3], compact: true).remaining, 0)
    }
}

extension DashboardEditTests {
    func testWeatherMatchingKeepsDifferentMeasurementsAndDifferentDays() throws {
        let forecast = try card(#"{"type":"stat","plugin":"weather","title":"上海今天","data":{"value":"9–21","label":"降水概率 7.5%"}}"#)
        let advice = try card(#"{"type":"list","plugin":"weather","title":"出门提醒","data":{"items":[{"title":"要带伞","subtitle":"降水概率 75%"}]}}"#)
        var tomorrow = forecast
        tomorrow.title = "明天"
        XCTAssertEqual(DashboardEdit.todayCards([forecast, advice]), [forecast, advice])
        XCTAssertEqual(DashboardEdit.todayCards([tomorrow, advice]), [tomorrow, advice])
    }
}
