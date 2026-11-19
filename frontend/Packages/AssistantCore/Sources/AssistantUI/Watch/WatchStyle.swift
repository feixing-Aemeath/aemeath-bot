#if os(watchOS)
    import AssistantCore
    import SwiftUI
    import WatchKit

    /// Sizes on a screen of this width: laid out for the 49 mm Ultra, narrower on smaller watches.
    enum WatchScale {
        static let width = WKInterfaceDevice.current().screenBounds.width
        static let factor = min(width / 205, 1)
        /// 42 mm and smaller.
        static let isCompact = width < 200

        static func of(_ value: CGFloat) -> CGFloat { (value * factor).rounded() }

        /// The row of the status bar's time, from the top of the screen, measured on both sizes.
        static let statusTop: CGFloat = isCompact ? 13 : 19
        static let statusHeight: CGFloat = 20
        /// Left of the text on every page. The status row's kicker starts at the same place.
        static let margin: CGFloat = of(14)
    }

    /// True while the display is in Always On, when the wrist is down: the pages go quiet.
    @propertyWrapper
    struct WatchDimmed: DynamicProperty {
        @Environment(\.isLuminanceReduced) private var reduced

        var wrappedValue: Bool {
            #if DEBUG
                // `-watch.dim 1` at launch, for the simulator, which never dims by itself.
                if UserDefaults.standard.bool(forKey: "watch.dim") { return true }
            #endif
            return reduced
        }
    }

    extension StickerLook {
        /// The Bot's color for small text and marks on the watch's black screen.
        var ink: Color { accent.onBlack.color }
    }

    extension StickerLook {
        /// A page's Bot. The characters are the manifests' own, for the moments the iPhone has
        /// not said yet (no snapshot, or a plugin that is off).
        static func watch(_ bot: WatchBot, in snapshot: WatchSnapshot?) -> StickerLook {
            switch bot {
            case .assistant:
                return StickerCatalog.main.assistant
            case .courses:
                return StickerLook(
                    mascot: (snapshot?.courses ?? snapshot?.tomorrowCourses)?.mascot
                        ?? PluginMascot(sticker: "moning", accent: "#D65F63"))
            case .health:
                return StickerLook(mascot: snapshot?.health?.mascot ?? PluginMascot(sticker: "lily_veil", accent: "#8068B2"))
            }
        }
    }

    /// What a page is called, in the status bar's row beside the time, where it costs no room.
    /// A view as the system navigation title crashes UIKit's bar on watchOS 27, so this is
    /// drawn over a page without a bar.
    struct WatchKicker: ViewModifier {
        let text: String
        let color: Color
        var look: StickerLook?
        var state: MascotState = .idle
        @WatchDimmed private var dimmed

        func body(content: Content) -> some View {
            content
                .toolbar(.hidden, for: .navigationBar)
                .overlay(alignment: .topLeading) {
                    HStack(spacing: 4) {
                        if let look, !dimmed {
                            MascotView(look: look, state: state, liveliness: .subtle)
                                .frame(width: WatchScale.of(22), height: WatchScale.of(22))
                        }
                        Text(text)
                            .font(.system(size: WatchScale.of(15), weight: .semibold))
                            .foregroundStyle(color)
                            .lineLimit(1)
                    }
                    .padding(.leading, WatchScale.margin)
                    .frame(height: WatchScale.statusHeight, alignment: .center)
                    .padding(.top, WatchScale.statusTop)
                    .ignoresSafeArea()
                }
        }
    }

    extension View {
        func watchKicker(_ text: String, color: Color, look: StickerLook? = nil, state: MascotState = .idle) -> some View {
            modifier(WatchKicker(text: text, color: color, look: look, state: state))
        }

        /// The page's color as light on black, fading from a corner: the OLED stays dark where
        /// nothing is drawn, and turning the Digital Crown crossfades one Bot's light into the next.
        func watchBackdrop(_ color: Color, glow: (color: Color, at: UnitPoint)? = nil) -> some View {
            containerBackground(for: .tabView) { WatchBackdrop(color: color, glow: glow) }
        }

        /// A scrolling page's text fading out before it reaches the status row, as under a
        /// system title.
        func watchScrollFade() -> some View {
            mask {
                VStack(spacing: 0) {
                    // Nothing shows behind the row of the time; below it the text comes back quickly.
                    LinearGradient(
                        stops: [.init(color: .clear, location: 0.8), .init(color: .black, location: 1)],
                        startPoint: .top, endPoint: .bottom
                    )
                    .frame(height: WatchScale.statusTop + WatchScale.statusHeight + 8)
                    Color.black
                }
                .ignoresSafeArea()
            }
        }
    }

    struct WatchBackdrop: View {
        let color: Color
        var glow: (color: Color, at: UnitPoint)? = nil
        @WatchDimmed private var dimmed

        var body: some View {
            ZStack {
                Color.black
                if !dimmed {
                    RadialGradient(
                        colors: [color.opacity(0.36), color.opacity(0.1), .clear], center: UnitPoint(x: 0.08, y: -0.08),
                        startRadius: 0, endRadius: WatchScale.width)
                    if let glow {
                        // A soft light where the Bot sits, so it is lit rather than pasted on.
                        RadialGradient(
                            colors: [glow.color.opacity(0.4), glow.color.opacity(0.1), .clear], center: glow.at, startRadius: 0,
                            endRadius: WatchScale.width * 0.5)
                    }
                }
            }
        }
    }

    /// A progress line, its unfilled part quiet. The hero's stickers sit on it.
    struct WatchProgressLine: View {
        let value: Double?
        let tint: Color
        var height: CGFloat = 4

        var body: some View {
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(.white.opacity(0.14))
                    if let value {
                        Capsule()
                            .fill(LinearGradient(colors: [tint.opacity(0.65), tint], startPoint: .leading, endPoint: .trailing))
                            .frame(width: max(height, proxy.size.width * value))
                    }
                }
            }
            .frame(height: height)
        }
    }

    /// A ring out of 1, with Activity's round caps and the light of its color at the end.
    struct WatchRing: View {
        let value: Double
        let tint: Color
        var lineWidth: CGFloat = 11
        @WatchDimmed private var dimmed

        var body: some View {
            ZStack {
                Circle().stroke(tint.opacity(dimmed ? 0.12 : 0.2), lineWidth: lineWidth)
                Circle()
                    .trim(from: 0, to: value)
                    .stroke(
                        AngularGradient(colors: [tint.opacity(0.55), tint], center: .center, startAngle: .degrees(0), endAngle: .degrees(360 * value)),
                        style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
                    )
                    .rotationEffect(.degrees(-90))
                    .shadow(color: dimmed ? .clear : tint.opacity(0.45), radius: 5)
            }
        }
    }

    /// One piece per todo, filling up as they are ticked off.
    struct WatchSegments: View {
        let total: Int
        let done: Int
        let tint: Color

        var body: some View {
            HStack(spacing: 3) {
                ForEach(0..<max(total, 1), id: \.self) { index in
                    Capsule().fill(index < done ? tint : Color.white.opacity(0.14))
                }
            }
            .frame(height: 4)
            .animation(.snappy, value: done)
        }
    }

    /// What is under the finger gives a little.
    struct WatchPressStyle: ButtonStyle {
        func makeBody(configuration: Configuration) -> some View {
            configuration.label
                .scaleEffect(configuration.isPressed ? 0.93 : 1)
                .opacity(configuration.isPressed ? 0.75 : 1)
                .animation(.snappy(duration: 0.15), value: configuration.isPressed)
        }
    }

    /// A hairline between rows; rows on black have no cards around them.
    struct WatchHairline: View {
        var body: some View {
            Rectangle().fill(.white.opacity(0.14)).frame(height: 0.5)
        }
    }
#endif
