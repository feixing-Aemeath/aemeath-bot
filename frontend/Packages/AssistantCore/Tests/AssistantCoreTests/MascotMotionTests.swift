import XCTest

@testable import AssistantCore

final class MascotMotionTests: XCTestCase {
    let seed = MascotMotion.seed(for: "lily_veil")
    /// A realistic clock: seconds since 1970, as MascotView passes them.
    let start = 1_791_536_400.0

    /// When the eyes are mostly shut (closure 0.5 or more), sampled every 5 ms.
    func closedSpans(seed: UInt64, from: Double, seconds: Double) -> [(start: Double, end: Double)] {
        var spans: [(start: Double, end: Double)] = []
        var opened: Double?
        for step in 0...Int(seconds / 0.005) {
            let time = from + Double(step) * 0.005
            let shut = MascotMotion.blink(at: time, seed: seed) >= 0.5
            if shut, opened == nil { opened = time }
            if !shut, let begin = opened {
                spans.append((begin, time))
                opened = nil
            }
        }
        return spans
    }

    // MARK: Seeds

    func testSeedsAreStableAndDifferByName() {
        // FNV-1a: the empty name gives the offset basis, the same name always the same seed.
        XCTAssertEqual(MascotMotion.seed(for: ""), 0xcbf2_9ce4_8422_2325)
        XCTAssertEqual(MascotMotion.seed(for: "aemeath"), MascotMotion.seed(for: "aemeath"))
        let names = ["aemeath", "luosela", "moning", "shouanren", "lily_veil", "red_ribbon", "placeholder"]
        XCTAssertEqual(Set(names.map(MascotMotion.seed(for:))).count, names.count)
    }

    // MARK: Blinking

    func testBlinkTimingAndDutyCycle() {
        let seconds = 600.0
        let spans = closedSpans(seed: seed, from: start, seconds: seconds)
        XCTAssertGreaterThan(spans.count, 100)
        // Each blink keeps the eyes shut about 0.12–0.15 s (the second of a double a bit less).
        for span in spans {
            XCTAssertGreaterThan(span.end - span.start, 0.09)
            XCTAssertLessThan(span.end - span.start, 0.16)
        }
        // Blinks (a double blink counts once) come 2.5–6 s apart.
        var groups: [Double] = []
        var doubles = 0
        for span in spans {
            if let last = groups.last, span.start - last < 0.6 {
                doubles += 1
            } else {
                groups.append(span.start)
            }
        }
        let gaps = zip(groups.dropFirst(), groups).map { $0 - $1 }
        XCTAssertGreaterThanOrEqual(gaps.min()!, 2.4)
        XCTAssertLessThanOrEqual(gaps.max()!, 6.1)
        XCTAssertGreaterThan(doubles, 3)
        XCTAssertLessThan(Double(doubles), Double(groups.count) * 0.4)
        // Eyes are open almost all of the time.
        let shut = spans.map { $0.end - $0.start }.reduce(0, +) / seconds
        XCTAssertGreaterThan(shut, 0.02)
        XCTAssertLessThan(shut, 0.06)
        // Closure stays within 0...1 and blends smoothly in and out.
        let samples = (0..<20_000).map { MascotMotion.blink(at: start + Double($0) * 0.003, seed: seed) }
        XCTAssertTrue(samples.allSatisfy { (0...1).contains($0) })
        XCTAssertTrue(samples.contains { $0 > 0.1 && $0 < 0.9 })
    }

    func testBlinksAreDeterministicAndDesynchronised() {
        for step in 0..<500 {
            let time = start + Double(step) * 0.037
            XCTAssertEqual(MascotMotion.blink(at: time, seed: seed), MascotMotion.blink(at: time, seed: seed))
        }
        let a = closedSpans(seed: MascotMotion.seed(for: "aemeath"), from: start, seconds: 60).map(\.start)
        let b = closedSpans(seed: MascotMotion.seed(for: "moning"), from: start, seconds: 60).map(\.start)
        let together = a.filter { time in b.contains { abs($0 - time) < 0.3 } }
        XCTAssertLessThan(together.count, a.count / 2)
    }

