#if !os(watchOS)
    import SwiftUI
    #if os(macOS)
        import AppKit
    #else
        import UIKit
    #endif

    /// The App's colors on the Mac and the iPhone, after Claude's own app: a warm beige sidebar
    /// next to a lighter page, white cards with a hairline border, a gray rounded row for the
    /// selection. Each has a light and a dark value.
    enum Palette {
        static let sidebar = Color(light: 0xF2F0E9, dark: 0x1F1E1C)
        static let page = Color(light: 0xFAF9F5, dark: 0x262624)
        static let card = Color(light: 0xFFFFFF, dark: 0x30302E)
        static let hairline = Color(light: 0xE6E3DA, dark: 0x3E3E3A)
        /// The chosen row in the sidebar.
        static let selection = Color(light: 0xE2DFD5, dark: 0x3A3A37)
        static let hover = Color(light: 0xEAE7DF, dark: 0x2C2C2A)
        /// What I wrote in a chat, and small notes.
        static let bubble = Color(light: 0xEFEDE5, dark: 0x393937)
        /// The wavy line between the cards and the chat.
        static let trail = Color(light: 0xC9C4B7, dark: 0x5E5C56)
        /// Claude's clay orange, for the send button.
        static let clay = Color(light: 0xD97757, dark: 0xD97757)
    }

    /// Claude's own typefaces are licensed to Anthropic and cannot ship in the App. The closest
    /// ones Apple has: its serif (New York, with Songti for Chinese) for titles, the system sans
    /// (SF, PingFang) for everything else. The Mac has Songti built in; an iPhone or iPad
    /// downloads it from Apple (`ChineseSerif`), and draws the titles' Chinese in PingFang until then.
    enum Typography {
        static func serif(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
            #if os(iOS)
                if ChineseSerif.shared.ready {
                    return Font(ChineseSerif.font(size, weight))
                }
            #endif
            return .system(size: size, weight: weight, design: .serif)
        }

        static var cardTitle: Font { serif(15) }
        static var pageTitle: Font { serif(28) }
    }

    #if os(iOS)
        /// Songti on an iPhone or iPad, so the titles' Chinese looks as on the Mac. iOS does not come
        /// with it but can download Apple's own copy, the same font the Mac has; after the first time
        /// it loads from the device without a network. Views that draw a serif title read `ready`,
        /// so they redraw when it arrives.
        @Observable
        final class ChineseSerif {
            static let shared = ChineseSerif()

            /// The faces the Mac's New York falls back to: Bold for semibold and heavier, Regular below.
            static let regular = "STSongti-SC-Regular"
            static let bold = "STSongti-SC-Bold"

            private(set) var ready = false
            @ObservationIgnored private var started = false

            func load() {
                guard !started else { return }
                started = true
                let faces = [Self.regular, Self.bold]
                let descriptors = faces.map { CTFontDescriptorCreateWithNameAndSize($0 as CFString, 0) }
                CTFontDescriptorMatchFontDescriptorsWithProgressHandler(descriptors as CFArray, nil) { state, _ in
                    if state == .didFinish {
                        DispatchQueue.main.async {
                            self.ready = faces.allSatisfy { UIFont(name: $0, size: 12) != nil }
                        }
                    }
                    return true
                }
            }

            /// New York at this size and weight, with Songti after it for the characters it lacks.
            static func font(_ size: CGFloat, _ weight: Font.Weight) -> UIFont {
                let heavy = [Font.Weight.semibold, .bold, .heavy, .black].contains(weight)
                let base = UIFont.systemFont(ofSize: size, weight: uiWeight(weight)).fontDescriptor
                let serif = base.withDesign(.serif) ?? base
                let songti = UIFontDescriptor(name: heavy ? bold : regular, size: size)
                return UIFont(descriptor: serif.addingAttributes([.cascadeList: [songti]]), size: size)
            }

            static func uiWeight(_ weight: Font.Weight) -> UIFont.Weight {
                switch weight {
                case .ultraLight: return .ultraLight
                case .thin: return .thin
                case .light: return .light
                case .medium: return .medium
                case .semibold: return .semibold
                case .bold: return .bold
                case .heavy: return .heavy
                case .black: return .black
                default: return .regular
                }
            }
        }
    #endif

    extension Color {
        init(light: UInt32, dark: UInt32) {
            func components(_ value: UInt32) -> (CGFloat, CGFloat, CGFloat) {
                (CGFloat((value >> 16) & 0xFF) / 255, CGFloat((value >> 8) & 0xFF) / 255, CGFloat(value & 0xFF) / 255)
            }
            #if os(macOS)
                self.init(
                    nsColor: NSColor(name: nil) { appearance in
                        let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                        let (red, green, blue) = components(isDark ? dark : light)
                        return NSColor(srgbRed: red, green: green, blue: blue, alpha: 1)
                    })
            #else
                self.init(
                    uiColor: UIColor { traits in
                        let (red, green, blue) = components(traits.userInterfaceStyle == .dark ? dark : light)
                        return UIColor(red: red, green: green, blue: blue, alpha: 1)
                    })
            #endif
        }
    }

    /// Between the cards and the chat: a paper plane trailing a wavy line, like the squiggles on
    /// Claude's site.
    struct WavyDivider: View {
        var tint: Color

        var body: some View {
            HStack(spacing: 6) {
                Wave()
                    .stroke(Palette.trail, style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round))
                    .frame(height: 10)
                Image(systemName: "paperplane.fill")
                    .font(.system(size: 13))
                    .foregroundStyle(tint)
                    .rotationEffect(.degrees(-8))
                    .offset(y: -2)
                    .accessibilityHidden(true)
            }
            .padding(.vertical, 2)
        }
    }

    /// A gentle sine wave across the rect, growing from flat at the start.
    struct Wave: Shape {
        var wavelength: CGFloat = 30
        var amplitude: CGFloat = 3

        func path(in rect: CGRect) -> Path {
            Path { path in
                let steps = max(Int(rect.width / 2), 1)
                for step in 0...steps {
                    let x = rect.minX + rect.width * CGFloat(step) / CGFloat(steps)
                    let grow = min(1, (x - rect.minX) / (wavelength * 1.5))
                    let y = rect.midY + sin((x - rect.minX) / wavelength * 2 * .pi) * amplitude * grow
                    if step == 0 {
                        path.move(to: CGPoint(x: x, y: y))
                    } else {
                        path.addLine(to: CGPoint(x: x, y: y))
                    }
                }
            }
        }
    }
#endif
