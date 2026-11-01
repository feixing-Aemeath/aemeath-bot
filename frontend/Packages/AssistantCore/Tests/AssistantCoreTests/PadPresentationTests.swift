import XCTest

@testable import AssistantCore

final class PadPresentationTests: XCTestCase {
    let assistant = Bot(id: Bot.assistantID, name: Bot.assistantName, kind: .assistant, look: StickerLook(sticker: nil))
    let shelf = Bot(id: Bot.knowledgeID, name: Bot.knowledgeName, kind: .knowledge, look: StickerLook(sticker: nil))

    func testSavedBotWaitsForPluginsThenFallsBackIfRemoved() {
        let saved = Bot(id: "health", name: "健康Bot", look: StickerLook(sticker: "lily_veil"))
        XCTAssertNil(PadPresentation.selectedBot(storedID: saved.id, bots: [assistant, shelf], settled: false))
        XCTAssertEqual(PadPresentation.selectedBot(storedID: saved.id, bots: [assistant, saved, shelf], settled: false), saved)
        XCTAssertEqual(PadPresentation.selectedBot(storedID: saved.id, bots: [assistant, shelf], settled: true), assistant)
        XCTAssertEqual(PadPresentation.selectedBot(storedID: shelf.id, bots: [assistant, shelf], settled: true), shelf)
        XCTAssertNil(PadPresentation.selectedBot(storedID: saved.id, bots: [], settled: true))
    }

    func testSidebarPreviewUsesWhatTheBotSaid() {
        let date = Date(timeIntervalSince1970: 1_800_000_000)
        let messages = [
            BotMessage(id: "1", author: .bot, date: date, text: "**今天有课**\n第二行"),
            BotMessage(id: "2", author: .me, date: date, text: "几点？"),
            BotMessage(id: "3", author: .app, date: date, text: "正在发送"),
            BotMessage(id: "4", author: .bot, date: date, text: "  "),
        ]
        XCTAssertEqual(PadPresentation.preview(messages: messages, fallback: shelf.intro), "今天有课")
        let report = BotMessage(id: "5", author: .bot, date: date, title: "晨间简报", text: "睡了 8 小时")
        XCTAssertEqual(PadPresentation.preview(messages: messages + [report], fallback: shelf.intro), "晨间简报")
        XCTAssertEqual(PadPresentation.preview(messages: [], fallback: shelf.intro), shelf.intro)
    }

    func testCardsAdaptToTheDetailWidth() {
        // 11-inch iPad: portrait with the sidebar hidden; landscape beside a 280-point sidebar.
        let minimum = PadPresentation.cardMinimumWidth
        XCTAssertEqual(CardPacking.columns(width: 834 - 48, minWidth: minimum, spacing: 12), 2)
        XCTAssertEqual(CardPacking.columns(width: 1194 - 280 - 48, minWidth: minimum, spacing: 12), 3)
        XCTAssertEqual(CardPacking.columns(width: 600 - 48, minWidth: minimum, spacing: 12), 2)
        XCTAssertEqual(CardPacking.columns(width: 500 - 48, minWidth: minimum, spacing: 12), 1)
        // Existing phone and Mac thresholds stay the same; the gap counts too.
        XCTAssertEqual(CardPacking.columns(width: 375 - 32, minWidth: 300, spacing: 12), 1)
        XCTAssertEqual(CardPacking.columns(width: 491, minWidth: 240, spacing: 12), 1)
        XCTAssertEqual(CardPacking.columns(width: 492, minWidth: 240, spacing: 12), 2)
        XCTAssertEqual(CardPacking.columns(width: .infinity, minWidth: minimum, spacing: 12), 1)
        XCTAssertEqual(CardPacking.columns(width: 0, minWidth: minimum, spacing: 12), 1)
    }

    func testInputNamesTheBotAndItsReplyState() {
        XCTAssertEqual(PadPresentation.placeholder(botName: shelf.name, replying: false), "给 知识库Bot 发消息")
        XCTAssertEqual(PadPresentation.placeholder(botName: shelf.name, replying: true), "知识库Bot 正在回复…")
    }
}