    // MARK: Looking around

    func testIdleGlances() {
        let samples = (0..<24_000).map { MascotMotion.glance(at: start + Double($0) * 0.01, seed: seed) }
        XCTAssertTrue(samples.allSatisfy { $0.length <= 1 + 1e-9 })
        // Mostly ahead, now and then well off to a side, and back.
        let ahead = samples.filter { $0.length < 0.01 }.count
        XCTAssertGreaterThan(Double(ahead) / Double(samples.count), 0.4)
        XCTAssertTrue(samples.contains { $0.x > 0.3 } && samples.contains { $0.x < -0.3 })
        // Glances look more to the sides than up and down.
        XCTAssertTrue(samples.allSatisfy { abs($0.y) <= 0.6 + 1e-9 })
        // Drifts smoothly: no jumps between frames 10 ms apart.
        let jumps = zip(samples.dropFirst(), samples).map { hypot($0.x - $1.x, $0.y - $1.y) }
        XCTAssertLessThan(jumps.max()!, 0.1)
        let again = MascotMotion.glance(at: start + 12.34, seed: seed)
        XCTAssertEqual(MascotMotion.glance(at: start + 12.34, seed: seed), again)
    }

    func testWhereTheEyesWantToLook() {
        let pointer = MascotGaze(x: 0.8, y: -0.2)
        XCTAssertEqual(MascotMotion.target(at: start, state: .idle, seed: seed, pointer: pointer), pointer)
        XCTAssertEqual(MascotMotion.target(at: start, state: .working, seed: seed, pointer: pointer), pointer)
        // Asleep: ahead, whatever happens.
        XCTAssertEqual(MascotMotion.target(at: start, state: .offline, seed: seed, pointer: pointer), .ahead)
        // Talking without a pointer: looks at you.
        let talk = MascotCue(.talk, at: Date(timeIntervalSince1970: start))
        XCTAssertEqual(MascotMotion.target(at: start + 0.5, state: .idle, seed: seed, cue: talk), .ahead)
        // Working looks down at the work, an error down and away.
        XCTAssertGreaterThan(MascotMotion.target(at: start, state: .working, seed: seed).y, 0.4)
        let error = MascotMotion.target(at: start, state: .error, seed: seed)
        XCTAssertGreaterThan(error.y, 0.5)
        XCTAssertLessThan(error.x, 0)
        let idle = MascotMotion.target(at: start + 3, state: .idle, seed: seed)
        XCTAssertEqual(idle, MascotMotion.glance(at: start + 3, seed: seed))
    }

    // MARK: Gaze

    func testGazeTowardsAPointIsClampedAndPointsThere() {
        let right = MascotGaze.toward(dx: 2, dy: 0)
        XCTAssertEqual(right.x, 1, accuracy: 1e-9)
        XCTAssertEqual(right.y, 0, accuracy: 1e-9)
        let near = MascotGaze.toward(dx: 0, dy: -0.15)
        XCTAssertEqual(near.y, -0.25, accuracy: 1e-9)  // a quarter of the way at a quarter of reach
        let far = MascotGaze.toward(dx: -30, dy: 40)
        XCTAssertEqual(far.length, 1, accuracy: 1e-9)
        XCTAssertEqual(far.x, -0.6, accuracy: 1e-9)
        XCTAssertEqual(far.y, 0.8, accuracy: 1e-9)
        XCTAssertEqual(MascotGaze.toward(dx: 0, dy: 0), .ahead)
        XCTAssertEqual(MascotGaze(x: 3, y: 4).x, 0.6, accuracy: 1e-9)
        XCTAssertEqual(MascotGaze(x: 3, y: 4).y, 0.8, accuracy: 1e-9)
        let inside = MascotGaze(x: 0.3, y: -0.4)
        XCTAssertEqual(inside.x, 0.3)
        XCTAssertEqual(inside.y, -0.4)

        // The eyes move at most eyeTravel, in the direction they look.
        let frame = MascotMotion.frame(at: start, state: .idle, seed: seed, look: far)
        XCTAssertEqual(frame.eyes.offsetX, -0.6 * MascotMotion.eyeTravel.x, accuracy: 1e-12)
        XCTAssertEqual(frame.eyes.offsetY, 0.8 * MascotMotion.eyeTravel.y, accuracy: 1e-12)
        for look in [MascotGaze(x: 1, y: 0), MascotGaze(x: -1, y: 0), MascotGaze(x: 0, y: 1), far] {
            let eyes = MascotMotion.frame(at: start, state: .idle, seed: seed, look: look).eyes
            XCTAssertLessThanOrEqual(abs(eyes.offsetX), MascotMotion.eyeTravel.x + 1e-12)
            XCTAssertLessThanOrEqual(abs(eyes.offsetY), MascotMotion.eyeTravel.y + 1e-12)
        }
    }

