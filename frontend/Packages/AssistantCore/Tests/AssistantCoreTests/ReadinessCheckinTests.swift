import XCTest

@testable import AssistantCore

final class ReadinessCheckinTests: XCTestCase {
    let calendar = DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!)

    func date(_ text: String) -> Date { ISO8601DateFormatter().date(from: text)! }

    func testParsingTheFitnessReadiness() {
        XCTAssertEqual(ReadinessCheckin.parseApple("7"), 7)
        XCTAssertEqual(ReadinessCheckin.parseApple(" 7.5 "), 7.5)
        XCTAssertEqual(ReadinessCheckin.parseApple("7,5"), 7.5)
        XCTAssertEqual(ReadinessCheckin.parseApple("0"), 0)
        XCTAssertEqual(ReadinessCheckin.parseApple("10"), 10)
        for bad in ["", "10.5", "-1", "abc", "nan", "inf"] {
            XCTAssertNil(ReadinessCheckin.parseApple(bad), bad)
        }
    }

    func testValidity() {
        XCTAssertFalse(ReadinessCheckin().isValid)
        XCTAssertTrue(ReadinessCheckin(perceived: 3).isValid)
        XCTAssertTrue(ReadinessCheckin(appleReadiness: 0).isValid)
        XCTAssertFalse(ReadinessCheckin(perceived: 6).isValid)
        XCTAssertFalse(ReadinessCheckin(appleReadiness: 11).isValid)
    }

    func testSamplesForTodayAndAnEarlierDay() throws {
        let now = date("2026-10-20T08:30:15+08:00")
        let batch = ReadinessCheckin(appleReadiness: 7.5, perceived: 4)
            .batch(for: now, now: now, calendar: calendar)
        XCTAssertEqual(batch.samples.map(\.type), ["apple_fitness_readiness", "perceived_readiness"])
        XCTAssertEqual(batch.samples.map(\.value), [7.5, 4])
        XCTAssertTrue(batch.samples.allSatisfy { $0.startAt == now && $0.endAt == now && $0.unit.isEmpty })
        XCTAssertEqual(Set(batch.samples.map(\.id)).count, 2)

        // Logged the next morning for yesterday: filed under yesterday, at the current time.
        let yesterday = date("2026-10-19T12:00:00+08:00")
        let earlier = ReadinessCheckin(perceived: 2).batch(for: yesterday, now: now, calendar: calendar)
        XCTAssertEqual(earlier.samples.count, 1)
        XCTAssertEqual(earlier.samples[0].startAt, date("2026-10-19T08:30:15+08:00"))
        let json = try JSONSerialization.jsonObject(with: JSONEncoder.assistant().encode(earlier))
        let sample = try XCTUnwrap(((json as? [String: Any])?["samples"] as? [[String: Any]])?.first)
        XCTAssertEqual(sample["type"] as? String, "perceived_readiness")
        XCTAssertEqual(sample["value"] as? Double, 2)
        XCTAssertEqual(sample["start_at"] as? String, "2026-10-19T00:30:15Z")
    }

    func testMockBackendTakesCheckinsOnce() async throws {
        let now = date("2026-10-20T08:30:15+08:00")
        let mock = MockBackend(now: now, calendar: calendar, clock: { now })
        let batch = ReadinessCheckin(appleReadiness: 6, perceived: 3).batch(for: now, now: now, calendar: calendar)
        let first = try await mock.uploadHealth(batch)
        XCTAssertEqual(first.accepted, 2)
        let again = try await mock.uploadHealth(batch)
        XCTAssertEqual(again.accepted, 0)
        XCTAssertEqual(again.duplicates, 2)
    }
}
