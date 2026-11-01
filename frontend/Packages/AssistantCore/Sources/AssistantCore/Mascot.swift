import Foundation

// Plugin stickers (docs/mascot.md): every plugin has its own sticker, an image in
// frontend/Design/stickers/ named in its manifest; plugins without one show the placeholder.
// State animations move the whole image; stickers with eye layers also blink and look around
// (MascotMotion.swift). Everything here is plain data and math so it can be unit tested;
// MascotView draws it.

/// sRGB color written as "#RRGGBB".
public struct MascotColor: Hashable, Sendable {
    public var red: Double
    public var green: Double
    public var blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = red
        self.green = green
        self.blue = blue
    }

    public init?(hex: String) {
        var text = hex.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, text.allSatisfy(\.isHexDigit), let value = UInt32(text, radix: 16)
        else { return nil }
        red = Double((value >> 16) & 0xFF) / 255
        green = Double((value >> 8) & 0xFF) / 255
        blue = Double(value & 0xFF) / 255
    }

    public var hex: String {
        let parts = [red, green, blue].map { Int(($0 * 255).rounded()) }
        return String(format: "#%02X%02X%02X", parts[0], parts[1], parts[2])
    }

    /// WCAG relative luminance, 0 for black to 1 for white.
    public var luminance: Double {
        func linear(_ c: Double) -> Double { c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    }

    /// `amount` of the way to `other` (0 keeps this color, 1 gives `other`).
    public func mixed(with other: MascotColor, _ amount: Double) -> MascotColor {
        MascotColor(
            red: red + (other.red - red) * amount, green: green + (other.green - green) * amount,
            blue: blue + (other.blue - blue) * amount)
    }

    /// The same hue mixed with white until small text in it reads on the watch's black screen
    /// (contrast 7:1, luminance 0.3); a light enough color stays as it is.
    public var onBlack: MascotColor {
        let white = MascotColor(red: 1, green: 1, blue: 1)
        var amount = 0.0
        while mixed(with: white, amount).luminance < 0.3 && amount < 1 { amount += 0.05 }
        return mixed(with: white, min(amount, 1))
    }
}

public enum MascotPalette {
    /// Accent of a plugin that declares none.
    public static let neutral = MascotColor(hex: "#8C96A8")!
    /// The drop of sweat shown with an error.
    public static let sweat = MascotColor(hex: "#5BB8F0")!
    /// The zzz shown while offline.
    public static let sleep = MascotColor(hex: "#8068B2")!
    /// Hearts and sparkles that pop out when the mascot is clicked.
    public static let heart = MascotColor(hex: "#FF6F9C")!
    public static let sparkle = MascotColor(hex: "#FFC94A")!
}

/// Which sticker stands for a plugin (or for the assistant), and its accent color.
public struct StickerLook: Hashable, Sendable {
    /// Shown for plugins without a sticker, or one this App does not have.
    public static let placeholder = "placeholder"

    /// File name without ".png" in the stickers folder; nil shows the placeholder.
    public var sticker: String?
    public var accent: MascotColor

    public init(sticker: String?, accent: MascotColor = MascotPalette.neutral) {
        self.sticker = sticker
        self.accent = accent
    }

    /// The look a manifest declares. A broken accent falls back to the neutral gray.
    public init(mascot: PluginMascot?) {
        self.init(
            sticker: mascot?.sticker,
            accent: mascot.flatMap { MascotColor(hex: $0.accent) } ?? MascotPalette.neutral)
    }

    public init(plugin: PluginView) {
        self.init(mascot: plugin.mascot)
    }

    /// The image to draw: this sticker when the App has it, otherwise the placeholder.
    public func resolved(in catalog: StickerCatalog) -> String {
        guard let sticker, catalog.contains(sticker) else { return Self.placeholder }
        return sticker
    }
}

