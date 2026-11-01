#if !os(watchOS)
    import AssistantCore
    import ImageIO
    import SwiftUI
    #if os(macOS)
        import AppKit
    #else
        import UIKit
    #endif

    extension Bot {
        var accent: Color { look.accent.color }

        /// The plugin's icon when it is an SF Symbol, else a generic one.
        var symbol: String {
            #if os(macOS)
                let known = NSImage(systemSymbolName: icon, accessibilityDescription: nil) != nil
            #else
                let known = UIImage(systemName: icon) != nil
            #endif
            return known ? icon : "puzzlepiece.extension"
        }
    }

    /// A Bot's face in a circle tinted with its accent color: the sticker for a character, the
    /// plugin's icon for a message source; with a dot for its state. A live avatar blinks and
    /// looks at the mouse.
    struct BotAvatar: View {
        let bot: Bot
        var state: MascotState?
        var live = false

        var body: some View {
            GeometryReader { proxy in
                let side = min(proxy.size.width, proxy.size.height)
                ZStack(alignment: .bottomTrailing) {
                    Group {
                        if bot.kind == .source {
                            Image(systemName: bot.symbol)
                                .font(.system(size: side * 0.42, weight: .medium))
                                .foregroundStyle(bot.accent)
                                .frame(width: side, height: side)
                        } else {
                            // Zoomed on the face, the same size for every character.
                            let name = bot.look.resolved(in: .main)
                            let place = FacePlacement.avatar(name, side: side)
                            face(name)
                                .frame(width: place.drawn, height: place.drawn)
                                .position(place.center)
                                .frame(width: side, height: side)
                        }
                    }
                    .background(bot.accent.opacity(0.15))
                    .clipShape(Circle())
                    .overlay(Circle().stroke(bot.accent.opacity(0.45), lineWidth: 1))
                    if let state {
                        Circle()
                            .fill(state.color)
                            .frame(width: side * 0.3, height: side * 0.3)
                            .overlay(Circle().stroke(Palette.sidebar, lineWidth: 2))
                    }
                }
            }
            .aspectRatio(1, contentMode: .fit)
        }

        @ViewBuilder func face(_ name: String) -> some View {
            if live {
                #if os(macOS)
                    LiveMascot(look: bot.look, state: state ?? .idle, liveliness: .subtle)
                #else
                    MascotView(look: bot.look, state: state ?? .idle, liveliness: .subtle)
                #endif
            } else {
                StickerImage(name: name)
            }
        }
    }

    /// Where a sticker's square canvas goes so its face has the same size and place for every
    /// character. The stickers were cut differently (成绩's and 健康's fill their canvas, the
    /// later ones with hats and a halo do not), so the eye layer sets the scale; a sticker
    /// without one is fitted by its outline.
    @MainActor
    enum FacePlacement {
        struct Place {
            let drawn: CGFloat
            let center: CGPoint
        }

        /// A round avatar: the eyes span 46 % of the circle, their middle a little below its
        /// center.
        static func avatar(_ name: String, side: CGFloat) -> Place {
            if let eyes = StickerBounds.eyes(of: name) {
                return place(eyes, side: side, span: 0.46, at: CGPoint(x: 0.5, y: 0.52))
            }
            let bounds = StickerBounds.of(name)
            let drawn = side * min(0.92 / bounds.height, 1.3 / bounds.width)
            return Place(
                drawn: drawn,
                center: CGPoint(
                    x: side / 2 + (0.5 - bounds.midX) * drawn, y: side * 0.56 + (0.5 - bounds.midY) * drawn))
        }

        /// The character peeking up in a page's header: the eyes span 30 % of the box, low in it.
        static func header(_ name: String, side: CGFloat) -> Place {
            guard let eyes = StickerBounds.eyes(of: name) else {
                return Place(drawn: side, center: CGPoint(x: side / 2, y: side / 2))
            }
            return place(eyes, side: side, span: 0.30, at: CGPoint(x: 0.45, y: 0.78))
        }

        private static func place(_ eyes: CGRect, side: CGFloat, span: CGFloat, at target: CGPoint) -> Place {
            let drawn = side * span / eyes.width
            return Place(
                drawn: drawn,
                center: CGPoint(
                    x: side * target.x + (0.5 - eyes.midX) * drawn, y: side * target.y + (0.5 - eyes.midY) * drawn))
        }
    }

    /// Where a sticker's character (or its eyes) sits inside its square canvas (unit
    /// coordinates, top left origin), from the alpha of the image.
    @MainActor
    enum StickerBounds {
        private static var cache: [String: CGRect] = [:]
        private static var eyeCache: [String: CGRect?] = [:]

        static func of(_ name: String) -> CGRect {
            if let bounds = cache[name] { return bounds }
            let bounds = StickerCatalog.url(of: name).flatMap(measure) ?? CGRect(x: 0, y: 0, width: 1, height: 1)
            cache[name] = bounds
            return bounds
        }

        /// The open eyes, for stickers with eye layers.
        static func eyes(of name: String) -> CGRect? {
            if let bounds = eyeCache[name] { return bounds }
            let bounds = StickerCatalog.url(of: name, layer: .eyesOpen).flatMap(measure)
            eyeCache[name] = bounds
            return bounds
        }

        private static func measure(_ url: URL) -> CGRect? {
            let side = 128
            guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                let image = CGImageSourceCreateThumbnailAtIndex(
                    source, 0,
                    [
                        kCGImageSourceThumbnailMaxPixelSize: side,
                        kCGImageSourceCreateThumbnailFromImageAlways: true,
                    ] as CFDictionary)
            else { return nil }
            var pixels = [UInt8](repeating: 0, count: side * side * 4)
            guard
                let context = CGContext(
                    data: &pixels, width: side, height: side, bitsPerComponent: 8, bytesPerRow: side * 4,
                    space: CGColorSpace(name: CGColorSpace.sRGB)!,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
            else { return nil }
            context.draw(image, in: CGRect(x: 0, y: 0, width: side, height: side))
            var minX = side, minY = side, maxX = -1, maxY = -1
            for y in 0..<side {
                for x in 0..<side where pixels[(y * side + x) * 4 + 3] > 32 {
                    minX = min(minX, x)
                    maxX = max(maxX, x)
                    minY = min(minY, y)
                    maxY = max(maxY, y)
                }
            }
            guard maxX >= minX, maxY >= minY else { return nil }
            let unit = 1 / Double(side)
            return CGRect(
                x: Double(minX) * unit, y: Double(minY) * unit, width: Double(maxX - minX + 1) * unit,
                height: Double(maxY - minY + 1) * unit)
        }
    }

    /// 今天, 昨天 or a date above a day's first message.
    struct DayDivider: View {
        let text: String

        var body: some View {
            Text(text)
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 3)
                .background(Capsule().fill(Palette.bubble))
                .frame(maxWidth: .infinity)
                .padding(.top, 4)
        }
    }

    /// One message: the Bot's on the left next to its face in a bubble of its color, mine on the
    /// right in warm gray as in Claude's app, the App's notes small in the middle.
    struct MessageView: View {
        @EnvironmentObject private var store: AppStore
        let message: BotMessage
        let bot: Bot
        @State private var hovering = false

        var time: String { DueText.format(message.date, "HH:mm", store.calendar) }

        var body: some View {
            switch message.author {
            case .bot: fromBot
            case .me: fromMe
            case .app:
                Text(message.text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Capsule().fill(Palette.bubble))
                    .frame(maxWidth: .infinity)
            }
        }

        var fromBot: some View {
            HStack(alignment: .top, spacing: 10) {
                BotAvatar(bot: bot)
                    .frame(width: 32, height: 32)
                VStack(alignment: .leading, spacing: 4) {
                    VStack(alignment: .leading, spacing: 6) {
                        if let title = message.title {
                            Text(title).font(.headline)
                        }
                        if !message.text.isEmpty {
                            MarkdownText(message.text)
                        }
                        if !message.todos.isEmpty {
                            Divider()
                            Text("提炼出的待办（\(message.todos.count)）")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                            ForEach(message.todos) { todo in
                                TodoRow(todo: todo, compact: true)
                            }
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(
                        UnevenRoundedRectangle(
                            topLeadingRadius: 4, bottomLeadingRadius: 14, bottomTrailingRadius: 14,
                            topTrailingRadius: 14
                        )
                        .fill(message.failed ? Color.red.opacity(0.09) : bot.accent.opacity(hovering ? 0.13 : 0.08)))
                    .onHover { hovering = $0 }
                    .contextMenu {
                        Button("拷贝") { copy(message.title.map { "\($0)\n\(message.text)" } ?? message.text) }
                    }
                    Text(time).font(.caption2).foregroundStyle(.tertiary)
                }
                .frame(maxWidth: 560, alignment: .leading)
                Spacer(minLength: 40)
            }
        }

        var fromMe: some View {
            HStack(alignment: .top, spacing: 10) {
                Spacer(minLength: 80)
                VStack(alignment: .trailing, spacing: 4) {
                    Text(message.text)
                        .selectableText()
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(
                            UnevenRoundedRectangle(
                                topLeadingRadius: 14, bottomLeadingRadius: 14, bottomTrailingRadius: 14,
                                topTrailingRadius: 4
                            )
                            .fill(Palette.bubble))
                    Text(time).font(.caption2).foregroundStyle(.tertiary)
                }
                Image(systemName: "person.crop.circle.fill")
                    .resizable()
                    .frame(width: 32, height: 32)
                    .foregroundStyle(.secondary)
            }
        }

        func copy(_ text: String) {
            #if os(macOS)
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            #else
                UIPasteboard.general.string = text
            #endif
        }
    }

    /// The Bot is working (one of its jobs is running): three dots bouncing in a bubble.
    struct TypingBubble: View {
        let bot: Bot
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            HStack(alignment: .center, spacing: 10) {
                BotAvatar(bot: bot, live: true)
                    .frame(width: 32, height: 32)
                TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { context in
                    let time = context.date.timeIntervalSinceReferenceDate
                    HStack(spacing: 5) {
                        ForEach(0..<3, id: \.self) { index in
                            let wave = reduceMotion ? 0 : max(0, sin(time * 2 * .pi - Double(index) * 0.7))
                            Circle()
                                .fill(bot.accent.opacity(0.5 + 0.5 * wave))
                                .frame(width: 7, height: 7)
                                .offset(y: -4 * wave)
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 13)
                    .background(
                        UnevenRoundedRectangle(
                            topLeadingRadius: 4, bottomLeadingRadius: 14, bottomTrailingRadius: 14,
                            topTrailingRadius: 14
                        )
                        .fill(bot.accent.opacity(0.08)))
                }
                Text("\(bot.name) 正在忙…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
            }
        }
    }

#endif
