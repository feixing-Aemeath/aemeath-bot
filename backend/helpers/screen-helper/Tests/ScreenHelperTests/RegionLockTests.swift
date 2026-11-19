import CoreGraphics
import Foundation
import XCTest
@testable import ScreenHelper

final class RegionLockTests: XCTestCase {
    // Page 0 of the 2026-11-13 read: the chat ran to the member panel at 0.83 of the window.
    let page0 = Regions(
        list: CGRect(x: 111, y: 0, width: 641, height: 3016),
        header: CGRect(x: 753, y: 0, width: 1043, height: 428),
        chat: CGRect(x: 753, y: 429, width: 1043, height: 2219),
        found: ["nav", "list", "panel", "header", "input"])
    // What page 1 found on its own: a false panel edge cut the chat to 867 pixels.
    let page1Found = Regions(
        list: CGRect(x: 111, y: 0, width: 641, height: 3016),
        header: CGRect(x: 753, y: 0, width: 867, height: 342),
        chat: CGRect(x: 753, y: 343, width: 867, height: 2305),
        found: ["nav", "list", "panel", "header", "input"])
    let start = Date(timeIntervalSince1970: 1_794_584_589)

    func testLaterPagesKeepThePageZeroChatArea() {
        let lock = RegionLock(width: 2164, height: 3016, regions: page0, savedAt: start)
        let regions = RegionLock.regions(locked: lock, width: 2164, height: 3016, now: start + 40) { page1Found }
        XCTAssertEqual(regions, page0)
        XCTAssertEqual(regions.chat.width, 1043)
    }

    func testAResizedWindowOrAnOldLockFindsTheRegionsAgain() {
        let lock = RegionLock(width: 2164, height: 3016, regions: page0, savedAt: start)
        XCTAssertEqual(RegionLock.regions(locked: lock, width: 2164, height: 1280, now: start + 40) { page1Found }, page1Found)
        XCTAssertEqual(RegionLock.regions(locked: lock, width: 2164, height: 3016, now: start + 16 * 60) { page1Found }, page1Found)
        // A lock from the future (the clock moved back) is not trusted either.
        XCTAssertEqual(RegionLock.regions(locked: lock, width: 2164, height: 3016, now: start - 5) { page1Found }, page1Found)
        XCTAssertEqual(RegionLock.regions(locked: nil, width: 2164, height: 3016, now: start) { page1Found }, page1Found)
    }

    func testTheLockSurvivesTheFileBetweenHelperRuns() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let output = Output(directory: folder.path)
        let store = output.regionStore(key: "qq-软件1-4班")
        XCTAssertNil(store.load())
        let lock = RegionLock(width: 2164, height: 3016, regions: page0, savedAt: start)
        store.save(lock)
        XCTAssertEqual(store.load(), lock)
        // Each chat has its own file, apart from the at-top marker.
        XCTAssertNil(output.regionStore(key: "qq-软件1班").load())
        XCTAssertTrue(store.file.lastPathComponent.hasPrefix(".regions-"))
        XCTAssertFalse(output.contains(store.file.path))
    }

    func testAnUnreadableFileIsNoLock() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let store = Output(directory: folder.path).regionStore(key: "qq-x")
        try Data("{\"width\": 10}".utf8).write(to: store.file)
        XCTAssertNil(store.load())
    }
}
