import Foundation

// Life on top of the state animations (docs/mascot.md): blinking, glancing around, looking at
// the pointer, and short reactions to a click, to the pointer coming over and to the Bot saying
// something. Like MascotPose everything is a plain function of time so it can be unit tested;
// only the smoothing of the gaze carries a value from one frame to the next (`follow`).

/// A reaction for the mascot to play once on top of its state. The caller sets a new cue each
/// time something happens; the mascot plays it for `kind.duration` from `at`.
public struct MascotCue: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        /// Clicked: a squash-and-stretch hop.
        case poke
        /// The pointer came over it: a small happy hop towards it.
        case greet
        /// The Bot just said something: lively nodding.
        case talk

        public var duration: TimeInterval {
            switch self {
            case .poke: return 0.6
            case .greet: return 0.5
            case .talk: return 1.2
            }
        }
    }

    public var kind: Kind
    public var at: Date

    public init(_ kind: Kind, at: Date = Date()) {
        self.kind = kind
        self.at = at
    }

    /// 0..<1 while the reaction plays at `time` (seconds since 1970), nil before and after.
    public func progress(at time: TimeInterval) -> Double? {
        let elapsed = time - at.timeIntervalSince1970
        guard elapsed >= 0, elapsed < kind.duration else { return nil }
        return elapsed / kind.duration
    }

    /// The newer of two cues, so a click on the mascot and a cue from the caller do not fight.
    public static func latest(_ a: MascotCue?, _ b: MascotCue?) -> MascotCue? {
        guard let a, let b else { return a ?? b }
        return b.at > a.at ? b : a
    }
}

/// How lively a mascot is.
public enum MascotLiveliness: Equatable, Sendable {
    /// Big mascots: the state animation, blinking, glancing, leaning and reactions.
    case full
    /// Small avatars (lists, chat bubbles), many on screen at once: the still pose of the
    /// state, only the eyes move (blinking, glancing, looking at the pointer), redrawn less often.
    case subtle
}

/// Where the eyes look: x to the right, y down. The length is at most 1, as far as they go.
public struct MascotGaze: Equatable, Sendable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) {
        let length = (x * x + y * y).squareRoot()
        let scale = length > 1 ? 1 / length : 1
        self.x = x * scale
        self.y = y * scale
    }

    public static let ahead = MascotGaze(x: 0, y: 0)

    public var length: Double { (x * x + y * y).squareRoot() }

    /// Looking at something `dx`, `dy` away from the eyes, in sticker sides (positive = right,
    /// down): within `reach` the eyes turn part of the way, from there on all the way.
    public static func toward(dx: Double, dy: Double, reach: Double = 0.6) -> MascotGaze {
        let distance = (dx * dx + dy * dy).squareRoot()
        guard distance > 0, reach > 0 else { return .ahead }
        let turn = min(distance / reach, 1)
        return MascotGaze(x: dx / distance * turn, y: dy / distance * turn)
    }
}

/// The eye layers in one frame.
public struct MascotEyes: Equatable, Sendable {
    /// How far the eyes are moved, fractions of the sticker side (positive = right, down).
    public var offsetX: Double
    public var offsetY: Double
    /// 0 open, 1 closed.
    public var closure: Double

    public init(offsetX: Double = 0, offsetY: Double = 0, closure: Double = 0) {
        self.offsetX = offsetX
        self.offsetY = offsetY
        self.closure = closure
    }

    public static let open = MascotEyes()
}

/// One frame: the whole sticker's pose and its eyes.
public struct MascotFrame: Equatable, Sendable {
    public var pose: MascotPose
    public var eyes: MascotEyes

    public init(pose: MascotPose, eyes: MascotEyes = .open) {
        self.pose = pose
        self.eyes = eyes
    }
}

/// A heart or sparkle popping out of a poke.
public struct MascotParticle: Equatable, Sendable {
    public enum Kind: Equatable, Sendable { case heart, sparkle }

    public var kind: Kind
    /// Center, fractions of the sticker side from its top left.
    public var x: Double
    public var y: Double
    /// Width and height, a fraction of the sticker side.
    public var size: Double
    /// Degrees.
    public var rotation: Double
    public var opacity: Double
}

