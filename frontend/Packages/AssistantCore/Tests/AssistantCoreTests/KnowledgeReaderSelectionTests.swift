import XCTest

@testable import AssistantCore

final class KnowledgeReaderSelectionTests: XCTestCase {
    @MainActor
    func testRapidRevocationsSaveInOrderAndKeepTheLatestSelection() async {
        let selection = KnowledgeReaderSelection(chosen: ["Claude Code", "Codex"])
        let started = expectation(description: "first save started")
        var release: CheckedContinuation<Void, Never>?
        var requests: [[String]] = []
        var persisted = selection.chosen
        var active = 0
        var maximumActive = 0
        let save: @MainActor ([String]) async throws -> [String] = { names in
            requests.append(names)
            active += 1
            maximumActive = max(maximumActive, active)
            if requests.count == 1 {
                await withCheckedContinuation { continuation in
                    release = continuation
                    started.fulfill()
                }
            }
            persisted = names
            active -= 1
            return names
        }
        let first = selection.set("Claude Code", false, save: save) { _ in XCTFail() }
        await fulfillment(of: [started], timeout: 2)
        let second = selection.set("Codex", false, save: save) { _ in XCTFail() }
        XCTAssertEqual(selection.chosen, [])
        await Task.yield()
        release?.resume()
        await first.value
        await second.value

        XCTAssertEqual(maximumActive, 1)
        XCTAssertEqual(requests, [["Codex"], []])
        XCTAssertEqual(persisted, [])
        XCTAssertEqual(selection.chosen, [])
    }

    @MainActor
    func testFailedSaveDoesNotDiscardANewerEditAndLaterFailureRestoresSavedState() async {
        let selection = KnowledgeReaderSelection(chosen: ["Claude Code", "Codex"])
        var requests = 0
        var errors = 0
        let save: @MainActor ([String]) async throws -> [String] = { names in
            requests += 1
            if requests != 2 { throw APIError.invalidRequest }
            return names
        }
        let first = selection.set("Claude Code", false, save: save) { _ in errors += 1 }
        let second = selection.set("Codex", false, save: save) { _ in errors += 1 }
        await first.value
        await second.value
        XCTAssertEqual(selection.chosen, [])

        await selection.set("Codex", true, save: save) { _ in errors += 1 }.value
        XCTAssertEqual(selection.chosen, [])
        XCTAssertEqual(errors, 2)
    }
}
