import XCTest

@testable import AssistantCore

final class ManualEntryTests: XCTestCase {
    let now = APIDateCoding.date(from: "2027-02-17T09:32:00+08:00")!

    func testTodayDistinguishesAbsentAndEmptyUnsure() throws {
        let legacy = try JSONDecoder.assistant().decode(Today.self, from: Data(Fixtures.today.utf8))
        XCTAssertNil(legacy.unsure)
        var today = legacy
        today.summary = "**通知**\n- 放假\n\n**还不确定**\n- 课程群：没读全\n- 另一个群：时间不明\n\n**其他**\n- 不算这里"
        XCTAssertEqual(today.unsureItems.map(\.text), ["课程群：没读全", "另一个群：时间不明"])
        XCTAssertTrue(today.unsureItems.allSatisfy { !$0.canOpen && $0.plugin == nil })
        XCTAssertEqual(today.unsureItems, today.unsureItems)
        let first = today.unsureItems.first?.id
        today.date = "2027-02-18"
        XCTAssertNotEqual(first, today.unsureItems.first?.id)
        today.unsure = []
        XCTAssertTrue(today.unsureItems.isEmpty)  // server explicitly resolved every item
        let data = Data(
            #"{"date":"2027-02-17","summary":"","todos":[],"cards":[],"unsure":[{"id":"u-x","text":"请核对","plugin":"qq_screen","chat_name":"课程群","can_open":true}]}"#
                .utf8)
        let structured = try JSONDecoder.assistant().decode(Today.self, from: data)
        XCTAssertEqual(structured.unsureItems.first?.chatName, "课程群")
        XCTAssertEqual(structured.unsureItems.first?.canOpen, true)
    }

    func testTodoNewEncodingAndDeadlineValidation() throws {
        let source = MissedSource(plugin: "wechat_screen", chatName: "项目讨论")
        let request = TodoNew(
            title: "整理调研", notes: "保留附件", dueAt: now, hasTime: true, missed: true, missedSource: source)
        let data = try JSONEncoder.assistant().encode(request)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(Set(object.keys), ["title", "notes", "due_at", "has_time", "missed", "missed_source"])
        XCTAssertEqual((object["missed_source"] as? [String: String])?["chat_name"], "项目讨论")
        XCTAssertEqual(try JSONDecoder.assistant().decode(TodoNew.self, from: data), request)
        XCTAssertNil(TodoNew(title: "笔记", missedSource: source).missedSource)
        XCTAssertFalse(TodoNew(title: " \n").isValid)
        XCTAssertFalse(TodoNew(title: "笔记", hasTime: true).isValid)
        XCTAssertTrue(TodoNew(title: "笔记").isValid)
    }

    func testInboxValidationAtEveryBoundary() {
        XCTAssertTrue(
            InboxItem(
                text: String(repeating: "文", count: 8000), note: String(repeating: "a", count: 500),
                sourceApp: String(repeating: "a", count: 40), chatName: String(repeating: "a", count: 80)
            ).isValid)
        for value in [
            InboxItem(text: " \n"), InboxItem(text: String(repeating: "文", count: 8001)),
            InboxItem(text: "有效", note: String(repeating: "a", count: 501)),
            InboxItem(text: "有效", sourceApp: String(repeating: "a", count: 41)),
            InboxItem(text: "有效", chatName: String(repeating: "a", count: 81)),
        ] {
            XCTAssertFalse(value.isValid)
        }
    }