/// A reaction's change to the pose and the eyes.
struct MascotNudge: Equatable {
    var offsetX = 0.0
    var offsetY = 0.0
    var scaleX = 1.0
    var scaleY = 1.0
    var rotation = 0.0
    var closure = 0.0

    /// Added on top of the state's pose, `amount` of it.
    func apply(to pose: inout MascotPose, amount: Double) {
        pose.offsetX += offsetX * amount
        pose.offsetY += offsetY * amount
        pose.scaleX *= 1 + (scaleX - 1) * amount
        pose.scaleY *= 1 + (scaleY - 1) * amount
        pose.rotation += rotation * amount
    }
}

public enum MascotMotion {
    /// How far the eyes move at most, fractions of the sticker side: enough to read as looking
    /// somewhere, little enough to stay on the face.
    public static let eyeTravel = (x: 0.016, y: 0.01)
    /// How far the head leans towards where the eyes look, degrees.
    public static let lean = 3.0
    /// How long the hearts and sparkles of a poke fly.
    public static let burstDuration: TimeInterval = 0.9

    // MARK: Randomness

    /// A number per sticker so mascots side by side blink and glance at different times. FNV-1a
    /// over the name, so it stays the same across launches (unlike `hashValue`).
    public static func seed(for sticker: String) -> UInt64 {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325
        for byte in sticker.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 0x0000_0100_0000_01b3
        }
        return hash
    }

    /// A random-looking number in 0..<1 that depends only on its inputs (SplitMix64).
    static func random(_ seed: UInt64, _ slot: Int, _ salt: UInt64) -> Double {
        var z = seed &+ UInt64(bitPattern: Int64(slot)) &* 0x9e37_79b9_7f4a_7c15
        z = z &+ salt &* 0xd1b5_4a32_d192_ed03
        z = (z ^ (z >> 30)) &* 0xbf58_476d_1ce4_e5b9
        z = (z ^ (z >> 27)) &* 0x94d0_49bb_1331_11eb
        z ^= z >> 31
        return Double(z >> 11) / Double(UInt64(1) << 53)
    }

    static func smooth(_ x: Double) -> Double {
        let t = min(max(x, 0), 1)
        return t * t * (3 - 2 * t)
    }

    /// 1 between `from` and `to` (progress), easing in and out over `fade`.
    static func window(_ p: Double, from: Double, to: Double, fade: Double) -> Double {
        smooth((p - from) / fade) * (1 - smooth((p - to + fade) / fade))
    }

    // MARK: Blinking

    /// A blink every `blinkPeriod` ± `blinkJitter` seconds, so 2.5–6 s apart.
    static let blinkPeriod = 4.25
    static let blinkJitter = 1.75
    static let blinkClosing = 0.04
    static let blinkOpening = 0.06

    /// How far the eyes are shut at `time` (seconds since 1970): 0 open, 1 closed. A blink
    /// keeps them mostly shut (0.5 or more) for 0.12–0.15 s; about one in five is a double blink.
    public static func blink(at time: TimeInterval, seed: UInt64) -> Double {
        let local = time - random(seed, 0, 1) * blinkPeriod
        let slot = Int((local / blinkPeriod).rounded(.down))
        var closure = 0.0
        for k in (slot - 1)...slot {
            let start = Double(k) * blinkPeriod + random(seed, k, 2) * blinkJitter
            let hold = 0.07 + 0.03 * random(seed, k, 3)
            closure = max(closure, lid(local - start, hold: hold))
            if random(seed, k, 4) < 0.2 {
                let again = start + blinkClosing + hold + blinkOpening + 0.08
                closure = max(closure, lid(local - again, hold: hold * 0.8))
            }
        }
        return closure
    }

    /// One blink, `elapsed` seconds after it began.
    static func lid(_ elapsed: TimeInterval, hold: TimeInterval) -> Double {
        if elapsed < 0 { return 0 }
        if elapsed < blinkClosing { return smooth(elapsed / blinkClosing) }
        if elapsed < blinkClosing + hold { return 1 }
        let opening = elapsed - blinkClosing - hold
        return opening < blinkOpening ? 1 - smooth(opening / blinkOpening) : 0
    }

    // MARK: Looking

    static let glancePeriod = 6.5

    /// Where idle eyes look at `time`: mostly ahead; every several seconds they drift to a
    /// random spot (more to the sides than up or down), rest there a moment and come back.
    public static func glance(at time: TimeInterval, seed: UInt64) -> MascotGaze {
        let local = time - random(seed, 0, 5) * glancePeriod
        let slot = Int((local / glancePeriod).rounded(.down))
        guard random(seed, slot, 6) < 0.75 else { return .ahead }
        let elapsed = local - (Double(slot) * glancePeriod + 0.5 + 2.5 * random(seed, slot, 7))
        let move = 0.25
        let hold = 0.9 + 1.2 * random(seed, slot, 8)
        let back = 0.35
        guard elapsed >= 0, elapsed < move + hold + back else { return .ahead }
        let weight =
            elapsed < move + hold ? smooth(elapsed / move) : 1 - smooth((elapsed - move - hold) / back)
        let angle = 2 * Double.pi * random(seed, slot, 9)
        let reach = (0.55 + 0.45 * random(seed, slot, 10)) * weight
        return MascotGaze(x: cos(angle) * reach, y: sin(angle) * reach * 0.6)
    }

    /// Where the eyes want to look at `time`: at the pointer when there is one, otherwise ahead
    /// while talking, down at the work while working, down and away after an error, and
    /// glancing around when idle. Asleep (offline) they stay ahead behind closed lids.
    public static func target(
        at time: TimeInterval, state: MascotState, seed: UInt64, pointer: MascotGaze? = nil,
        cue: MascotCue? = nil
    ) -> MascotGaze {
        if state == .offline { return .ahead }
        if let pointer { return pointer }
        if let cue, cue.kind == .talk, cue.progress(at: time) != nil { return .ahead }
        switch state {
        case .working:
            return MascotGaze(x: glance(at: time, seed: seed).x * 0.5, y: 0.55)
        case .error:
            return MascotGaze(x: -0.35, y: 0.75)
        case .idle, .done, .offline:
            return glance(at: time, seed: seed)
        }
    }

    /// The gaze `elapsed` seconds later, moving from `current` towards `target`: quick, but not
    /// a jump; after a long pause (the view was off screen) it is simply there.
    public static func follow(
        _ current: MascotGaze, toward target: MascotGaze, after elapsed: TimeInterval
    ) -> MascotGaze {
        guard elapsed > 0 else { return current }
        guard elapsed < 1 else { return target }
        let step = 1 - exp(-elapsed / 0.08)
        return MascotGaze(
            x: current.x + (target.x - current.x) * step, y: current.y + (target.y - current.y) * step)
    }

    // MARK: Frames

    /// The frame at `time` (seconds since 1970) for a state that began at `start`: the state's
    /// pose, the head leaning towards `look`, the reaction to `cue` on top, and the eyes (moved
    /// towards `look`, blinking). `.subtle` keeps the state's still pose and only moves the eyes.
    /// With reduce motion it is the state's still pose with open eyes looking ahead.
    public static func frame(
        at time: TimeInterval, state: MascotState, since start: TimeInterval = 0, seed: UInt64 = 0,
        look: MascotGaze = .ahead, cue: MascotCue? = nil, liveliness: MascotLiveliness = .full,
        reduceMotion: Bool = false
    ) -> MascotFrame {
        if reduceMotion {
            return MascotFrame(pose: MascotPose.at(time, state: state, since: start, reduceMotion: true))
        }
        var pose = MascotPose.at(time, state: state, since: start, reduceMotion: liveliness == .subtle)
        let asleep = state == .offline
        var eyes = MascotEyes(
            offsetX: asleep ? 0 : look.x * eyeTravel.x, offsetY: asleep ? 0 : look.y * eyeTravel.y,
            closure: asleep ? 1 : blink(at: time, seed: seed))
        if liveliness == .full && !asleep {
            pose.rotation += look.x * lean
            pose.offsetX += look.x * 0.006
            pose.offsetY += look.y * 0.004
        }
        if let cue, let progress = cue.progress(at: time) {
            let amount = strength(of: cue.kind, in: state)
            if amount > 0 {
                let nudge = reaction(cue.kind, progress: progress, toward: look.x < 0 ? -1 : 1)
                if liveliness == .full { nudge.apply(to: &pose, amount: amount) }
                // Poked while asleep: the eyes pop open for a moment.
                eyes.closure =
                    asleep
                    ? 1 - window(progress, from: 0, to: 1, fade: 0.15)
                    : max(eyes.closure, nudge.closure)
            }
        }
        return MascotFrame(pose: pose, eyes: eyes)
    }

    /// How much of a reaction shows: half while something is wrong, so the error or sleep
    /// stays readable; asleep it only reacts to being poked.
    static func strength(of kind: MascotCue.Kind, in state: MascotState) -> Double {
        switch state {
        case .offline: return kind == .poke ? 0.5 : 0
        case .error: return 0.5
        case .idle, .working, .done: return 1
        }
    }

    /// A reaction at `progress` (0..<1); `side` is -1 or 1, where the eyes look.
    static func reaction(_ kind: MascotCue.Kind, progress p: Double, toward side: Double) -> MascotNudge {
        var nudge = MascotNudge()
        switch kind {
        case .poke:
            if p < 0.15 {
                // Wind up: squash down.
                let squash = sin(Double.pi * p / 0.15)
                nudge.scaleY = 1 - 0.1 * squash
                nudge.scaleX = 1 + 0.06 * squash
            } else if p < 0.75 {
                // Hop, stretched on the way up.
                let q = (p - 0.15) / 0.6
                nudge.offsetY = -0.08 * sin(Double.pi * q)
                let stretch = q < 0.5 ? sin(2 * Double.pi * q) : 0
                nudge.scaleY = 1 + 0.06 * stretch
                nudge.scaleX = 1 - 0.04 * stretch
            } else {
                // Land: squash and settle.
                let squash = sin(Double.pi * (p - 0.75) / 0.25)
                nudge.scaleY = 1 - 0.07 * squash
                nudge.scaleX = 1 + 0.045 * squash
            }
            nudge.rotation = 5 * sin(4 * Double.pi * p) * (1 - p) * side
            nudge.closure = window(p, from: 0.02, to: 0.45, fade: 0.04)
        case .greet:
            let bump = sin(Double.pi * p)
            nudge.offsetY = -0.035 * bump
            nudge.offsetX = 0.01 * bump * side
            nudge.rotation = 5 * bump * side
            nudge.scaleY = 1 + 0.02 * bump
            nudge.scaleX = 1 - 0.01 * bump
            nudge.closure = window(p, from: 0.2, to: 0.75, fade: 0.06)
        case .talk:
            // Three nods, swaying a little from side to side.
            let envelope = smooth(p / 0.12) * (1 - smooth((p - 0.8) / 0.2))
            let nod = (1 - cos(6 * Double.pi * p)) / 2
            nudge.offsetY = 0.018 * nod * envelope
            nudge.scaleY = 1 - 0.02 * nod * envelope
            nudge.scaleX = 1 + 0.012 * nod * envelope
            nudge.rotation = 2.5 * sin(3 * Double.pi * p) * envelope
        }
        return nudge
    }

    /// Hearts and sparkles flying up and out of `origin` (fractions of the sticker side) for
    /// `burstDuration` after a poke; nothing for other cues.
    public static func burst(
        of cue: MascotCue?, at time: TimeInterval, from origin: (x: Double, y: Double), seed: UInt64 = 0
    ) -> [MascotParticle] {
        guard let cue, cue.kind == .poke else { return [] }
        let age = time - cue.at.timeIntervalSince1970
        guard age >= 0, age < burstDuration else { return [] }
        let q = age / burstDuration
        let slot = Int((cue.at.timeIntervalSince1970 * 1000).rounded())
        return (0..<6).map { index in
            func vary(_ salt: UInt64) -> Double { random(seed, slot, salt + UInt64(index) * 8) }
            let angle = -Double.pi / 2 + (Double(index) - 2.5) / 2.5 * 0.95 + (vary(20) - 0.5) * 0.3
            let distance = (0.2 + 0.12 * vary(21)) * (1 - (1 - q) * (1 - q))
            let pop = min(1, q / 0.12) * (1 - 0.3 * q)
            return MascotParticle(
                kind: index % 2 == 0 ? .heart : .sparkle,
                x: origin.x + cos(angle) * distance, y: origin.y + sin(angle) * distance + 0.04 * q * q,
                size: (0.06 + 0.035 * vary(22)) * pop,
                rotation: (vary(23) - 0.5) * 40 + (vary(24) - 0.5) * 120 * q,
                opacity: q < 0.6 ? 1 : 1 - (q - 0.6) / 0.4)
        }
    }
}
