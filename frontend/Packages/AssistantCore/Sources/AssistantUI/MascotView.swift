import AssistantCore
import SwiftUI

#if canImport(UIKit)
    import UIKit
#elseif canImport(AppKit)
    import AppKit
#endif

/// A plugin's sticker with its state animation (docs/mascot.md). The whole image moves:
/// floating, bouncing, tilting and a little scaling around the bottom center, with a drop of
/// sweat on errors and a zzz while offline. Stickers with eye layers also blink, glance around
/// and look at the pointer; reactions (a click, the pointer coming over, the Bot talking) play
/// on top. With Reduce Motion only the still pose is shown.
public struct MascotView: View {
    let look: StickerLook
    let state: MascotState
    let since: Date
    let catalog: StickerCatalog
    let pointer: CGPoint?
    let cue: MascotCue?
    let interactive: Bool
    let liveliness: MascotLiveliness
    let characterAnimations: Bool
    let animationRequest: MascotAnimationRequest?
    @Environment(\.scenePhase) private var scenePhase
    #if os(macOS)
        @Environment(\.controlActiveState) private var controlActiveState
    #endif
    #if !os(watchOS)
        @State private var animationClock = MascotAnimationClock()
        @State private var characterRequest: MascotAnimationRequest?
    #endif
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The pointer over this mascot (interactive only), in its own coordinates.
    @State private var hover: CGPoint?
    /// The latest click or greeting on the mascot itself, and where it was clicked.
    @State private var touch: MascotCue?
    @State private var touchedAt: CGPoint?
    @State private var lastGreeting = Date.distantPast
    @State private var gaze = GazeFollower()

    /// - Parameters:
    ///   - pointer: the mouse in global coordinates (SwiftUI's `.global` space, for example from
    ///     `onContinuousHover(coordinateSpace: .global)` on the window's content); the face looks
    ///     at it. nil: it glances around by itself.
    ///   - cue: the latest reaction to play, for example `MascotCue(.talk)` when the Bot said
    ///     something. A new cue plays once.
    ///   - interactive: the pointer coming over it plays `.greet` (at most every few seconds),
    ///     a click plays `.poke` with a burst of hearts and sparkles.
    ///   - liveliness: `.full` for big mascots; `.subtle` for small avatars, which only move
    ///     their eyes and are redrawn less often.
    ///   - characterAnimations: opt in only for Bot headers and the gallery; never for avatars.
    ///   - animationRequest: a named gallery act, or a random character act when its name is nil.
    public init(
        look: StickerLook, state: MascotState = .idle, since: Date = Date(),
        catalog: StickerCatalog = .main, pointer: CGPoint? = nil, cue: MascotCue? = nil,
        interactive: Bool = false, liveliness: MascotLiveliness = .full,
        characterAnimations: Bool = false, animationRequest: MascotAnimationRequest? = nil
    ) {
        self.look = look
        self.state = state
        self.since = since
        self.catalog = catalog
        self.pointer = pointer
        self.cue = cue
        self.interactive = interactive
        self.liveliness = liveliness
        self.characterAnimations = characterAnimations
        self.animationRequest = animationRequest
    }