    func testFormLimitsCountUnicodeScalars() throws {
        let pair = "🇨🇳"
        XCTAssertTrue(InboxItem(text: String(repeating: pair, count: 4000)).isValid)
        XCTAssertFalse(InboxItem(text: String(repeating: pair, count: 4001)).isValid)
        XCTAssertFalse(InboxItem(text: "x", note: String(repeating: pair, count: 251)).isValid)
        XCTAssertFalse(InboxItem(text: "x", sourceApp: String(repeating: pair, count: 21)).isValid)
        XCTAssertFalse(InboxItem(text: "x", chatName: String(repeating: pair, count: 41)).isValid)
        let todo = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todo.utf8))
        var draft = TodoDraft(todo)
        for (title, notes, valid) in [
            (String(repeating: pair, count: 250), String(repeating: pair, count: 25000), true),
            (String(repeating: pair, count: 251), "", false),
            ("待办", String(repeating: pair, count: 25001), false),
        ] {
            draft.title = title
            draft.notes = notes
            XCTAssertEqual(draft.isValid, valid)
            XCTAssertEqual(TodoNew(title: title, notes: notes).isValid, valid)
        }
    }

    func testForwardedChatPreservesContextAndTruncatesUnicode() {
        let short = InboxItem(text: "明天带材料", note: "核对一下", sourceApp: "微信", chatName: "小组", missed: true)
            .chatText
        XCTAssertTrue(short.contains("微信 · 小组"))
        XCTAssertTrue(short.contains("核对一下"))
        XCTAssertTrue(short.contains("助手漏掉"))
        XCTAssertFalse(short.contains("…"))
        let long = InboxItem(text: String(repeating: "🙂", count: 900)).chatText
        XCTAssertEqual(long.filter { $0 == "🙂" }.count, 300)
        XCTAssertTrue(long.hasSuffix("…"))
    }

    func testDismissalsExpireOnBackendDateAndSurviveEncoding() throws {
        var hidden = UnsureDismissals()
        hidden.hide("a", on: "2027-02-17")
        hidden.hide("b", on: "2027-02-17")
        let restored = try JSONDecoder().decode(UnsureDismissals.self, from: JSONEncoder().encode(hidden))
        XCTAssertTrue(restored.contains("a", on: "2027-02-17"))
        XCTAssertFalse(restored.contains("a", on: "2027-02-18"))
        hidden.hide("c", on: "2027-02-18")
        XCTAssertEqual(hidden.ids, ["c"])
    }

    func testDeleteReasonsAndSourceNames() throws {
        for reason in TodoDeleteReason.allCases {
            let data = try JSONEncoder.assistant().encode(TodoPatch.delete(reason: reason))
            let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: String])
            XCTAssertEqual(object, ["status": "deleted", "reason": reason.rawValue])
            XCTAssertFalse(reason.title.isEmpty)
        }
        var todo = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todo.utf8))
        XCTAssertTrue(ManualEntryText.needsDeleteReason(todo))
        todo.createdBy = "user"
        XCTAssertFalse(ManualEntryText.needsDeleteReason(todo))
        XCTAssertEqual(Names.plugins("share,app"), "我转给助手的 · 我自己记的")
        todo.sourcePlugin = "app"
        todo.sources = nil
        XCTAssertEqual(TodoSourceText.describe(todo), "我自己记的")
    }

    func testElapsedAndOpenErrorsUsePlainWords() {
        XCTAssertEqual(ManualEntryText.elapsed(since: now, now: now.addingTimeInterval(-2)), "已等 0 秒")
        XCTAssertEqual(ManualEntryText.elapsed(since: now, now: now.addingTimeInterval(25)), "已等 25 秒")
        XCTAssertEqual(ManualEntryText.elapsed(since: now, now: now.addingTimeInterval(78)), "已等 1 分 18 秒")
        for code in ["screen_open_not_whitelisted", "screen_open_not_allowed", "screen_open_daily_limit"] {
            let text = ManualEntryText.openError(.screenOpen(code))
            XCTAssertFalse(text.contains(code))
            XCTAssertFalse(text.isEmpty)
        }
        XCTAssertTrue(ManualEntryText.openError(.notFound).contains("后端还没更新"))
    }

    func testStatsEmptyAndMissingDeadlineAreDifferent() throws {
        let json =
            #"{"weeks":[{"week_start":"2027-02-15","caught":0,"with_due":0,"late":0,"median_lead_hours":null,"missed":0,"not_a_todo":0,"recall":null}]}"#
        var week = try XCTUnwrap(
            JSONDecoder.assistant().decode(ExtractionStats.self, from: Data(json.utf8)).weeks.first)
        XCTAssertFalse(week.hasData)
        XCTAssertEqual(week.summary, "这周没有数据")
        week.missed = 2
        week.recall = 0
        XCTAssertTrue(week.hasData)
        XCTAssertTrue(week.summary.contains("自己补了 2 条"))
        XCTAssertEqual(week.recallText, "0%")
        XCTAssertFalse(week.summary.contains("提前"))
        week.caught = 7
        week.recall = 0.777777
        week.medianLeadHours = 0.5
        week.late = 1
        XCTAssertEqual(week.recallText, "78%")
        XCTAssertEqual(week.leadText, "30 分钟")
        XCTAssertTrue(week.summary.contains("1 条发现时已经晚了"))
        week.medianLeadHours = 33.25
        XCTAssertTrue(week.leadText.contains("33.2") || week.leadText.contains("33.3"))
    }

    func testReceiptIgnoresOldOrUnrelatedRuns() {
        let receipt = InboxReceipt(id: "message", text: "原文", date: now)
        func run(_ job: String, _ status: RunStatus, _ end: Date) -> Run {
            Run(
                id: UUID().uuidString, job: job, harness: nil, startedAt: now.addingTimeInterval(-30),
                finishedAt: end, status: status, error: nil, attempt: 1)
        }
        XCTAssertFalse(receipt.finished(by: [run("extract_todos", .succeeded, now.addingTimeInterval(-1))]))
        XCTAssertFalse(receipt.finished(by: [run("fetch:qq_screen", .succeeded, now.addingTimeInterval(20))]))
        XCTAssertFalse(receipt.finished(by: [run("extract_todos", .running, now.addingTimeInterval(20))]))
        XCTAssertTrue(receipt.finished(by: [run("extract_todos", .succeeded, now.addingTimeInterval(20))]))
        XCTAssertEqual(receipt.message.author, .me)
    }
}