/// One of the images of a sticker with eye layers, in the folder named like the sticker (for
/// example stickers/lily_veil/base.png). All have the sticker's canvas size.
public enum StickerLayer: String, CaseIterable, Sendable {
    /// The sticker with the eyes painted over in skin color.
    case base
    /// Only the eyes, open; base + eyes_open looks exactly like the sticker.
    case eyesOpen = "eyes_open"
    /// Only the eyes, closed.
    case eyesClosed = "eyes_closed"
    /// Optional: where the eyes may show, so eyes looking sideways slide under the hair.
    case eyesMask = "eyes_mask"

    /// The layers a sticker needs before it blinks; the mask is optional.
    public static let required: [StickerLayer] = [.base, .eyesOpen, .eyesClosed]
}

/// The stickers bundled with the App: the folder frontend/Design/stickers is copied into the
/// App as "stickers". Its app.json says which sticker the App itself uses, for example
/// `{"assistant": {"sticker": "lily_veil", "accent": "#8068B2"}}`, and characters.json what
/// the Bot with a sticker is called, for example `{"lily_veil": "健康Bot"}`.
public struct StickerCatalog: Hashable, Sendable {
    public static let folderName = "stickers"

    public var names: Set<String>
    public var roles: [String: PluginMascot]
    /// Bot names by sticker.
    public var characters: [String: String]
    /// Stickers with eye layers (StickerLayer), which blink and look around.
    public var layered: Set<String>

    public init(
        names: Set<String>, roles: [String: PluginMascot] = [:], characters: [String: String] = [:],
        layered: Set<String> = []
    ) {
        self.names = names
        self.roles = roles
        self.characters = characters
        self.layered = layered
    }

    public func contains(_ name: String) -> Bool { names.contains(name) }

    public func hasLayers(_ name: String) -> Bool { layered.contains(name) }

    /// The look of an App role such as "assistant" (Agent status, menu bar).
    public func look(role: String) -> StickerLook {
        StickerLook(mascot: roles[role])
    }

    /// The assistant as a whole: Agent status page, menu bar, today header.
    public var assistant: StickerLook { look(role: "assistant") }

    /// Every sticker by name, the placeholder last.
    public var sorted: [String] {
        names.subtracting([StickerLook.placeholder]).sorted()
            + (names.contains(StickerLook.placeholder) ? [StickerLook.placeholder] : [])
    }

    /// Reads a stickers folder: every .png is a sticker, app.json names the App's own,
    /// characters.json the Bots' names, and a folder with the layers makes a sticker layered.
    public static func load(folder: URL?) -> StickerCatalog {
        guard let folder,
            let files = try? FileManager.default.contentsOfDirectory(
                at: folder, includingPropertiesForKeys: nil)
        else { return StickerCatalog(names: []) }
        let names = files.filter { $0.pathExtension.lowercased() == "png" }
            .map { $0.deletingPathExtension().lastPathComponent }
        func read<T: Decodable>(_ file: String, as type: T.Type) -> T? {
            guard let data = try? Data(contentsOf: folder.appendingPathComponent(file)) else { return nil }
            return try? JSONDecoder().decode(type, from: data)
        }
        let layered = names.filter { name in
            let layers = folder.appendingPathComponent(name)
            return StickerLayer.required.allSatisfy { layer in
                let file = layers.appendingPathComponent("\(layer.rawValue).png")
                return FileManager.default.fileExists(atPath: file.path)
            }
        }
        return StickerCatalog(
            names: Set(names), roles: read("app.json", as: [String: PluginMascot].self) ?? [:],
            characters: read("characters.json", as: [String: String].self) ?? [:], layered: Set(layered))
    }

    public static func load(bundle: Bundle) -> StickerCatalog {
        load(folder: bundle.url(forResource: folderName, withExtension: nil))
    }

    /// The stickers of the running App.
    public static let main = load(bundle: .main)

    public static func url(of name: String, in bundle: Bundle = .main) -> URL? {
        bundle.url(forResource: name, withExtension: "png", subdirectory: folderName)
    }

