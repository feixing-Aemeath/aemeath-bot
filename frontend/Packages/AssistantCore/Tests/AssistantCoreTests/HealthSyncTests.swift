import XCTest
@testable import AssistantCore

final class HealthSyncTests: XCTestCase {
    func testAnchorsAreScopedToBackend() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "HealthSyncTests-\(UUID().uuidString)"))
        let a = HealthSyncState(backend: "https://a.example.test/", defaults: defaults)
        let b = HealthSyncState(backend: "https://b.example.test/", defaults: defaults)
        let now = Date()
        a.saveAnchor(Data([1]), for: "steps")
        a.finished(at: now, count: 1)
        XCTAssertNil(b.anchor(for: "steps"))
        XCTAssertNil(b.lastSync)
        b.saveAnchor(Data([2]), for: "steps")
        b.finished(at: now, count: 2)
        XCTAssertEqual(b.anchor(for: "steps"), Data([2]))
        XCTAssertEqual(a.anchor(for: "steps"), Data([1]))
        let reloaded = HealthSyncState(backend: "https://a.example.test/", defaults: defaults)
        XCTAssertEqual(reloaded.anchor(for: "steps"), Data([1]))
        XCTAssertEqual(reloaded.lastCount, 1)
    }

    /// The backend refuses a batch that adds and deletes one sample, which would hold the type.
    func testASampleDeletedInTheSamePageIsOnlySentAsDeleted() {
        let date = Date(timeIntervalSince1970: 0)
        let sample = HealthSample(
            id: "S", type: "step_count", value: 1, unit: "count", startAt: date, endAt: date, source: "手表")
        let page = HealthUploadPage(samples: [sample], deleted: ["S"], anchor: Data(), addedCount: 1)
        XCTAssertEqual(page.samples, [])
        XCTAssertEqual(page.deleted, ["S"])
    }

    @MainActor
    func testDeletedOnlyPageIsUploadedBeforeAnchorAdvances() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "HealthSyncTests-\(UUID().uuidString)"))
        let progress = HealthSyncState(backend: "https://a.example.test", defaults: defaults)
        progress.saveAnchor(Data([1]), for: "steps")
        let page = HealthUploadPage(samples: [], deleted: ["S"], anchor: Data([2]), addedCount: 0)
        var uploaded = [String]()
        try await page.upload(kind: "steps", progress: progress) { batch in
            XCTAssertEqual(progress.anchor(for: "steps"), Data([1]))
            uploaded.append(String(decoding: try JSONEncoder.assistant().encode(batch), as: UTF8.self))
        }
        XCTAssertEqual(uploaded, [#"{"deleted":["S"],"samples":[]}"#])
        XCTAssertEqual(progress.anchor(for: "steps"), Data([2]))
        XCTAssertTrue(page.hasMore(pageSize: 1))
        progress.saveAnchor(Data([1]), for: "steps")
        do {
            try await page.upload(kind: "steps", progress: progress) { _ in throw APIError.server(status: 500) }
            XCTFail("Deletion upload should fail")
        } catch {}
        XCTAssertEqual(progress.anchor(for: "steps"), Data([1]))
    }

    @MainActor
    func testDeletionBatchFailureKeepsAnchorForRetry() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "HealthSyncTests-\(UUID().uuidString)"))
        let progress = HealthSyncState(backend: "https://a.example.test", defaults: defaults)
        progress.saveAnchor(Data([1]), for: "steps")
        let ids = (0..<2001).map { "sample-\($0)" }
        let page = HealthUploadPage(samples: [], deleted: ids, anchor: Data([2]), addedCount: 0)
        var calls = 0
        do {
            try await page.upload(kind: "steps", progress: progress) { batch in
                XCTAssertLessThanOrEqual(batch.deleted.count, 1000)
                calls += 1
                if calls == 2 { throw APIError.server(status: 500) }
            }
            XCTFail("second batch should fail")
        } catch {}
        XCTAssertEqual(progress.anchor(for: "steps"), Data([1]))
        var retried = [String]()
        try await page.upload(kind: "steps", progress: progress) { batch in
            XCTAssertEqual(progress.anchor(for: "steps"), Data([1]))
            XCTAssertLessThanOrEqual(batch.deleted.count, 1000)
            retried += batch.deleted
        }
        XCTAssertEqual(retried, ids)
        XCTAssertEqual(progress.anchor(for: "steps"), Data([2]))
    }

    @MainActor
    func testForegroundRetriesFailedLaunchUploadWithoutNewHealthEvents() async throws {
        let center = NotificationCenter()
        let foreground = Notification.Name("synthetic.foreground")
        let uploaded = expectation(description: "foreground upload")
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "HealthSyncTests-\(UUID().uuidString)"))
        let progress = HealthSyncState(backend: "https://a.example.test", defaults: defaults)
        var online = false
        var attempts = 0
        let lifecycle = HealthSyncLifecycle(center: center, foreground: foreground) {
            attempts += 1
            guard online else { return }
            progress.finished(at: Date(), count: 1)
            uploaded.fulfill()
        }
        await lifecycle.start()
        XCTAssertEqual(attempts, 1)
        XCTAssertNil(progress.lastSync)
        online = true
        center.post(name: foreground, object: nil)
        await fulfillment(of: [uploaded], timeout: 0.2)
        XCTAssertEqual(attempts, 2)
        XCTAssertEqual(progress.lastCount, 1)
        withExtendedLifetime(lifecycle) {}
    }
}
