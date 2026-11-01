import Foundation

/// Character-specific acts. Raw values also name the exported preview frames.
public enum MascotAnimation: String, CaseIterable, Sendable {
    case sleep, heartbeat, stretch, training
    case tokens, quota, stars, savings
    case grading, ribbon, report, medal
    case writing, books, alarm, halo
    case calendar, approval, meteor, earrings
    case umbrella, checklist, wave, moon

    public var sticker: String {
        switch self {
        case .sleep, .heartbeat, .stretch, .training: return "lily_veil"
        case .tokens, .quota, .stars, .savings: return "shouanren"
        case .grading, .ribbon, .report, .medal: return "red_ribbon"
        case .writing, .books, .alarm, .halo: return "moning"
        case .calendar, .approval, .meteor, .earrings: return "luosela"
        case .umbrella, .checklist, .wave, .moon: return "aemeath"
        }
    }

    public var title: String {
        switch self {
        case .sleep: return "蕾丝被子"
        case .heartbeat: return "怦怦心跳"
        case .stretch: return "早安伸懒腰"
        case .training: return "训练完成"
        case .tokens: return "算 Token"
        case .quota: return "额度仪表"
        case .stars: return "星空闪烁"
        case .savings: return "攒下星币"
        case .grading: return "红笔批卷"
        case .ribbon: return "蝴蝶结弹跳"
        case .report: return "展示成绩单"
        case .medal: return "学分勋章"
        case .writing: return "写作业"
        case .books: return "叠好课本"
        case .alarm: return "上课铃响"
        case .halo: return "晶石光环"
        case .calendar: return "翻日历"
        case .approval: return "请假已批准"
        case .meteor: return "帽上流星"
        case .earrings: return "月亮轻摇"
        case .umbrella: return "撑伞提醒"
        case .checklist: return "勾好待办"
        case .wave: return "挥手你好"
        case .moon: return "月牙发光"
        }
    }

    public var accent: MascotColor {
        let hex: String
        switch sticker {
        case "lily_veil": hex = "#8068B2"
        case "shouanren": hex = "#3E7FD6"
        case "red_ribbon": hex = "#E8383F"
        case "moning": hex = "#D65F63"
        case "luosela": hex = "#5B43C9"
        default: hex = "#E86A9A"
        }
        return MascotColor(hex: hex)!
    }

    public var duration: TimeInterval {
        switch self {
        case .sleep: return 6
        case .tokens, .writing, .grading, .calendar, .checklist: return 4
        default: return 3
        }
    }

    public static func all(for sticker: String) -> [Self] {
        allCases.filter { $0.sticker == sticker }
    }

    public static func working(for sticker: String) -> Self? {
        switch sticker {
        case "lily_veil": return .heartbeat
        case "shouanren": return .tokens
        case "red_ribbon": return .grading
        case "moning": return .writing
        case "luosela": return .calendar
        case "aemeath": return .checklist
        default: return nil
        }
    }

    public static func celebration(for sticker: String) -> Self? {
        switch sticker {
        case "lily_veil": return .training
        case "shouanren": return .savings
        case "red_ribbon": return .medal
        case "moning": return .books
        case "luosela": return .approval
        case "aemeath": return .wave
        default: return nil
        }
    }

    public static func idle(for sticker: String) -> [Self] {
        // Sleep is reserved for the night, offline and explicit interaction.
        all(for: sticker).filter { $0 != working(for: sticker) && $0 != .sleep }
    }
}

public struct MascotAnimationRequest: Equatable, Sendable {
    public let id: UUID
    /// nil means a poke: choose another of this character's acts.
    public let animation: MascotAnimation?
    public let at: Date

    public init(_ animation: MascotAnimation? = nil, at: Date = Date()) {
        id = UUID()
        self.animation = animation
        self.at = at
    }
}

public struct MascotAnimationPlayback: Equatable, Sendable {
    public let animation: MascotAnimation
    public let elapsed: TimeInterval
    public let looping: Bool

    public init(_ animation: MascotAnimation, elapsed: TimeInterval, looping: Bool = false) {
        self.animation = animation
        self.elapsed = max(0, elapsed)
        self.looping = looping
    }

    public var phase: Double {
        (elapsed / animation.duration).truncatingRemainder(dividingBy: 1)
    }

    /// Props ease in and out; looping acts stay visible across cycle boundaries.
    public var visibility: Double {
        let entrance = min(1, elapsed / 0.25)
        return looping ? entrance : min(entrance, max(0, (animation.duration - elapsed) / 0.3))
    }