    func testHeadLeansTowardsTheGaze() {
        // Working tilts from side to side; the lean adds on top of it.
        for state in [MascotState.idle, .working, .done, .error] {
            let plain = MascotMotion.frame(at: start + 0.3, state: state, since: start, seed: seed).pose
            let right = MascotMotion.frame(
                at: start + 0.3, state: state, since: start, seed: seed, look: MascotGaze(x: 1, y: 0)
            ).pose
            let left = MascotMotion.frame(
                at: start + 0.3, state: state, since: start, seed: seed, look: MascotGaze(x: -1, y: 0)
            ).pose
            XCTAssertEqual(right.rotation - plain.rotation, MascotMotion.lean, accuracy: 1e-9, "\(state)")
            XCTAssertEqual(left.rotation - plain.rotation, -MascotMotion.lean, accuracy: 1e-9, "\(state)")
            XCTAssertGreaterThan(right.offsetX, plain.offsetX)
        }
        // Asleep it does not follow anything.
        let asleep = MascotMotion.frame(at: start, state: .offline, seed: seed, look: MascotGaze(x: 1, y: 0))
        XCTAssertEqual(asleep.pose, MascotPose.at(start, state: .offline))
        XCTAssertEqual(asleep.eyes, MascotEyes(closure: 1))
    }

    func testFollowingTheGazeIsQuickButSmooth() {
        let target = MascotGaze(x: 1, y: 0)
        var gaze = MascotGaze.ahead
        var previous = 0.0
        for _ in 0..<30 {
            gaze = MascotMotion.follow(gaze, toward: target, after: 1.0 / 60)
            XCTAssertGreaterThan(gaze.x, previous)
            XCTAssertLessThanOrEqual(gaze.x, 1)
            previous = gaze.x
        }
        XCTAssertGreaterThan(gaze.x, 0.99)  // half a second later
        XCTAssertLessThan(MascotMotion.follow(.ahead, toward: target, after: 1.0 / 60).x, 0.25)
        XCTAssertEqual(MascotMotion.follow(.ahead, toward: target, after: 0), .ahead)
        XCTAssertEqual(MascotMotion.follow(.ahead, toward: target, after: 5), target)
    }

    // MARK: Reactions

