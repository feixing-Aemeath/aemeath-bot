import XCTest
@testable import ScreenHelper

final class ChatReaderTests: XCTestCase {
    func testShorterOrTruncatedNamesAreNeverClicked() {
        let chat = "软件工程2班".matchKey
        for line in ["软件工程", "软件工程…", "软件工程2班…", "软件工程2班家长群"] {
            XCTAssertFalse(ChatReader.names(chat: chat, line: line), line)
        }
    }

    func testDigitsInAnotherChatNameAreNotMistakenForTheTime() {
        XCTAssertFalse(ChatReader.names(chat: "软件工程2班".matchKey, line: "软件工程2班2105"))
    }

    func testWholeNamesAndASeparateClockAreAccepted() {
        let chat = "软件工程2班".matchKey
        XCTAssertTrue(ChatReader.names(chat: chat, line: "软件工程 2 班"))
        XCTAssertTrue(ChatReader.names(chat: chat, line: "软件工程2班 21:05"))
        XCTAssertTrue(ChatReader.names(chat: chat, line: "软件工程2班 昨天20:59"))
        XCTAssertTrue(ChatReader.names(chat: chat, line: "软件工程2班21:05"))  // OCR dropped the space
    }

    func block(_ text: String, x: Double = 0.2, y: Double = 0.1, w: Double = 0.4) -> Block {
        Block(text: text, x: x, y: y, w: w, h: 0.02, confidence: 1)
    }

    func testDuplicateChatNamesAreRejectedBeforeClicking() {
        let blocks = [block("软件工程2班"), block("20:41", x: 0.88, w: 0.1),
                      block("软件工程2班", y: 0.3), block("20:40", x: 0.88, y: 0.3, w: 0.1)]
        XCTAssertThrowsError(try ChatReader.row(chat: "软件工程2班", blocks: blocks))
    }

    func testMessageTextCannotBeSelectedAsAConversationName() {
        let blocks = [block("其他群"), block("20:41", x: 0.88, w: 0.1),
                      block("软件工程2班", y: 0.14)]
        XCTAssertThrowsError(try ChatReader.row(chat: "软件工程2班", blocks: blocks))
    }

    func testAListTimeWithAStrayDigitStillMarksTheHeaderRow() throws {
        let blocks = [block("信息学院26级"), block("昨天20:411", x: 0.81, w: 0.16),
                      block("周老师：@全体成员", y: 0.14)]
        XCTAssertEqual(try ChatReader.row(chat: "信息学院26级", blocks: blocks).text, "信息学院26级")
    }

    func testOneWholeHeaderSelectsTheRequestedRow() throws {
        let blocks = [block("软件工程2班"), block("20:41", x: 0.88, w: 0.1)]
        XCTAssertEqual(try ChatReader.row(chat: "软件工程2班", blocks: blocks).text, "软件工程2班")
    }
}

extension ChatReaderTests {
    func testSplitNameBlocksAreJoinedOnlyOnTheHeaderRow() throws {
        let blocks = [block("软件工程", w: 0.2), block("２班", x: 0.42, w: 0.12),
                      block("20:41", x: 0.88, w: 0.1),
                      block("软件工程2班家长群", y: 0.16)]
        XCTAssertEqual(try ChatReader.row(chat: "软件工程2班", blocks: blocks).text, "软件工程２班")
    }

    func testEmojiAndSpacesDoNotChangeWholeNameButSuffixesStillDo() {
        let key = "软件工程2班".matchKey
        XCTAssertTrue(ChatReader.names(chat: key, line: "软件 工程２班👩‍💻️"))
        for text in ["软件工程3班", "软件工程2班家长群", "软件工程2班…", "软件工程2…"] {
            XCTAssertFalse(ChatReader.names(chat: key, line: text))
        }
    }

    func testTitlesAcceptOnlyCompleteNamesWithOptionalMemberCounts() {
        XCTAssertEqual(ChatReader.title(chat: "软件工程2班", lines: ["软件工程２班（56）"]), "软件工程２班")
        XCTAssertEqual(ChatReader.title(chat: "课程(2026)", lines: ["课程(2026)"]), "课程(2026)")
        XCTAssertEqual(ChatReader.title(chat: "软件工程2班", lines: ["软件工程", "2班"]), "软件工程2班")
        XCTAssertNil(ChatReader.title(chat: "软件工程2班", lines: ["软件工程2班家长群(56)"]))
        XCTAssertNil(ChatReader.title(chat: "软件工程2班", lines: ["软件工程2班…"]))
    }

    func testListSearchScrollsToAnInitiallyInvisibleWholeName() async throws {
        var index = 0
        var directions: [Double] = []
        let pages = [[block("其他群"), block("20:41", x: 0.88, w: 0.1)],
                     [block("软件工程2班"), block("20:41", x: 0.88, w: 0.1)]]
        let found = try await ChatReader.findRow(chat: "软件工程2班", read: { pages[index] }, scroll: {
            directions.append($0); index = 1
        })
        XCTAssertEqual(found.text, "软件工程2班")
        XCTAssertEqual(directions, [1])
    }

    func testListSearchChangesDirectionAndStopsAtRepeatedViewport() async {
        var directions: [Double] = []
        do {
            _ = try await ChatReader.findRow(chat: "软件工程2班", read: {
                [self.block("其他群"), self.block("20:41", x: 0.88, w: 0.1)]
            }, scroll: { directions.append($0) })
            XCTFail("missing target")
        } catch { XCTAssertEqual((error as? HelperError)?.code, "screen_chat_not_in_list") }
        XCTAssertEqual(directions, [1, -1])
    }

    func testNoisyListSearchStillHasAnIndependentBound() async {
        var reads = 0, scrolls = 0
        do {
            _ = try await ChatReader.findRow(chat: "软件工程2班", read: {
                reads += 1
                return [self.block("其他群\(reads)"), self.block("20:41", x: 0.88, w: 0.1)]
            }, scroll: { _ in scrolls += 1 })
            XCTFail("missing target")
        } catch { XCTAssertEqual((error as? HelperError)?.code, "screen_chat_not_in_list") }
        XCTAssertEqual(scrolls, 36)
        XCTAssertEqual(reads, 37)
    }

    func testAmbiguousListAndMidScanLockStopImmediately() async {
        for code in [HelperError.chatAmbiguous, HelperError.locked] {
            var scrolls = 0
            do {
                _ = try await ChatReader.findRow(chat: "软件工程2班", read: {
                    if scrolls > 0 { throw code }
                    return [self.block("其他群"), self.block("20:41", x: 0.88, w: 0.1)]
                }, scroll: { _ in scrolls += 1 })
                XCTFail("should abort")
            } catch { XCTAssertEqual((error as? HelperError)?.code, code.code) }
            XCTAssertEqual(scrolls, 1)
        }
    }
}
