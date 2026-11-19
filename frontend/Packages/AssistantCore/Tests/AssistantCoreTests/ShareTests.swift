import XCTest
@testable import AssistantCore
@testable import AssistantUI

final class ShareTests: XCTestCase {
    func queue() throws -> ShareQueue {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("share-tests-\(UUID())")
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        return ShareQueue(directory: directory)
    }
    let live = ShareConfiguration(baseURL: "https://home.example.ts.net", useMockData: false)

    #if canImport(Security)
    func testTokenMigrationKeepsPrivateItemAndExtensionReadsSharedCopy() throws {
        let legacy = TestShareToken("old-device-token")
        let shared = TestShareToken(nil)
        let app = AppTokenStore(legacy: legacy, shared: shared)
        app.synchronize()
        XCTAssertEqual(legacy.token, "old-device-token")
        XCTAssertEqual(try shared.currentToken(), "old-device-token")
        try app.save("replacement-device-token")
        XCTAssertEqual(legacy.token, "replacement-device-token")
        XCTAssertEqual(shared.token, "replacement-device-token")
        shared.token = "different-shared-token"
        XCTAssertEqual(try app.currentToken(), "replacement-device-token")
        legacy.token = nil
        XCTAssertEqual(try app.currentToken(), "different-shared-token")
    }
    func testSharedKeychainFailurePreservesExistingAppConnection() throws {
        let legacy = TestShareToken("private-token")
        let denied = TestShareToken(nil)
        denied.saveError = KeychainError.unexpectedStatus(-34018)
        let app = AppTokenStore(legacy: legacy, shared: denied)
        app.synchronize()
        XCTAssertEqual(try app.currentToken(), "private-token")
        try app.save("new-private-token")
        XCTAssertEqual(legacy.token, "new-private-token")
        XCTAssertNil(denied.token)
    }
    #endif
    func testContentCombinesTitlesURLsAndTextInOrderWithoutRepeatingRepresentations() {
        let content = ShareContent(fragments: [
            .init(text: " 重复正文 \n", title: "标题甲"),
            .init(text: "重复正文"),
            .init(text: "这是 URL 的备用表示", url: URL(string: "https://example.org/a?q=b")!, title: "页面乙"),
            .init(url: URL(string: "https://example.org/a?q=b")!, title: "页面乙"),
            .init(url: URL(fileURLWithPath: "/tmp/private.txt")),
            .init(text: "\n"),
        ])
        XCTAssertEqual(content.text, "标题甲\n\n重复正文\n\n页面乙\n\nhttps://example.org/a?q=b")
        XCTAssertFalse(content.truncated)
        XCTAssertEqual(ShareContent(fragments: []).text, "")
    }
    func testTruncationMatchesBackendCodePointLimitsAndKeepsMetadata() {
        for input in [String(repeating: "字", count: 8001), String(repeating: "👩🏽‍💻", count: 2500), String(repeating: "e\u{301}", count: 4001)] {
            let content = ShareContent(fragments: [.init(text: input)])
            XCTAssertTrue(content.truncated)
            XCTAssertEqual(content.text.unicodeScalars.count, 8000)
            let item = ShareContent.item(text: input, note: String(repeating: "注", count: 501), source: .init(plugin: "wechat_screen", chatName: String(repeating: "会", count: 90)), missed: true)
            XCTAssertTrue(item.isValid)
            XCTAssertEqual(item.note?.unicodeScalars.count, 500)
            XCTAssertEqual(item.chatName?.count, 80)
            XCTAssertEqual(item.sourceApp, Names.plugin("wechat_screen"))
            XCTAssertTrue(item.missed)
        }
        XCTAssertFalse(ShareContent(fragments: [.init(text: String(repeating: "x", count: 8000))]).truncated)
        XCTAssertFalse(ShareContent.item(text: " \n ", note: "", source: nil, missed: false).isValid)
    }
    func testQueueDeduplicatesPendingTextAcrossRestartsAndSeparatesDestinations() throws {
        let queue = try queue()
        try queue.configure(live)
        let first = try queue.enqueue(InboxItem(text: "正文", note: "备注甲"), configuration: live)
        let duplicate = try ShareQueue(directory: queue.directory).enqueue(InboxItem(text: "正文", note: "备注乙"), configuration: live, now: Date().addingTimeInterval(900))
        XCTAssertEqual(first.id, duplicate.id)
        XCTAssertEqual(duplicate.item.note, "备注甲")
        let other = ShareConfiguration(baseURL: "https://other.example.ts.net", useMockData: false)
        try queue.configure(other)
        let different = try queue.enqueue(InboxItem(text: "正文"), configuration: other)
        XCTAssertNotEqual(first.id, different.id)
        XCTAssertEqual(try queue.entries(destination: live.baseURL).count, 1)
        XCTAssertNil(try queue.claim(id: first.id))
        XCTAssertThrowsError(try queue.enqueue(InboxItem(text: "新文字"), configuration: live))
    }
    func testMockModeNeverEnqueuesOrClaimsExistingRows() throws {
        let queue = try queue()
        try queue.configure(live)
        let entry = try queue.enqueue(InboxItem(text: "留下"), configuration: live)
        var mock = live
        mock.useMockData = true
        try queue.configure(mock)
        XCTAssertThrowsError(try queue.enqueue(InboxItem(text: "模拟时不保存"), configuration: mock))
        XCTAssertNil(try queue.claim(id: entry.id))
        XCTAssertEqual(try queue.entries(destination: live.baseURL).count, 1)
    }
    func testClaimLeaseSuccessAndAcknowledgementPreserveUnsentRows() throws {
        let queue = try queue()
        try queue.configure(live)
        let first = try queue.enqueue(InboxItem(text: "甲"), configuration: live)
        let second = try queue.enqueue(InboxItem(text: "乙"), configuration: live)
        let now = Date()
        XCTAssertNotNil(try queue.claim(id: first.id, now: now))
        XCTAssertNil(try ShareQueue(directory: queue.directory).claim(id: first.id, now: now.addingTimeInterval(5)))
        XCTAssertNotNil(try queue.claim(id: first.id, now: now.addingTimeInterval(61)))
        try queue.release(id: first.id)
        XCTAssertNotNil(try queue.claim(id: first.id, now: now))
        try queue.accepted(id: first.id, response: .init(messageID: "accepted", jobStarted: false), now: now)
        XCTAssertNil(try queue.claim(id: first.id, now: now.addingTimeInterval(1000)))
        XCTAssertEqual(try queue.enqueue(InboxItem(text: "甲"), configuration: live, now: now.addingTimeInterval(599)).id, first.id)
        XCTAssertNotEqual(try queue.enqueue(InboxItem(text: "甲"), configuration: live, now: now.addingTimeInterval(601)).id, first.id)
        try queue.acknowledge(ids: [first.id, second.id])
        let left = try queue.entries(destination: live.baseURL)
        XCTAssertFalse(left.contains { $0.id == first.id })
        XCTAssertTrue(left.contains { $0.id == second.id })
    }
    func testConcurrentWritersDoNotLoseRowsAndOnlyOneClaims() throws {
        let queue = try queue()
        try queue.configure(live)
        let errors = LockedShareErrors()
        DispatchQueue.concurrentPerform(iterations: 40) { index in
            do {
                let independent = ShareQueue(directory: queue.directory)
                _ = try independent.enqueue(InboxItem(text: "并发 \(index)"), configuration: live)
            } catch { errors.record() }
        }
        XCTAssertEqual(errors.count, 0)
        XCTAssertEqual(try queue.entries(destination: live.baseURL).count, 40)
        let entry = try XCTUnwrap(queue.entries(destination: live.baseURL).first)
        let claims = LockedShareErrors()
        DispatchQueue.concurrentPerform(iterations: 20) { _ in
            if (try? ShareQueue(directory: queue.directory).claim(id: entry.id)) != nil { claims.record() }
        }
        XCTAssertEqual(claims.count, 1)
    }
    func testDeliveryRetainsFailuresThenCommitsSuccessWithoutResending() async throws {
        let queue = try queue()
        try queue.configure(live)
        let item = InboxItem(text: "待重试的内容", note: "完整备注", missed: true)
        let entry = try queue.enqueue(item, configuration: live)
        for failure in [APIError.missingToken, .transport("timeout"), .unauthorized, .notFound] {
            let result = await ShareDelivery.send(entry: entry, queue: queue) { _ in throw failure }
            XCTAssertEqual(result, .queued)
            XCTAssertNil(try queue.entries(destination: live.baseURL).first?.receipt)
        }
        let sent = await ShareDelivery.send(entry: entry, queue: queue) { request in
            XCTAssertEqual(request, item)
            return .init(messageID: "server-id", jobStarted: true)
        }
        XCTAssertEqual(sent, .sent)
        let saved = try XCTUnwrap(queue.entries(destination: live.baseURL).first)
        XCTAssertEqual(saved.receipt?.id, "server-id")
        XCTAssertTrue(saved.receipt?.text.contains("完整备注") == true)
        let again = await ShareDelivery.send(entry: saved, queue: queue) { _ in XCTFail("已发送的内容不得再发"); throw APIError.invalidResponse }
        XCTAssertEqual(again, .sent)
    }
    func testUnreadableQueueIsNotReplacedByEmptyState() throws {
        let queue = try queue()
        try queue.configure(live)
        let path = queue.directory.appendingPathComponent("queue.json")
        try Data("damaged".utf8).write(to: path)
        XCTAssertThrowsError(try queue.enqueue(InboxItem(text: "新内容"), configuration: live))
        XCTAssertEqual(try String(contentsOf: path), "damaged")
    }
    func testStatusAndBoundedNetworking() {
        XCTAssertEqual(ShareDeliveryStatus.sent.text, "已转给助理")
        XCTAssertEqual(ShareDeliveryStatus.sent.text(assistantName: "小伙伴"), "已转给小伙伴")
        XCTAssertEqual(ShareDeliveryStatus.queued.text, "已存下，连上 Mac 后会发出去")
        XCTAssertEqual(ShareDeliveryStatus.mock.text, "App 现在用的是模拟数据")
        XCTAssertNil(ShareDeliveryStatus.pendingText(0))
        XCTAssertTrue(ShareDeliveryStatus.pendingText(3)?.contains("3 条") == true)
        XCTAssertFalse(ShareDeliveryStatus.unavailable.shouldClose)
        let session = ShareDelivery.session()
        defer { session.invalidateAndCancel() }
        XCTAssertEqual(session.configuration.timeoutIntervalForResource, 10)
        XCTAssertFalse(session.configuration.waitsForConnectivity)
    }
    @MainActor
    func testMockAndOtherDestinationsDoNotImportRealReceipts() throws {
        let queue = try queue()
        try queue.configure(live)
        let entry = try queue.enqueue(InboxItem(text: "真实内容"), configuration: live)
        try queue.accepted(id: entry.id, response: .init(messageID: "live-only", jobStarted: true))
        for connection in [ConnectionSettings(baseURL: live.baseURL, useMockData: true),
                           ConnectionSettings(baseURL: "https://different.example", useMockData: false)] {
            let store = AppStore(backend: MockBackend(), connection: connection)
            store.manualDefaults = UserDefaults(suiteName: "ShareTests.\(UUID())")!
            store.importShareReceipts(queue)
            XCTAssertTrue(store.inboxReceipts.isEmpty)
            XCTAssertEqual(store.pendingShareCount, 0)
        }
        XCTAssertEqual(try queue.entries(destination: live.baseURL).count, 1)
    }
    @MainActor
    func testAppImportsReceiptsOnceAndKeepsQueueUntilSuccess() async throws {
        let queue = try queue()
        try queue.configure(live)
        let entry = try queue.enqueue(InboxItem(text: "从别处转来的通知"), configuration: live)
        let defaults = UserDefaults(suiteName: "ShareTests.\(UUID())")!
        let store = AppStore(backend: MockBackend(), connection: .init(baseURL: live.baseURL, useMockData: false))
        store.manualDefaults = defaults
        store.shareQueue = queue
        await store.refreshShareInbox(reached: false, pluginsLoaded: false)
        XCTAssertEqual(store.pendingShareCount, 1)
        XCTAssertTrue(store.inboxReceipts.isEmpty)
        try queue.accepted(id: entry.id, response: .init(messageID: "shared", jobStarted: true))
        await store.refreshShareInbox(reached: false, pluginsLoaded: false)
        XCTAssertEqual(store.pendingShareCount, 0)
        XCTAssertEqual(store.inboxReceipts.count, 1)
        XCTAssertTrue(store.messages(store.bots[0]).contains { $0.text.contains("从别处转来的通知") })
        // In a restricted test process UserDefaults may decline disk synchronization. In that
        // case the shared sent receipt stays for another import, and must still never resend.
        XCTAssertTrue(try queue.entries(destination: live.baseURL).allSatisfy { $0.receipt != nil })
        await store.refreshShareInbox(reached: false, pluginsLoaded: false)
        XCTAssertEqual(store.inboxReceipts.count, 1)
        let restored = AppStore(backend: MockBackend(), connection: store.connection)
        restored.manualDefaults = defaults
        restored.shareQueue = queue
        await restored.refreshShareInbox(reached: false, pluginsLoaded: false)
        XCTAssertEqual(restored.inboxReceipts.count, 1)
    }
}
private final class LockedShareErrors: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    var count: Int { lock.lock(); defer { lock.unlock() }; return value }
    func record() { lock.lock(); value += 1; lock.unlock() }
}

#if canImport(Security)
private final class TestShareToken: MutableTokenStore, @unchecked Sendable {
    var token: String?
    var saveError: Error?
    init(_ token: String?) { self.token = token }
    func currentToken() throws -> String? { token }
    func save(_ token: String) throws {
        if let saveError { throw saveError }
        self.token = token
    }
}
#endif