    func testReactionsStartAndEnd() {
        let at = Date(timeIntervalSince1970: start)
        for kind in [MascotCue.Kind.poke, .greet, .talk] {
            let cue = MascotCue(kind, at: at)
            func frame(_ time: Double, _ cue: MascotCue?) -> MascotFrame {
                MascotMotion.frame(at: time, state: .idle, seed: seed, cue: cue)
            }
            XCTAssertNil(cue.progress(at: start - 0.01))
            XCTAssertEqual(cue.progress(at: start), 0)
            XCTAssertNil(cue.progress(at: start + kind.duration + 0.001))
            XCTAssertEqual(frame(start - 0.01, cue), frame(start - 0.01, nil), "\(kind)")
            XCTAssertEqual(frame(start + kind.duration + 0.01, cue), frame(start + kind.duration + 0.01, nil))
            let during = (1..<40).map { start + kind.duration * Double($0) / 40 }
            XCTAssertTrue(during.contains { frame($0, cue).pose != frame($0, nil).pose }, "\(kind)")
        }

        // Poke: squash, hop up stretched, squash on landing, eyes squeezed shut.
        let poke = MascotCue(.poke, at: at)
        let times = (0..<60).map { start + Double($0) * 0.01 }
        let poked = times.map { MascotMotion.frame(at: $0, state: .idle, cue: poke) }
        let hop = zip(poked, times).map { $0.pose.offsetY - MascotPose.at($1, state: .idle).offsetY }
        let stretch = zip(poked, times).map { $0.pose.scaleY / MascotPose.at($1, state: .idle).scaleY }
        XCTAssertLessThan(hop.min()!, -0.07)
        XCTAssertLessThan(stretch.prefix(15).min()!, 0.92)
        XCTAssertGreaterThan(stretch.max()!, 1.05)
        XCTAssertLessThan(stretch.suffix(15).min()!, 0.94)
        XCTAssertEqual(poked[15].eyes.closure, 1)
        XCTAssertLessThan(poked[55].eyes.closure, 1)

        // Greet: a small hop towards where it looks, a happy squint.
        let greet = MascotCue(.greet, at: at)
        let left = MascotGaze(x: -1, y: 0)
        let greeted = MascotMotion.frame(at: start + 0.25, state: .idle, look: left, cue: greet)
        let still = MascotMotion.frame(at: start + 0.25, state: .idle, look: left)
        XCTAssertLessThan(greeted.pose.offsetY, still.pose.offsetY - 0.03)
        XCTAssertLessThan(greeted.pose.rotation, still.pose.rotation - 4)
        XCTAssertEqual(greeted.eyes.closure, 1)

        // Talk: three nods down and up.
        let talk = MascotCue(.talk, at: at)
        let nods = (0...120).map {
            MascotMotion.frame(at: start + Double($0) * 0.01, state: .idle, cue: talk).pose.offsetY
                - MascotPose.at(start + Double($0) * 0.01, state: .idle).offsetY
        }
        let bottoms = (1..<nods.count - 1).filter {
            nods[$0] > nods[$0 - 1] && nods[$0] >= nods[$0 + 1] && nods[$0] > 0.005
        }
        XCTAssertEqual(bottoms.count, 3)
    }

    func testReactionsComposeWithStates() {
        let at = Date(timeIntervalSince1970: start)
        let poke = MascotCue(.poke, at: at)
        // Done: the bounce and the hop add up.
        let done = MascotMotion.frame(at: start + 0.27, state: .done, since: start, cue: poke)
        let bounce = MascotPose.at(start + 0.27, state: .done, since: start)
        XCTAssertLessThan(done.pose.offsetY, bounce.offsetY - 0.05)

        // Error: the droop, tilt and sweat stay; the hop is half as high.
        for step in 0..<60 {
            let time = start + Double(step) * 0.01
            let error = MascotMotion.frame(at: time, state: .error, cue: poke).pose
            XCTAssertEqual(error.effects, [.sweat])
            XCTAssertLessThan(error.rotation, -2)
            XCTAssertEqual(error.saturation, 1)
        }
        let errorHop = (0..<60).map {
            MascotMotion.frame(at: start + Double($0) * 0.01, state: .error, cue: poke)
        }
        XCTAssertLessThan(errorHop.map(\.pose.offsetY).min()!, 0.04 - 0.03)
        XCTAssertGreaterThan(errorHop.map(\.pose.offsetY).min()!, 0.04 - 0.05)

        // Offline: still gray and asleep with its zzz; a poke opens the eyes for a moment,
        // hovering does not wake it.
        let asleep = MascotMotion.frame(at: start + 0.3, state: .offline, cue: poke)
        XCTAssertEqual(asleep.pose.effects, [.zzz])
        XCTAssertEqual(asleep.pose.saturation, 0.4)
        XCTAssertEqual(asleep.eyes.closure, 0)
        XCTAssertEqual(MascotMotion.frame(at: start + 0.7, state: .offline, cue: poke).eyes.closure, 1)
        let greet = MascotCue(.greet, at: at)
        XCTAssertEqual(
            MascotMotion.frame(at: start + 0.25, state: .offline, cue: greet),
            MascotMotion.frame(at: start + 0.25, state: .offline))
    }

