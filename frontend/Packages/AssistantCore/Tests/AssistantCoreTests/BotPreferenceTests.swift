import XCTest

@testable import AssistantCore

final class BotPreferenceTests: XCTestCase {
    func bot(_ id: String, kind: Bot.Kind = .character) -> Bot {
        Bot(id: id, name: id.uppercased(), kind: kind, look: StickerLook(sticker: nil))
    }

    func testPinGroupsKeepUserOrderThenAppendNewBotsInServerOrder() {
        var prefs = BotPreferences()
        let bots = ["new", "a", "another", "b", "c", "d"].map { bot($0) }
        prefs.setOrder(["gone", "c", "b", "a"])
        for id in ["a", "c", "d"] { prefs[id].pinned = true }
        XCTAssertEqual(prefs.ordered(bots).map(\.id), ["c", "a", "d", "b", "new", "another"])
    }

    func testHidingIncludingAssistantDoesNotChangeSidebarOrServerName() {
        var prefs = BotPreferences()
        let assistant = bot("assistant", kind: .assistant), other = bot("other")
        let bots = [assistant, other, bot("source", kind: .source)]
        prefs["assistant"].customName = "  小助手  "
        prefs["assistant"].visible = false
        XCTAssertEqual(prefs.displayName(assistant), "小助手")
        XCTAssertEqual(assistant.name, "ASSISTANT")
        XCTAssertEqual(prefs.dashboardBots(bots), [other])
        XCTAssertEqual(prefs.ordered(bots), bots)
        prefs["other"].visible = false
        XCTAssertTrue(prefs.dashboardBots(bots).isEmpty)
        prefs["assistant"].customName = " \n"
        XCTAssertEqual(prefs.displayName(assistant), assistant.name)
    }

    func testLayoutResetAndPersistenceKeepOnlyRenames() throws {
        let suite = "BotPreferencesTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        var prefs = BotPreferences()
        prefs["a"].customName = "小助手"
        prefs["a"].pinned = true
        prefs["b"].visible = false
        prefs.setOrder(["gone", "b", "a"])
        prefs.save(to: defaults)
        var loaded = BotPreferences.load(from: defaults)
        XCTAssertEqual(loaded, prefs)
        loaded.resetLayout()
        loaded.save(to: defaults)
        let restored = BotPreferences.load(from: defaults)
        XCTAssertEqual(restored, loaded)
        XCTAssertEqual(restored["a"].customName, "小助手")
        for entry in restored.entries.values {
            XCTAssertNil(entry.position)
            XCTAssertFalse(entry.pinned)
            XCTAssertTrue(entry.visible)
        }
        XCTAssertEqual(restored.merging(prefs.entries), restored)
        loaded.resetLayout()
        XCTAssertEqual(loaded, restored)
    }

    func testMergeUsesNewestWholeEntryAndKeepsBotsFromEitherDevice() {
        let older = BotPreferences.Entry(pinned: true, customName: "旧名字", updatedAt: "2026-11-18T00:00:00.123450Z")
        let newer = BotPreferences.Entry(visible: false, updatedAt: "2026-11-18T00:00:00.123451Z")
        let local = BotPreferences().merging(["health": older, "grades": newer, "only_local": older])
        let remote = ["health": newer, "grades": older, "new_bot": newer]
        let merged = local.merging(remote)
        XCTAssertEqual(merged.entries, ["health": newer, "grades": newer, "only_local": older, "new_bot": newer])
        XCTAssertEqual(merged.merging(remote), merged)
        var tie = newer
        tie.customName = "服务端名字"
        XCTAssertEqual(merged.merging(["health": tie])["health"], tie)
    }

    func testClearingLastRenameBeatsOfflineCopyEvenWithClockBehind() {
        let renamed = BotPreferences.Entry(customName: "旧名字", updatedAt: "2099-06-01T12:34:56.123999Z")
        let offline = BotPreferences().merging(["assistant": renamed])
        var edited = offline
        edited["assistant"].customName = ""
        XCTAssertGreaterThan(edited["assistant"].updatedAt, renamed.updatedAt)
        XCTAssertEqual(edited.merging(offline.entries), edited)
        XCTAssertEqual(offline.merging(edited.entries), edited)
        XCTAssertEqual(edited.displayName(for: "assistant", fallback: "原名"), "原名")
    }

    func testRapidEditsAlwaysAdvanceTheTimestamp() {
        let future = BotPreferences.Entry(updatedAt: "2099-06-01T00:00:00.000999Z")
        var prefs = BotPreferences().merging(["health": future])
        for _ in 0..<100 {
            let before = prefs["health"].updatedAt
            prefs["health"].visible.toggle()
            guard prefs["health"].updatedAt > before else {
                return XCTFail("Edit did not advance past \(before)")
            }
        }
    }

    func testLegacyPreferencesSeedMissingBotsWithoutOverwritingDatedEdits() throws {
        let suite = "BotPreferencesTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let json = #"{"entries":{"new_bot":{"customName":"旧设备","pinned":true,"position":2,"visible":false},"health":{"customName":"旧名字","pinned":true,"visible":true}}}"#
        defaults.set(Data(json.utf8), forKey: "assistant.botPreferences.v1")
        let local = BotPreferences.load(from: defaults)
        var remote = BotPreferences()
        remote["health"].customName = "新名字"
        let merged = local.merging(remote.entries)
        XCTAssertEqual(merged["new_bot"].customName, "旧设备")
        XCTAssertTrue(merged["new_bot"].pinned)
        XCTAssertFalse(merged["new_bot"].visible)
        XCTAssertEqual(merged["new_bot"].position, 2)
        XCTAssertEqual(merged["health"], remote["health"])
        merged.save(to: defaults)
        XCTAssertEqual(BotPreferences.load(from: defaults), merged)
    }
}
