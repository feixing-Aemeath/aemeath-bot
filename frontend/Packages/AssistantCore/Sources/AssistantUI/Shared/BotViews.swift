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

        /// The accent for small text: darker on the light page, lighter on the dark one, so a
        /// tag or a section name in it stays readable (the plain accent was about 2.4:1).
        var accentText: Color {
            let black = MascotColor(red: 0, green: 0, blue: 0)
            let white = MascotColor(red: 1, green: 1, blue: 1)
            return Color(light: look.accent.mixed(with: black, 0.28).rgb, dark: look.accent.mixed(with: white, 0.3).rgb)
        }

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

    extension MascotColor {
        /// 0xRRGGBB, for `Color(light:dark:)`.
        var rgb: UInt32 {
            func byte(_ value: Double) -> UInt32 { UInt32((min(max(value, 0), 1) * 255).rounded()) }
            return byte(red) << 16 | byte(green) << 8 | byte(blue)
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
                    if let color = state?.dotColor {
                        Circle()
                            .fill(color)
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

        /// The face in the iPhone header's circle, for a `MascotView` that plays character
        /// animations: the head fills the circle, its cut-off bottom at the circle's bottom, and
        /// its hair may rise past the top.
        static func hero(_ name: String, side: CGFloat) -> Place {
            if let eyes = stagedEyes(name) ?? StickerBounds.eyes(of: name) {
                return place(eyes, side: side, span: 0.32, at: CGPoint(x: 0.5, y: 0.74))
            }
            return avatar(name, side: side)
        }

        /// The character peeking up from the bottom of the Mac header's band, for a `MascotView`
        /// that plays character animations: the eyes span 30 % of the box, low in it.
        static func band(_ name: String, side: CGFloat) -> Place {
            guard let eyes = stagedEyes(name) ?? StickerBounds.eyes(of: name) else {
                return Place(drawn: side, center: CGPoint(x: side / 2, y: side / 2))
            }
            return place(eyes, side: side, span: 0.30, at: CGPoint(x: 0.5, y: 0.8))
        }

        /// Where the eyes are in a `MascotView` that plays character animations: those draw the
        /// sticker smaller on a stage of their own (MascotStagePlacement), with room for props,
        /// so the sticker's own eye rectangle is not where they end up. Nil for a sticker
        /// without animations or eyes, which is drawn as it is.
        static func stagedEyes(_ name: String) -> CGRect? {
            guard !MascotAnimation.all(for: name).isEmpty, let eyes = StickerLayers.shared.eyes(named: name)
            else { return nil }
            let stage = MascotStagePlacement(named: name)
            let middle = stage.point(eyes.midX, eyes.midY)
            let width = eyes.width * stage.scale
            let height = eyes.height * stage.scale
            return CGRect(x: middle.x / 240 - width / 2, y: middle.y / 240 - height / 2, width: width, height: height)
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

    /// 今天, 昨天 or a date above a day's first message: small gray text between two hairlines.
    struct DayDivider: View {
        let text: String

        var body: some View {
            HStack(spacing: 10) {
                Palette.hairline.frame(height: 1)
                Text(text)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
                    .fixedSize()
                Palette.hairline.frame(height: 1)
            }
            .padding(.top, 6)
        }
    }

    /// The chat's text sizes: 16 points on the iPhone, the Mac's 13.
    enum ChatFont {
        #if os(iOS)
            static let body = Font.callout
            static let title = Font.callout.weight(.semibold)
            static let name = Font.footnote.weight(.semibold)
        #else
            static let body = Font.body
            static let title = Font.headline
            static let name = Font.callout.weight(.semibold)
        #endif
    }

    /// One message, as in today's AI apps: the Bot's across the full width without a bubble,
    /// under a line with its face, name and time (left out for a message that follows its own
    /// within a few minutes); mine on the right in a warm gray bubble; a failed run as one
    /// quiet line with a warning sign; the App's notes small in the middle.
    struct MessageView: View {
        @EnvironmentObject private var store: AppStore
        let message: BotMessage
        let bot: Bot
        /// The face, name and time above the message.
        var showsHeader = true

        var time: String { DueText.format(message.date, "HH:mm", store.calendar) }

        var body: some View {
            switch message.author {
            case .bot: message.failed ? AnyView(failure) : AnyView(fromBot)
            case .me: AnyView(fromMe)
            case .app:
                AnyView(
                    Text(message.text)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity))
            }
        }

        var copyText: String { message.title.map { "\($0)\n\(message.text)" } ?? message.text }

        var fromBot: some View {
            VStack(alignment: .leading, spacing: 6) {
                if showsHeader {
                    HStack(spacing: 7) {
                        BotAvatar(bot: bot).frame(width: 22, height: 22)
                        Text(store.displayName(bot)).font(ChatFont.name)
                        Text(time).font(.caption).foregroundStyle(.tertiary).monospacedDigit()
                    }
                    .padding(.bottom, 2)
                }
                if let title = message.title {
                    Text(title).font(ChatFont.title)
                }
                if !message.text.isEmpty {
                    BulletedText(source: bot.isAssistant && message.id.hasPrefix("summary-")
                        ? DashboardEdit.trimBrief(message.text, dropping: ["还不确定"]) : message.text)
                        .font(ChatFont.body)
                        .lineSpacing(2)
                        .tint(bot.accentText)
                }
                if bot.isAssistant && message.id.hasPrefix("summary-"), !store.visibleUnsure.isEmpty {
                    UnsureItems(expanded: true)
                }
                if !message.cards.isEmpty {
                    AttachedCards(cards: message.cards, bot: bot).padding(.top, 4)
                }
                if !message.todos.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(bot.isAssistant ? "今天新建的待办（\(message.todos.count)）" : "提炼出的待办（\(message.todos.count)）")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ActionableTodos(todos: message.todos)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.card))
                    .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Palette.hairline))
                    .padding(.top, 2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .contextMenu {
                Button("拷贝") { copy(copyText) }
            }
        }

        /// A run that went wrong: a warning sign and the reason, small, with its time.
        var failure: some View {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
                Text(message.title.map { "\($0)：\(message.text)" } ?? message.text)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Text(time).font(.caption).foregroundStyle(.tertiary).monospacedDigit()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.orange.opacity(0.08)))
            .contextMenu {
                Button("拷贝") { copy(copyText) }
            }
        }

        var fromMe: some View {
            HStack {
                Spacer(minLength: 56)
                Text(message.text)
                    .font(ChatFont.body)
                    .selectableText()
                    .padding(.horizontal, 14)
                    .padding(.vertical, 9)
                    .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Palette.bubble))
                    .help(time)
                    .contextMenu {
                        Button("拷贝") { copy(message.text) }
                        Text("发送于 \(time)")
                    }
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

    /// The cards a Bot attached to a reply, as they were when it wrote it: under the text, no
    /// wider than a card on the dashboard, with one line under them all (they are taken together):
    /// 「截至 14:05」 and 「看最新」, which opens the Bot's cards beside the chat, left out when none
    /// of them is there (助理 showing 作业's homework). Nothing in them can be ticked: they are
    /// a record.
    struct AttachedCards: View {
        @EnvironmentObject private var store: AppStore
        @AppStorage(CardStyle.storageKey) private var style = CardStyle.plain
        @Environment(\.openBotCards) private var openCards
        let cards: [Card]
        let bot: Bot

        static let maxWidth: CGFloat = 460

        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(cards) { card in
                    CardView(card: card).frame(maxWidth: Self.maxWidth, alignment: .leading)
                }
                footer
            }
            .tint(bot.accent)
            .environment(\.cardStyle, style)
            .environment(\.cardAccentText, bot.accentText)
        }

        /// Whether the Bot's own cards, beside its chat, have this one's plugin.
        func isOwn(_ card: Card) -> Bool { card.plugin.map(bot.pluginIDs.contains) ?? true }

        var footer: some View {
            HStack(spacing: 8) {
                if let taken = cards.compactMap(\.takenAt).max() {
                    Text("截至 \(when(taken))")
                }
                if let openCards, cards.contains(where: isOwn) {
                    Button {
                        openCards()
                    } label: {
                        HStack(spacing: 2) {
                            Text("看最新")
                            Image(systemName: "chevron.right").font(.caption2.weight(.semibold))
                        }
                        .foregroundStyle(bot.accentText)
                    }
                    .buttonStyle(.plain)
                }
            }
            .font(.caption)
            .foregroundStyle(.tertiary)
            .padding(.leading, 4)
        }

        /// 14:05 today, 昨天 14:05, or 11月6日 14:05.
        func when(_ date: Date) -> String {
            let calendar = store.calendar
            let time = DueText.format(date, "HH:mm", calendar)
            if calendar.isDateInToday(date) { return time }
            if calendar.isDateInYesterday(date) { return "昨天 \(time)" }
            return DueText.format(date, "M月d日 HH:mm", calendar)
        }
    }

    private struct OpenBotCardsKey: EnvironmentKey {
        static var defaultValue: (() -> Void)? { nil }
    }

    extension EnvironmentValues {
        /// Opens the cards beside a Bot's chat; set by the Bot's page.
        var openBotCards: (() -> Void)? {
            get { self[OpenBotCardsKey.self] }
            set { self[OpenBotCardsKey.self] = newValue }
        }
    }

    /// The Bot is working (one of its jobs is running): its face and three dots bouncing, no
    /// bubble, like its messages.
    struct TypingBubble: View {
        let bot: Bot
        @EnvironmentObject private var store: AppStore
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            HStack(alignment: .center, spacing: 8) {
                BotAvatar(bot: bot, live: true)
                    .frame(width: 22, height: 22)
                TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { context in
                    let time = context.date.timeIntervalSinceReferenceDate
                    HStack(spacing: 4) {
                        ForEach(0..<3, id: \.self) { index in
                            let wave = reduceMotion ? 0 : max(0, sin(time * 2 * .pi - Double(index) * 0.7))
                            Circle()
                                .fill(bot.accent.opacity(0.45 + 0.55 * wave))
                                .frame(width: 6, height: 6)
                                .offset(y: -3 * wave)
                        }
                    }
                    .padding(.vertical, 6)
                }
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text("正在忙… · " + ManualEntryText.elapsed(since: store.busySince(bot), now: context.date))
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                }
                Spacer()
            }
        }
    }

#endif