    public static func url(of name: String, layer: StickerLayer, in bundle: Bundle = .main) -> URL? {
        bundle.url(forResource: layer.rawValue, withExtension: "png", subdirectory: "\(folderName)/\(name)")
    }
}

public enum MascotState: String, CaseIterable, Sendable {
    case idle, working, done, error, offline

    /// How long "done" lasts before the mascot goes back to idle.
    public static let doneDuration: TimeInterval = 3

    /// When several sources disagree the higher one is shown.
    public var priority: Int {
        switch self {
        case .idle: return 0
        case .done: return 1
        case .working: return 2
        case .error: return 3
        case .offline: return 4
        }
    }

    public static func combined(_ states: [MascotState]) -> MascotState {
        states.max { $0.priority < $1.priority } ?? .idle
    }

    /// One Agent run: running works, failed or cancelled is an error until the next run,
    /// success shows "done" for `doneDuration` after it finished.
    public static func forRun(
        _ run: Run?, now: Date, doneDuration: TimeInterval = MascotState.doneDuration
    ) -> MascotState {
        guard let run else { return .idle }
        switch run.status {
        case .running:
            return .working
        case .failed, .cancelled:
            return .error
        case .succeeded:
            guard let finished = run.finishedAt else { return .done }
            return now.timeIntervalSince(finished) < doneDuration ? .done : .idle
        }
    }

    /// A plugin: asleep when disabled or unsupported, an error when its setup failed, working
    /// while one of its jobs runs, otherwise the state of its latest run.
    /// `runs` are newest first, as `GET /api/v1/runs` returns them.
    public static func forPlugin(_ plugin: PluginView, runs: [Run], now: Date) -> MascotState {
        switch plugin.state {
        case .disabled, .unsupported:
            return .offline
        case .error:
            return .error
        case .enabled:
            let own = runs.filter {
                $0.job == "fetch:\(plugin.id)" || $0.job.hasPrefix("plugin:\(plugin.id):")
            }
            if own.contains(where: { $0.status == .running }) { return .working }
            return forRun(own.first, now: now)
        }
    }

    /// 知识库Bot (the backend reachable): working while the knowledge base is being organized or
    /// one of her inputs runs, an error when one of them failed, otherwise the state of the
    /// latest organizing.
    public static func forKnowledge(inputs: [PluginView], runs: [Run], now: Date) -> MascotState {
        let organizing = runs.filter { $0.job == "knowledge_organize" }
        if organizing.contains(where: { $0.status == .running }) { return .working }
        let states = inputs.map { forPlugin($0, runs: runs, now: now) }
        if states.contains(.working) { return .working }
        if states.contains(.error) { return .error }
        // The night's run as a whole, not its Agents' attempts.
        return forRun(organizing.first { $0.harness == nil }, now: now)
    }

    /// The assistant as a whole: asleep when the backend cannot be reached.
    public static func forAssistant(status: Status?, runs: [Run], now: Date) -> MascotState {
        guard let status, status.online else { return .offline }
        if runs.contains(where: { $0.status == .running }) { return .working }
        return forRun(runs.first ?? status.lastRun, now: now)
    }
}

public enum MascotEvent: Equatable, Sendable {
    case connected, disconnected
    case started, succeeded, failed
    case enabled, disabled
}

/// State changes from live events (e.g. polling runs): offline wins until reconnected,
/// errors stay until the next start or success, and "done" falls back to idle by itself.
public struct MascotStateMachine: Equatable, Sendable {
    public private(set) var stored: MascotState
    /// When the current state began; animations such as the "done" bounce start here.
    public private(set) var since: Date
    public var doneDuration: TimeInterval

    public init(
        state: MascotState = .idle, since: Date = Date(),
        doneDuration: TimeInterval = MascotState.doneDuration
    ) {
        stored = state
        self.since = since
        self.doneDuration = doneDuration
    }

    public func state(at date: Date) -> MascotState {
        if stored == .done && date.timeIntervalSince(since) >= doneDuration { return .idle }
        return stored
    }

