import XCTest

@testable import AssistantCore

final class MascotAnimationTests: XCTestCase {
    let names = ["lily_veil", "shouanren", "red_ribbon", "moning", "luosela", "aemeath"]

    func testCatalogAndStateChoices() {
        XCTAssertEqual(MascotAnimation.allCases.count, 24)
        for name in names {
            let acts = MascotAnimation.all(for: name)
            XCTAssertEqual(acts.count, 4)
            XCTAssertTrue(acts.contains(MascotAnimation.working(for: name)!))
            XCTAssertTrue(acts.contains(MascotAnimation.celebration(for: name)!))
            var player = MascotAnimationPlayer(seed: 42)
            let work = player.sample(sticker: name, state: .working, at: 100, localHour: 12)
            XCTAssertEqual(work?.animation, MascotAnimation.working(for: name))
            XCTAssertEqual(work?.looping, true)
            XCTAssertEqual(player.sample(sticker: name, state: .working, at: 700, localHour: 12)?.animation, work?.animation)
            let done = player.sample(sticker: name, state: .done, at: 701, localHour: 12)
            XCTAssertEqual(done?.animation, MascotAnimation.celebration(for: name))
            XCTAssertEqual(done?.elapsed, 0)
            XCTAssertNil(player.sample(sticker: name, state: .done, at: 705, localHour: 12))
            XCTAssertNil(player.sample(sticker: name, state: .error, at: 706, localHour: 12))
        }
    }

    func testSleepAndLocalHourBoundaries() {
        var player = MascotAnimationPlayer(seed: 1)
        for hour in [23, 0, 5] {
            XCTAssertEqual(player.sample(sticker: "lily_veil", state: .idle, at: Double(hour), localHour: hour)?.animation, .sleep)
        }
        for hour in [6, 22] {
            XCTAssertNil(player.sample(sticker: "lily_veil", state: .idle, at: Double(hour + 100), localHour: hour))
        }
        XCTAssertEqual(player.sample(sticker: "lily_veil", state: .offline, at: 200, localHour: 12)?.animation, .sleep)
        XCTAssertEqual(player.sample(sticker: "lily_veil", state: .working, at: 201, localHour: 23)?.animation, .heartbeat)
        for name in names.dropFirst() {
            XCTAssertNil(player.sample(sticker: name, state: .offline, at: 300, localHour: 23))
            XCTAssertNil(player.sample(sticker: name, state: .idle, at: 301, localHour: 23))
        }
    }

    func testIdleGapsAreVariedAndNeverRepeatConsecutively() {
        for name in names {
            var player = MascotAnimationPlayer(seed: MascotMotion.seed(for: name))
            var time = 1000.0
            XCTAssertNil(player.sample(sticker: name, state: .idle, at: time, localHour: 12))
            var previous: MascotAnimation?
            var seen: Set<MascotAnimation> = []
            var gaps: Set<Int> = []
            for _ in 0..<60 {
                let next = player.nextIdleAt!
                XCTAssertTrue((20...40).contains(next - time))
                gaps.insert(Int(next - time))
                XCTAssertNil(player.sample(sticker: name, state: .idle, at: next - 0.01, localHour: 12))
                let chosen = player.sample(sticker: name, state: .idle, at: next, localHour: 12)!
                XCTAssertNotEqual(chosen.animation, previous)
                XCTAssertTrue(MascotAnimation.idle(for: name).contains(chosen.animation))
                XCTAssertNil(player.sample(sticker: name, state: .idle, at: next + chosen.animation.duration + 0.01, localHour: 12))
                seen.insert(chosen.animation)
                previous = chosen.animation
                time = next
            }
            XCTAssertGreaterThan(seen.count, 1)
            XCTAssertGreaterThan(gaps.count, 10)
        }
    }

    func testPokesPreviewAndStateInterruptions() {
        for name in names {
            var player = MascotAnimationPlayer(seed: 7)
            var previous: MascotAnimation?
            for index in 0..<30 {
                let time = 1000 + Double(index) * 10
                let request = MascotAnimationRequest(at: Date(timeIntervalSince1970: time))
                let act = player.sample(sticker: name, state: .working, at: time, localHour: 12, request: request)!
                XCTAssertNotEqual(act.animation, previous)
                XCTAssertEqual(act.animation.sticker, name)
                XCTAssertFalse(act.looping)
                XCTAssertEqual(player.sample(sticker: name, state: .working, at: time + 0.5, localHour: 12, request: request)?.elapsed, 0.5)
                XCTAssertEqual(player.sample(sticker: name, state: .working, at: time + 7, localHour: 12, request: request)?.animation, MascotAnimation.working(for: name))
                previous = act.animation
            }
            for act in MascotAnimation.all(for: name) {
                let request = MascotAnimationRequest(act, at: Date(timeIntervalSince1970: 2000))
                XCTAssertEqual(player.sample(sticker: name, state: .idle, at: 2000, localHour: 12, request: request)?.animation, act)
                XCTAssertNil(player.sample(sticker: name, state: .error, at: 2001, localHour: 12, request: request))
            }
        }
    }

    func testGatesUnknownStickerAndStaleOrWrongRequests() {
        var player = MascotAnimationPlayer(seed: 11)
        let request = MascotAnimationRequest(.sleep, at: Date(timeIntervalSince1970: 100))
        XCTAssertNil(player.sample(sticker: "unknown", state: .working, at: 100, localHour: 23, request: request))
        XCTAssertNil(player.sample(sticker: "aemeath", state: .idle, at: 100, localHour: 12, request: request))
        XCTAssertNil(player.sample(sticker: "lily_veil", state: .idle, at: 110, localHour: 12, request: request))
        for state in MascotState.allCases {
            XCTAssertNil(player.sample(sticker: "lily_veil", state: state, at: 200, localHour: 23, enabled: false))
            XCTAssertNil(player.sample(sticker: "lily_veil", state: state, at: 200, localHour: 23, reduceMotion: true))
            XCTAssertNil(player.sample(sticker: "lily_veil", state: state, at: 200, localHour: 23, active: false))
            XCTAssertNil(player.nextIdleAt)
        }
        XCTAssertNil(player.sample(sticker: "aemeath", state: .idle, at: 500, localHour: 12))
        XCTAssertGreaterThanOrEqual(player.nextIdleAt!, 520)
    }

    func testFramesAndLoopBoundaries() {
        let base = MascotFrame(pose: MascotPose())
        for act in MascotAnimation.allCases {
            for step in 0...100 {
                let play = MascotAnimationPlayback(act, elapsed: Double(step) * 0.2, looping: true)
                XCTAssertTrue((0..<1).contains(play.phase))
                XCTAssertTrue((0...1).contains(play.visibility))
                let frame = play.applying(to: base)
                XCTAssertTrue(frame.pose.offsetY.isFinite)
                XCTAssertTrue((0...1).contains(frame.eyes.closure))
            }
            XCTAssertEqual(MascotAnimationPlayback(act, elapsed: act.duration).visibility, 0)
        }
        XCTAssertEqual(MascotAnimationPlayback(.sleep, elapsed: 3).applying(to: base).eyes.closure, 1)
        XCTAssertGreaterThan(MascotAnimationPlayback(.writing, elapsed: 1).applying(to: base).eyes.offsetY, 0)
        XCTAssertEqual(MascotAnimationPlayback(.tokens, elapsed: 4, looping: true).phase, 0)
    }
}
