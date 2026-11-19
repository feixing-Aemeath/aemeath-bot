import XCTest

@testable import AssistantCore

final class GoalRepliesTests: XCTestCase {
    func testDecodesOldMessagesAndOptionalReplies() throws {
        let base = """
            {"id":"q","bot_id":"assistant","author":"bot","text":"接着来？",
             "status":"ready","error":null,"created_at":"2026-11-10T07:40:00+08:00",
             "updated_at":"2026-11-10T07:40:00+08:00"}
            """
        let old = try JSONDecoder.assistant().decode(ChatMessage.self, from: Data(base.utf8))
        XCTAssertNil(old.replies)
        let json = base.dropLast() + ",\"replies\":[\"好\",\"改天\"]}"
        let question = try JSONDecoder.assistant().decode(ChatMessage.self, from: Data(json.utf8))
        XCTAssertEqual(question.replies, ["好", "改天"])
        XCTAssertEqual(BotChat.replyQuestion([question], now: question.updatedAt)?.replies, ["好", "改天"])
    }

    func testOnlyLastReadyBotLineOffersReplies() {
        let now = Date()
        let question = ChatMessage(id: "q", botID: "assistant", author: .bot, text: "接着来？",
                                   status: .ready, createdAt: now, updatedAt: now, replies: ["好"])
        var user = question
        user.id = "u"
        user.author = .me
        user.createdAt = now.addingTimeInterval(1)
        XCTAssertEqual(BotChat.replyQuestion([question])?.id, "q")
        XCTAssertNil(BotChat.replyQuestion([user, question]))
        var pending = question
        pending.id = "p"
        pending.status = .pending
        pending.createdAt = now.addingTimeInterval(2)
        XCTAssertNil(BotChat.replyQuestion([question, pending]))
    }

    func testYesterdayQuestionHasNoRepliesEvenWhenEarlierDayIsOpened() {
        let now = Date()
        let yesterday = now.addingTimeInterval(-86400)
        let question = ChatMessage(id: "q", botID: "assistant", author: .bot, text: "接着来？",
                                   status: .ready, createdAt: yesterday, updatedAt: yesterday, replies: ["好"])
        XCTAssertNil(BotChat.replyQuestion([question], visible: BotChat.messages([question]), now: now))
    }

    func testRepliesStayWithQuestionWhenDigestFollowsIt() {
        let now = Date()
        let question = ChatMessage(id: "q", botID: "assistant", author: .bot, text: "接着来？",
                                   status: .ready, createdAt: now, updatedAt: now, replies: ["好"])
        let digest = BotMessage(id: "digest", author: .bot, date: now.addingTimeInterval(60), text: "今天的总结")
        let visible = BotChat.messages([question]) + [digest]
        let offered = BotChat.replyQuestion([question], visible: visible, now: now)
        XCTAssertEqual(visible.filter { $0.id == offered?.id }.map(\.id), ["q"])
        XCTAssertNil(BotChat.replyQuestion([question], visible: [digest], now: now))
    }

    func testGoalSourceUsesTheSameFieldsAsEveryOtherTodo() throws {
        var todo = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todo.utf8))
        todo.sourcePlugin = "goals"
        todo.sources = [TodoSource(messageID: "s", plugin: "goals", chatName: "高数85分", sender: "", sentAt: Date())]
        XCTAssertEqual(Names.plugin("goals"), "目标")
        XCTAssertEqual(TodoSourceText.describe(todo), "目标 · 高数85分")
    }

    func testGoalsAreAppOnly() throws {
        var todo = try JSONDecoder.assistant().decode(Todo.self, from: Data(Fixtures.todo.utf8))
        todo.sourcePlugin = "goals"
        XCTAssertEqual(SyncState(todo), .appOnly)
        todo.sourcePlugin = "chat,goals"
        XCTAssertEqual(SyncState(todo), .appOnly)
    }
}