    public mutating func handle(_ event: MascotEvent, at date: Date) {
        let current = state(at: date)
        let next: MascotState
        switch event {
        case .disconnected, .disabled:
            next = .offline
        case .connected, .enabled:
            next = current == .offline ? .idle : current
        case .started:
            next = current == .offline ? .offline : .working
        case .succeeded:
            next = current == .offline ? .offline : .done
        case .failed:
            next = current == .offline ? .offline : .error
        }
        // A new success restarts the bounce even if the last one is still showing.
        if next != current || (event == .succeeded && next == .done) {
            since = date
        }
        stored = next
    }
}

public enum MascotEffect: String, CaseIterable, Sendable {
    case sweat, zzz
}

/// One frame of the sticker. The whole image moves: offsets are fractions of its size
/// (positive = right, down), rotations are degrees around `anchor`, near the bottom center.
public struct MascotPose: Equatable, Sendable {
    public static let anchor = (x: 0.5, y: 0.92)

    public var offsetX: Double = 0
    public var offsetY: Double = 0
    public var scaleX: Double = 1
    public var scaleY: Double = 1
    public var rotation: Double = 0
    /// 1 in color; offline is grayed out a little.
    public var saturation: Double = 1
    public var effects: [MascotEffect] = []
    /// 0...1, how far a sweat drop has slid or a zzz has floated.
    public var effectPhase: Double = 0

    public init() {}

    public static func effects(for state: MascotState) -> [MascotEffect] {
        switch state {
        case .idle, .working, .done: return []
        case .error: return [.sweat]
        case .offline: return [.zzz]
        }
    }

    /// The tilt a state rests in; also the whole pose when motion is reduced.
    public static func restingRotation(for state: MascotState) -> Double {
        switch state {
        case .idle, .working, .done: return 0
        case .error: return -8
        case .offline: return 4
        }
    }

    public static func saturation(for state: MascotState) -> Double {
        state == .offline ? 0.4 : 1
    }

    /// The pose at `time` (seconds) for a state that began at `start`. With reduce motion the
    /// sticker keeps the state's tilt, color and overlays but does not move at all.
    public static func at(
        _ time: TimeInterval, state: MascotState, since start: TimeInterval = 0,
        reduceMotion: Bool = false
    ) -> MascotPose {
        var pose = MascotPose()
        pose.effects = effects(for: state)
        pose.saturation = saturation(for: state)
        pose.rotation = restingRotation(for: state)
        if reduceMotion {
            pose.effectPhase = 0.5
            return pose
        }
        let elapsed = max(0, time - start)
        let tau = 2 * Double.pi
        switch state {
        case .idle:
            // Float up and down, breathing a little.
            pose.offsetY = 0.02 * sin(tau * time / 3)
            let breath = 0.01 * cos(tau * time / 3)
            pose.scaleX = 1 + breath
            pose.scaleY = 1 + breath
        case .working:
            // Tilt the head from side to side with a small nod.
            pose.rotation = 6 * sin(tau * time / 1.2)
            pose.offsetY = -0.015 * abs(sin(tau * time / 1.2))
        case .done:
            let bounce = 0.6
            if elapsed < bounce {
                let progress = elapsed / bounce
                pose.offsetY = -0.08 * sin(Double.pi * progress)
                if progress > 0.8 {
                    pose.scaleY = 0.94  // landing squash
                    pose.scaleX = 1.03
                }
            } else {
                pose.offsetY = 0.02 * sin(tau * (elapsed - bounce) / 3)
            }
        case .error:
            pose.offsetY = 0.04
            pose.rotation = -8 + 1.5 * sin(tau * time / 2.5)
            pose.scaleY = 0.97
            pose.effectPhase = (time / 2).truncatingRemainder(dividingBy: 1)
        case .offline:
            let breath = 0.015 * sin(tau * time / 4)
            pose.scaleX = 1 + breath
            pose.scaleY = 1 + breath
            pose.effectPhase = (time / 3).truncatingRemainder(dividingBy: 1)
        }
        return pose
    }
}