    public func applying(to frame: MascotFrame) -> MascotFrame {
        var result = frame
        let wave = sin(phase * .pi * 2)
        switch animation {
        case .sleep:
            result.pose = MascotPose()
            result.pose.rotation = 3
            result.pose.scaleY = 1 + 0.012 * wave
            result.eyes = MascotEyes(closure: min(1, elapsed / 0.5))
        case .stretch:
            result.eyes.closure = sin(phase * .pi)
            result.pose.scaleY *= 1 + 0.055 * sin(phase * .pi)
            result.pose.rotation += 4 * wave
        case .writing, .grading, .tokens, .calendar, .checklist, .approval:
            result.eyes.offsetX = 0
            result.eyes.offsetY = MascotMotion.eyeTravel.y
            result.pose.rotation *= 0.25
        case .wave:
            result.pose.rotation += 5 * wave
            result.eyes.closure = max(result.eyes.closure, max(0, sin(phase * .pi * 4)))
        case .training, .savings, .medal, .books, .report:
            result.pose.offsetY -= 0.025 * abs(wave)
        default: break
        }
        return result
    }
}

/// Owns only timing and selection, never UI or timers. Feed a local hour and a seeded clock
/// in tests. State changes interrupt an act; taps/preview buttons then take priority once.
public struct MascotAnimationPlayer: Sendable {
    private var random: UInt64
    private var sticker: String?
    private var state: MascotState?
    private var night = false
    private var began = 0.0
    private var requested: (animation: MascotAnimation, at: Double)?
    private var lastRequest: UUID?
    private var previousChoice: MascotAnimation?
    public private(set) var nextIdleAt: TimeInterval?

    public init(seed: UInt64 = UInt64.random(in: .min ... .max)) { random = seed }

    private mutating func number() -> Double {
        random = random &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        return Double(random >> 11) / Double(UInt64.max >> 11)
    }

    private mutating func choose(_ choices: [MascotAnimation]) -> MascotAnimation? {
        let pool = choices.filter { $0 != previousChoice }
        let available = pool.isEmpty ? choices : pool
        guard !available.isEmpty else { return nil }
        let choice = available[min(available.count - 1, Int(number() * Double(available.count)))]
        previousChoice = choice
        return choice
    }

    public mutating func sample(
        sticker name: String, state current: MascotState, at now: TimeInterval, localHour: Int,
        request: MascotAnimationRequest? = nil, enabled: Bool = true,
        reduceMotion: Bool = false, active: Bool = true
    ) -> MascotAnimationPlayback? {
        guard enabled, !reduceMotion, active, !MascotAnimation.all(for: name).isEmpty else {
            state = nil
            requested = nil
            nextIdleAt = nil
            lastRequest = request?.id
            return nil
        }
        let sleeping = name == "lily_veil" && (localHour >= 23 || localHour < 6)
        if sticker != name || state != current || night != sleeping {
            sticker = name
            state = current
            night = sleeping
            began = now
            requested = nil
            nextIdleAt = now + 20 + 20 * number()
        }
        if let request, request.id != lastRequest {
            lastRequest = request.id
            let age = now - request.at.timeIntervalSince1970
            if age >= 0, age < (request.animation?.duration ?? 6),
                let act = request.animation ?? choose(MascotAnimation.all(for: name)), act.sticker == name
            {
                requested = (act, request.at.timeIntervalSince1970)
                previousChoice = act
                nextIdleAt = now + 20 + 20 * number()
            }
        }
        if let playing = requested {
            let elapsed = now - playing.at
            if elapsed < playing.animation.duration {
                return MascotAnimationPlayback(playing.animation, elapsed: elapsed)
            }
            requested = nil
        }
        let elapsed = max(0, now - began)
        if current == .offline || (current == .idle && sleeping) {
            return name == "lily_veil" ? MascotAnimationPlayback(.sleep, elapsed: elapsed, looping: true) : nil
        }
        if current == .working, let act = MascotAnimation.working(for: name) {
            return MascotAnimationPlayback(act, elapsed: elapsed, looping: true)
        }
        if current == .done, let act = MascotAnimation.celebration(for: name), elapsed < act.duration {
            return MascotAnimationPlayback(act, elapsed: elapsed)
        }
        if current == .idle, now >= (nextIdleAt ?? now) {
            if let act = choose(MascotAnimation.idle(for: name)) {
                requested = (act, now)
                nextIdleAt = now + 20 + 20 * number()
                return MascotAnimationPlayback(act, elapsed: 0)
            }
        }
        return nil
    }
}