    public var body: some View {
        GeometryReader { proxy in
            let side = min(proxy.size.width, proxy.size.height)
            let name = look.resolved(in: catalog)
            let size = proxy.size
            let global = proxy.frame(in: .global)
            // 30 fps is smooth enough for floating and hopping and costs half of 60.
            let interval = liveliness == .full ? 1.0 / 30 : 1.0 / 15
            Group {
                if reduceMotion || !active || (liveliness == .subtle && !catalog.hasLayers(name)) {
                    still(name: name, side: side)
                } else {
                    TimelineView(.animation(minimumInterval: interval)) { context in
                        animated(name: name, at: context.date, side: side, size: size, global: global)
                    }
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .aspectRatio(1, contentMode: .fit)
        .modifier(
            MascotTouch(
                enabled: interactive && !reduceMotion && active, hover: $hover, onEnter: greet, onTap: poke))
        #if !os(watchOS)
            .onChange(of: cue, initial: true) { _, newCue in
                if characterAnimations, liveliness == .full, newCue?.kind == .poke, let newCue {
                    characterRequest = MascotAnimationRequest(at: newCue.at)
                }
            }
            .onChange(of: active && !reduceMotion) { _, running in
                if !running { animationClock.suspend(request: latestRequest) }
            }
        #endif
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(MascotView.label(for: state)))
    }

    @ViewBuilder private func still(name: String, side: CGFloat) -> some View {
        #if !os(watchOS)
            if characterAnimations && liveliness == .full && !MascotAnimation.all(for: name).isEmpty {
                MascotAnimationScene(
                    name: name, frame: MascotFrame(pose: MascotPose.at(0, state: state, reduceMotion: true)),
                    playback: nil, side: side)
            } else {
                MascotFigure(name: name, pose: MascotPose.at(0, state: state, reduceMotion: true), side: side)
            }
        #else
            MascotFigure(name: name, pose: MascotPose.at(0, state: state, reduceMotion: true), side: side)
        #endif
    }

    private var active: Bool {
        #if os(macOS)
            scenePhase == .active && controlActiveState != .inactive
        #else
            scenePhase == .active
        #endif
    }

    #if !os(watchOS)
        private var latestRequest: MascotAnimationRequest? {
            guard let a = animationRequest, let b = characterRequest else {
                return animationRequest ?? characterRequest
            }
            return a.at > b.at ? a : b
        }
    #endif

    private func animated(name: String, at date: Date, side: CGFloat, size: CGSize, global: CGRect)
        -> some View
    {
        let time = date.timeIntervalSince1970
        let seed = MascotMotion.seed(for: name)
        let layers = catalog.hasLayers(name) ? StickerLayers.shared.layers(named: name) : nil
        let hasActs = characterAnimations && liveliness == .full && !MascotAnimation.all(for: name).isEmpty
        var eyes = layers?.eyeCenter ?? CGPoint(x: 0.5, y: 0.65)
        #if !os(watchOS)
            if hasActs {
                let stage = MascotStagePlacement(named: name)
                let point = stage.point(eyes.x, eyes.y)
                eyes = CGPoint(x: point.x / 240, y: point.y / 240)
            }
        #endif
        // The sticker is a centered square inside this view.
        let inset = CGPoint(x: (size.width - side) / 2, y: (size.height - side) / 2)
        let point = pointer.map { CGPoint(x: $0.x - global.minX, y: $0.y - global.minY) } ?? hover
        let pointed = point.map {
            MascotGaze.toward(dx: ($0.x - inset.x) / side - eyes.x, dy: ($0.y - inset.y) / side - eyes.y)
        }
        let proposedReaction = MascotCue.latest(cue, touch)
        let reaction = hasActs && proposedReaction?.kind == .poke ? nil : proposedReaction
        let target = MascotMotion.target(at: time, state: state, seed: seed, pointer: pointed, cue: reaction)
        let frame = MascotMotion.frame(
            at: time, state: state, since: since.timeIntervalSince1970, seed: seed,
            look: gaze.follow(target, at: time), cue: reaction, liveliness: liveliness)
        // Hearts fly from where it was clicked, or from above the eyes.
        var origin = (x: Double(eyes.x), y: Double(eyes.y) - 0.2)
        if let touchedAt, reaction == touch {
            origin = (Double((touchedAt.x - inset.x) / side), Double((touchedAt.y - inset.y) / side))
        }
        let burst =
            liveliness == .full ? MascotMotion.burst(of: reaction, at: time, from: origin, seed: seed) : []
        return ZStack {
            #if !os(watchOS)
                if hasActs {
                    MascotAnimationScene(
                        name: name, frame: frame,
                        playback: animationClock.sample(name: name, state: state, date: date, request: latestRequest),
                        side: side)
                } else {
                    MascotFigure(name: name, pose: frame.pose, side: side,
                                 eyes: catalog.hasLayers(name) ? frame.eyes : nil)
                }
            #else
                MascotFigure(name: name, pose: frame.pose, side: side,
                             eyes: catalog.hasLayers(name) ? frame.eyes : nil)
            #endif
            if !burst.isEmpty {
                MascotBurst(particles: burst, side: side)
            }
        }
        .frame(width: side, height: side)
    }

    private func greet() {
        let now = Date()
        guard now.timeIntervalSince(lastGreeting) > 6,
            MascotCue.latest(cue, touch)?.progress(at: now.timeIntervalSince1970) == nil
        else { return }
        lastGreeting = now
        touch = MascotCue(.greet, at: now)
        touchedAt = nil
    }

    private func poke(at location: CGPoint) {
        #if !os(watchOS)
            if characterAnimations && liveliness == .full && !MascotAnimation.all(for: look.resolved(in: catalog)).isEmpty {
                characterRequest = MascotAnimationRequest()
                touch = nil
                touchedAt = nil
                return
            }
        #endif
        touch = MascotCue(.poke)
        touchedAt = location
    }

    public static func label(for state: MascotState) -> String {
        switch state {
        case .idle: return "待机中"
        case .working: return "正在处理"
        case .done: return "已完成"
        case .error: return "遇到问题"
        case .offline: return "离线"
        }
    }
}

/// Smooths the gaze from one frame to the next. A class kept in @State, so updating it while
/// drawing a frame does not invalidate the view.
final class GazeFollower {
    private var gaze = MascotGaze.ahead
    private var time: TimeInterval?

    func follow(_ target: MascotGaze, at now: TimeInterval) -> MascotGaze {
        gaze = MascotMotion.follow(gaze, toward: target, after: now - (time ?? now - 1))
        time = now
        return gaze
    }
}

/// Hover and clicks on an interactive mascot. Hovering does not exist on watchOS.
private struct MascotTouch: ViewModifier {
    let enabled: Bool
    @Binding var hover: CGPoint?
    let onEnter: () -> Void
    let onTap: (CGPoint) -> Void

    func body(content: Content) -> some View {
        if enabled {
            content
                .contentShape(Rectangle())
                .onTapGesture(coordinateSpace: .local) { onTap($0) }
                #if !os(watchOS)
                    .onContinuousHover { phase in
                        switch phase {
                        case .active(let location):
                            if hover == nil { onEnter() }
                            hover = location
                        case .ended:
                            hover = nil
                        }
                    }
                #endif
        } else {
            content
        }
    }
}

/// Every bundled sticker in every state, for checking new artwork and the motion: the big ones
/// can be poked and greeted, the small ones are avatars that only move their eyes.
public struct MascotGallery: View {
    let catalog: StickerCatalog

    public init(catalog: StickerCatalog = .main) {
        self.catalog = catalog
    }

    public var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                if catalog.names.isEmpty {
                    Text("App 里没有贴图（frontend/Design/stickers 没有打包进来）")
                        .foregroundStyle(.secondary)
                }
                #if !os(watchOS)
                    ForEach(catalog.sorted.filter { !MascotAnimation.all(for: $0).isEmpty }, id: \.self) { name in
                        MascotAnimationGalleryRow(name: name, catalog: catalog)
                    }
                    Text("五种状态").font(.title2.bold())
                #endif
                ForEach(MascotState.allCases, id: \.self) { state in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(MascotView.label(for: state)).font(.headline)
                        ScrollView(.horizontal) {
                            HStack(spacing: 12) {
                                ForEach(catalog.sorted, id: \.self) { name in
                                    VStack(spacing: 4) {
                                        MascotView(
                                            look: StickerLook(sticker: name), state: state,
                                            catalog: catalog, interactive: true
                                        )
                                        .frame(width: 96, height: 96)
                                        MascotView(
                                            look: StickerLook(sticker: name), state: state,
                                            catalog: catalog, liveliness: .subtle
                                        )
                                        .frame(width: 24, height: 24)
                                        Text(name).font(.caption2)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .padding()
        }
    }
}

#if !os(watchOS)
    /// Kept below the timeline boundary: advancing an act never invalidates the Bot page.
    private final class MascotAnimationClock {
        private var player = MascotAnimationPlayer()

        func sample(name: String, state: MascotState, date: Date, request: MascotAnimationRequest?) -> MascotAnimationPlayback? {
            player.sample(sticker: name, state: state, at: date.timeIntervalSince1970,
                          localHour: Calendar.current.component(.hour, from: date), request: request)
        }

        func suspend(request: MascotAnimationRequest?) {
            _ = player.sample(sticker: "", state: .idle, at: 0, localHour: 12, request: request, active: false)
        }
    }

    private struct MascotAnimationGalleryRow: View {
        let name: String
        let catalog: StickerCatalog
        @State private var request: MascotAnimationRequest?
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            let acts = MascotAnimation.all(for: name)
            VStack(alignment: .leading, spacing: 10) {
                Text(catalog.characters[name] ?? name).font(.headline)
                MascotView(
                    look: StickerLook(sticker: name, accent: acts[0].accent), catalog: catalog,
                    interactive: true, characterAnimations: true, animationRequest: request
                )
                .frame(width: 240, height: 240)
                .frame(maxWidth: .infinity)
                .background(acts[0].accent.color.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 120))], alignment: .leading, spacing: 8) {
                    ForEach(acts, id: \.self) { act in
                        Button(act.title) { request = MascotAnimationRequest(act) }
                            .buttonStyle(.bordered)
                            .tint(acts[0].accent.color)
                            .disabled(reduceMotion)
                    }
                }
                if reduceMotion {
                    Text("已开启减弱动态效果，显示静止姿势。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
#endif