    func testPokeBurst() {
        let poke = MascotCue(.poke, at: Date(timeIntervalSince1970: start))
        let origin = (x: 0.5, y: 0.4)
        XCTAssertTrue(MascotMotion.burst(of: poke, at: start - 0.01, from: origin).isEmpty)
        let end = start + MascotMotion.burstDuration
        XCTAssertTrue(MascotMotion.burst(of: poke, at: end, from: origin).isEmpty)
        let greet = MascotCue(.greet, at: poke.at)
        XCTAssertTrue(MascotMotion.burst(of: greet, at: start + 0.2, from: origin).isEmpty)
        XCTAssertTrue(MascotMotion.burst(of: nil, at: start + 0.2, from: origin).isEmpty)

        let early = MascotMotion.burst(of: poke, at: start + 0.1, from: origin)
        let late = MascotMotion.burst(of: poke, at: start + 0.6, from: origin)
        XCTAssertEqual(early.count, 6)
        XCTAssertEqual(Set(early.map { $0.kind == .heart }), [true, false])
        XCTAssertEqual(early, MascotMotion.burst(of: poke, at: start + 0.1, from: origin))
        for (a, b) in zip(early, late) {
            // They fly up and out, and fade.
            XCTAssertGreaterThan(hypot(b.x - origin.x, b.y - origin.y), hypot(a.x - origin.x, a.y - origin.y))
            XCTAssertLessThan(b.y, origin.y)
            XCTAssertTrue((0...1).contains(a.opacity) && (0...1).contains(b.opacity))
            XCTAssertLessThan(b.opacity, 1)
            XCTAssertGreaterThan(a.size, 0)
        }
    }

    // MARK: Reduce motion and small avatars

    func testReduceMotionGivesTheStillPose() {
        let poke = MascotCue(.poke, at: Date(timeIntervalSince1970: start))
        for state in MascotState.allCases {
            for step in 0..<50 {
                let time = start + Double(step) * 0.13
                for liveliness in [MascotLiveliness.full, .subtle] {
                    let frame = MascotMotion.frame(
                        at: time, state: state, since: start, seed: seed, look: MascotGaze(x: 1, y: 1),
                        cue: poke, liveliness: liveliness, reduceMotion: true)
                    XCTAssertEqual(frame.pose, MascotPose.at(0, state: state, reduceMotion: true))
                    XCTAssertEqual(frame.eyes, .open)
                }
            }
        }
    }

    func testSubtleOnlyMovesTheEyes() {
        let poke = MascotCue(.poke, at: Date(timeIntervalSince1970: start))
        var blinked = false
        for step in 0..<1000 {
            let time = start + Double(step) * 0.01
            let frame = MascotMotion.frame(
                at: time, state: .working, since: start, seed: seed, look: MascotGaze(x: -1, y: 0), cue: poke,
                liveliness: .subtle)
            XCTAssertEqual(frame.pose, MascotPose.at(0, state: .working, reduceMotion: true))
            XCTAssertEqual(frame.eyes.offsetX, -MascotMotion.eyeTravel.x, accuracy: 1e-12)
            if frame.eyes.closure == 1 { blinked = true }
        }
        XCTAssertTrue(blinked)
    }
}
