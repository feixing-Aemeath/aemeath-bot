#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    // MARK: Sideways scrolling

    /// Where the page scrolls sideways (仪表盘's 今天一览), on the screen: on the iPhone a sideways
    /// swipe that starts there scrolls it, rather than sliding the page aside to the Bots. Only
    /// read when a swipe starts, so a change redraws nothing.
    final class SidewaysZones {
        var frames: [String: CGRect] = [:]

        func contains(_ point: CGPoint) -> Bool { frames.values.contains { $0.contains(point) } }
    }

    private struct SidewaysZonesKey: EnvironmentKey {
        static let defaultValue: SidewaysZones? = nil
    }

    extension EnvironmentValues {
        var sidewaysZones: SidewaysZones? {
            get { self[SidewaysZonesKey.self] }
            set { self[SidewaysZonesKey.self] = newValue }
        }
    }

    /// Marks a view that scrolls sideways (see `SidewaysZones`); nothing where no one asks.
    struct SidewaysZone: ViewModifier {
        @Environment(\.sidewaysZones) private var zones
        let id: String

        func body(content: Content) -> some View {
            if let zones {
                content
                    .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { zones.frames[id] = $0 }
                    .onDisappear { zones.frames[id] = nil }
            } else {
                content
            }
        }
    }

    // MARK: 一览

    /// Tiles jump to the remaining cards or open all cards. A quiet Bot's chip opens its chat.
    struct GlanceStrip: View {
        @EnvironmentObject private var store: AppStore
        let groups: [DashboardBot]
        let compact: Bool
        let openBot: (String) -> Void
        let jump: (DashboardBot) -> Void
        /// Grows with the text size chosen in Settings.
        @ScaledMetric(relativeTo: .body) private var tileWidth: CGFloat = 150

        var loading: Bool { store.lastRefresh == nil && store.online }

        var body: some View {
            let parts = DashboardEdit.strip(groups.map { ($0.id, $0.glance) })
            let shown = loading ? groups : parts.tiles.compactMap { id in groups.first { $0.id == id } }
            let quiet = groups.filter { !$0.hasPanel && $0.glance == nil }.map(\.bot)
            VStack(alignment: .leading, spacing: 10) {
                if !shown.isEmpty {
                    if compact {
                        ScrollView(.horizontal) {
                            EqualHeightRow(itemWidth: tileWidth, spacing: 10) { tiles(shown) }
                                .padding(.horizontal, 16)
                                .padding(.vertical, 2)
                        }
                        .scrollIndicators(.hidden)
                        .scrollClipDisabled()
                        // Out to the screen's edges, so a tile slides away under them.
                        .padding(.horizontal, -16)
                        .modifier(SidewaysZone(id: "glance"))
                    } else {
                        GlanceGrid(minWidth: 150, maxWidth: 250, spacing: 10) { tiles(shown) }
                    }
                }
                if !loading && !quiet.isEmpty {
                    BotChips(title: "没有新情况", bots: quiet, action: openBot)
                }
            }
        }

        func tiles(_ groups: [DashboardBot]) -> some View {
            ForEach(Array(groups.enumerated()), id: \.element.id) { index, group in
                GlanceTile(bot: group.bot, glance: group.glance) { jump(group) }
                    .modifier(CardMotion(index: index))
            }
        }
    }

    /// A Bot's tile in 今天一览, like a pinned figure in Apple Health: its face and what the figure
    /// is in its color, the figure large, a bar when it has one, one line under it (orange or red
    /// when it needs a look). A Bot without a figure (知识库Bot) says how it is doing instead.
    struct GlanceTile: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let glance: Glance?
        let action: () -> Void

        static let radius: CGFloat = 14

        static var valueFont: Font {
            #if os(iOS)
                .system(.title2, design: .rounded).weight(.semibold)
            #else
                .system(size: 20, weight: .semibold, design: .rounded)
            #endif
        }

        static var labelFont: Font {
            #if os(iOS)
                .footnote.weight(.semibold)
            #else
                .subheadline.weight(.semibold)
            #endif
        }

        static var noteFont: Font {
            #if os(iOS)
                .caption
            #else
                .subheadline
            #endif
        }

        /// Not read from the Mac yet: a gray placeholder rather than 「0 件」.
        var loading: Bool { glance == nil && store.lastRefresh == nil && store.online }

        /// In place of a figure: how the Bot is doing, or why there is nothing yet.
        var fallback: String {
            if loading { return "正在读取" }
            if bot.isAssistant { return store.online ? "还没读到今天的待办" : "连不上 Mac" }
            return store.statusLine(bot)
        }

        var toneColor: Color? {
            switch glance?.tone {
            case .alert: return .red
            case .warn: return .orange
            case nil: return nil
            }
        }

        var body: some View {
            let unread = store.unread(bot)
            let shape = RoundedRectangle(cornerRadius: Self.radius, style: .continuous)
            Button(action: action) {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 6) {
                        BotAvatar(bot: bot, state: store.botState(bot))
                            .frame(width: 22, height: 22)
                            .overlay(alignment: .topTrailing) {
                                if unread > 0 { UnreadDot(count: unread).scaleEffect(0.8).offset(x: 3, y: -3) }
                            }
                        Text(store.displayName(bot))
                            .font(Self.labelFont)
                            .foregroundStyle(bot.accentText)
                            .lineLimit(1)
                    }
                    .padding(.bottom, 1)
                    if let glance {
                        Text(glance.label).font(Self.noteFont).foregroundStyle(.secondary).lineLimit(1)
                        figure(glance)
                    } else {
                        Text(fallback)
                            .font(Self.noteFont)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .redacted(reason: loading ? .placeholder : [])
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(shape.fill(Palette.card).shadow(color: .black.opacity(0.035), radius: 6, y: 2))
                .overlay(shape.strokeBorder(Palette.hairline))
                .contentShape(shape)
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityText)
            .accessibilityHint("跳到\(store.displayName(bot))")
            .accessibilityAddTraits(.isButton)
        }

        @ViewBuilder func figure(_ glance: Glance) -> some View {
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(glance.value)
                    .font(Self.valueFont)
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                if let unit = glance.unit {
                    Text(unit)
                        .font(Self.noteFont.weight(.medium))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .fixedSize()
                }
            }
            if let progress = glance.progress {
                // Quieter than a warning: 成绩's color is a red of its own.
                GlanceBar(fraction: progress, color: toneColor ?? bot.accent.opacity(0.55))
                    .padding(.vertical, 2)
            }
            if let note = glance.note {
                HStack(spacing: 3) {
                    if let symbol = Self.trendSymbol(glance.trend) {
                        Image(systemName: symbol).font(.caption2.weight(.bold))
                    }
                    // Whole where it fits, else its first part rather than 「按学分加…」.
                    ViewThatFits(in: .horizontal) {
                        Text(note).lineLimit(1)
                        Text(glance.shortNote ?? note).lineLimit(1)
                    }
                }
                .font(Self.noteFont)
                .foregroundStyle(toneColor.map { AnyShapeStyle($0) } ?? AnyShapeStyle(.secondary))
            }
        }

        static func trendSymbol(_ trend: CardStat.Trend?) -> String? {
            switch trend {
            case .up: return "arrow.up.right"
            case .down: return "arrow.down.right"
            case .flat, nil: return nil
            }
        }

        var accessibilityText: String {
            guard let glance else { return "\(store.displayName(bot))，\(fallback)" }
            let figure = [glance.value, glance.unit].compactMap { $0 }.joined(separator: " ")
            return [store.displayName(bot), glance.label, figure, glance.note].compactMap { $0 }.joined(separator: "，")
        }
    }

    /// The thin bar under a tile's figure: the share used, in the Bot's color or the warning's.
    struct GlanceBar: View {
        let fraction: Double
        let color: Color

        var body: some View {
            Capsule()
                .fill(Color.secondary.opacity(0.15))
                .overlay(alignment: .leading) {
                    GeometryReader { size in
                        Capsule()
                            .fill(color)
                            .frame(width: fraction > 0 ? max(size.size.width * min(fraction, 1), 4) : 0)
                    }
                }
                .frame(height: 4)
                .accessibilityHidden(true)
        }
    }

    // MARK: A Bot's group

    /// A Bot's group on 仪表盘: one rounded panel with the Bot's header on top (its face, name and
    /// latest line on a wash of its color) and its cards inside as cells, a hairline between them;
    /// the group, not each card, has the border, so the page reads as a few groups rather than a
    /// pile of cards. 「今天」 (the assistant) lays its cells out itself.
    struct DashboardGroup: View {
        @AppStorage(CardStyle.storageKey) private var style = CardStyle.plain
        let bot: Bot
        let compact: Bool
        /// Room for the todos and the weather side by side.
        let wide: Bool
        let minWidth: CGFloat
        let fold: CardFold
        let cards: [Card]
        let openBot: (String) -> Void

        static let radius: CGFloat = 18

        var body: some View {
            let shape = RoundedRectangle(cornerRadius: Self.radius, style: .continuous)
            VStack(spacing: 0) {
                GroupHeader(bot: bot, compact: compact, openBot: openBot)
                if bot.isAssistant {
                    TodayCells(bot: bot, wide: wide, minWidth: minWidth)
                } else {
                    // Wide, the knowledge base opens as a page of its own; on the iPhone in a sheet.
                    BotCards(bot: bot, minWidth: minWidth, wideLayout: !compact, fold: fold, dashboardCards: cards)
                }
            }
            .environment(\.cardChrome, .cell)
            .modifier(BotCardEnvironment(bot: bot))
            .background(Palette.card)
            .clipShape(shape)
            .overlay {
                if style == .soft {
                    shape.strokeBorder(bot.accent.opacity(0.14))
                } else {
                    shape.strokeBorder(Palette.hairline)
                }
            }
            .background(shape.fill(Palette.card).shadow(color: .black.opacity(0.04), radius: 8, y: 2))
        }
    }

    /// The top of a Bot's group: its face (with the blue dot when it has said something new),
    /// its name (「今天」 for the assistant) with what it looks after in a capsule of its color and
    /// when it last spoke; under them the last thing it said, or how it is doing when something
    /// is wrong (the assistant: its Agent's state and latest extraction). 立即提炼 for the
    /// assistant and 「聊天 ›」 to its page on the right. All on a wash of its color, like the top
    /// of its page.
    struct GroupHeader: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let compact: Bool
        let openBot: (String) -> Void

        var titleFont: Font { Typography.serif(compact ? 18 : 19) }

        var body: some View {
            let state = store.botState(bot)
            let unread = store.unread(bot)
            let last = store.messages(bot).last { $0.author == .bot && !$0.preview.isEmpty }
            HStack(alignment: .center, spacing: 12) {
                BotAvatar(bot: bot, state: state)
                    .frame(width: 38, height: 38)
                    .overlay(alignment: .topTrailing) {
                        if unread > 0 { UnreadDot(count: unread).offset(x: 2, y: -2) }
                    }
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .center, spacing: 8) {
                        title(last: bot.isAssistant ? nil : last)
                        Spacer(minLength: 6)
                        if bot.isAssistant { RunNowChip() }
                        chatLink
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        if let color = state.dotColor { StatusDot(color: color) }
                        Text(subtitle(state: state, last: last))
                            .lineLimit(compact ? 2 : 1)
                    }
                    .font(compact ? .footnote : .subheadline)
                    .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(
                LinearGradient(
                    colors: [bot.accent.opacity(0.13), bot.accent.opacity(0.02)], startPoint: .leading,
                    endPoint: .trailing))
        }

        /// The name, its duties in a capsule where there is room, and when it last spoke.
        func title(last: BotMessage?) -> some View {
            let name = Text(bot.isAssistant ? "今天" : store.displayName(bot)).font(titleFont).lineLimit(1)
                .accessibilityAddTraits(.isHeader)
            let time = last.map { BotTimeline.listTime($0.date, now: Date(), calendar: store.calendar) }
            return ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    name
                    if !bot.isAssistant && !compact { tag }
                    if let time { timeText(time) }
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    name
                    if let time { timeText(time) }
                }
                name
            }
        }

        var tag: some View {
            Text(bot.tag)
                .font(.caption.weight(.medium))
                .foregroundStyle(bot.accentText)
                .lineLimit(1)
                .padding(.horizontal, 7)
                .padding(.vertical, 2)
                .background(Capsule().fill(bot.accent.opacity(0.14)))
                .fixedSize()
        }

        func timeText(_ time: String) -> some View {
            Text(time).font(.caption).monospacedDigit().foregroundStyle(.tertiary).fixedSize()
        }

        func subtitle(state: MascotState, last: BotMessage?) -> String {
            if bot.isAssistant { return "\(store.displayName(bot)) · \(store.assistantStatusLine)" }
            if state.dotColor != nil { return store.statusLine(bot) }
            return last?.preview ?? store.statusLine(bot)
        }

        var chatLink: some View {
            Button {
                openBot(bot.id)
            } label: {
                HStack(spacing: 3) {
                    Text("聊天")
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold))
                }
                .font(.subheadline.weight(.medium))
                .foregroundStyle(bot.accentText)
                .padding(.vertical, 6)
                .padding(.leading, 6)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .fixedSize()
            .accessibilityLabel("和\(store.displayName(bot))聊天")
        }
    }

    /// 「今天」's cells: the todos first and widest, what the assistant could not settle under
    /// them, with 天气's today and tomorrow stacked beside them on a wide screen (under them, side
    /// by side, on the iPhone); then what to take and the morning brief last, without the todos
    /// and the weather it would say a second time (`DashboardEdit.todayCards`).
    struct TodayCells: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let wide: Bool
        let minWidth: CGFloat

        var body: some View {
            let todos = store.today?.todos ?? []
            let alerts = (todos + DashboardEdit.later(store.todos, today: todos))
                .filter { $0.sourcePlugin.split(separator: ",").contains("watchdog") }.map(\.title)
            let (tiles, rest) = DashboardGlance.todayOrder(
                DashboardEdit.todayCards(BotCards.cards(of: bot, in: store), systemAlerts: alerts))
            let outing = rest.filter { $0.type == .list && $0.title == "出门提醒" }
            let other = rest.filter { !outing.contains($0) }
            if wide && !tiles.isEmpty {
                SplitRow {
                    TodayTodosCard()
                    VStack(spacing: 0) {
                        CardGrid(minWidth: minWidth, spacing: 0, fillsLastRow: true) { cells(tiles, from: 1) }
                        cells(outing, from: tiles.count + 1)
                    }
                }
            } else {
                TodayTodosCard()
                CardGrid(minWidth: minWidth, spacing: 0, fillsLastRow: true) { cells(tiles + outing, from: 1) }
            }
            if !other.isEmpty {
                CardGrid(minWidth: minWidth, spacing: 0, fillsLastRow: true) {
                    cells(other, from: tiles.count + outing.count + 1)
                }
            }
        }

        func cells(_ cards: [Card], from start: Int) -> some View {
            ForEach(Array(cards.enumerated()), id: \.element.id) { index, card in
                CardView(card: card).modifier(CardMotion(index: start + index))
                    .layoutValue(key: CardTileKey.self, value: card.isTile)
            }
        }
    }

    // MARK: Layouts

    /// Top-aligned todos and weather, with the first column taking `fraction` of the width.
    struct SplitRow: Layout {
        var fraction: CGFloat = 0.58

        func widths(_ total: CGFloat) -> (CGFloat, CGFloat) {
            let first = (total * fraction).rounded()
            return (first, total - first)
        }

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            let width = proposal.width.flatMap { $0.isFinite ? max($0, 0) : nil } ?? 640
            let (left, right) = widths(width)
            let heights = zip(subviews, [left, right]).map { view, side in
                view.sizeThatFits(ProposedViewSize(width: side, height: nil)).height
            }
            return CGSize(width: width, height: heights.max() ?? 0)
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            let (left, right) = widths(bounds.width)
            var x = bounds.minX
            for (view, side) in zip(subviews, [left, right]) {
                view.place(
                    at: CGPoint(x: x, y: bounds.minY), anchor: .topLeading,
                    proposal: ProposedViewSize(width: side, height: nil))
                x += side
            }
        }
    }

    /// Tiles in even rows (CardPacking.balancedColumns), every tile as tall as the tallest and
    /// no wider than `maxWidth`; a short last row starts on the left.
    struct GlanceGrid: Layout {
        var minWidth: CGFloat = 150
        var maxWidth: CGFloat = 250
        var spacing: CGFloat = 10

        func arrange(_ width: CGFloat, _ subviews: Subviews) -> (columns: Int, column: CGFloat, height: CGFloat) {
            let columns = CardPacking.balancedColumns(
                count: subviews.count, width: Double(width), minWidth: Double(minWidth), spacing: Double(spacing))
            let column = min((width - spacing * CGFloat(columns - 1)) / CGFloat(columns), maxWidth)
            let height = subviews.map { $0.sizeThatFits(ProposedViewSize(width: column, height: nil)).height }.max() ?? 0
            return (columns, column, height)
        }

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            let width = proposal.width.flatMap { $0.isFinite ? max($0, 0) : nil } ?? minWidth * 4
            let (columns, _, height) = arrange(width, subviews)
            let rows = (subviews.count + columns - 1) / max(columns, 1)
            return CGSize(width: width, height: CGFloat(rows) * height + spacing * CGFloat(max(rows - 1, 0)))
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            let (columns, column, height) = arrange(bounds.width, subviews)
            for (index, view) in subviews.enumerated() {
                let row = index / columns, slot = index % columns
                view.place(
                    at: CGPoint(
                        x: bounds.minX + CGFloat(slot) * (column + spacing),
                        y: bounds.minY + CGFloat(row) * (height + spacing)),
                    anchor: .topLeading, proposal: ProposedViewSize(width: column, height: height))
            }
        }
    }

    /// Tiles of one width in a row, each as tall as the tallest (in a row that scrolls sideways).
    struct EqualHeightRow: Layout {
        var itemWidth: CGFloat
        var spacing: CGFloat = 10

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            let height = subviews.map { $0.sizeThatFits(ProposedViewSize(width: itemWidth, height: nil)).height }.max() ?? 0
            let count = CGFloat(subviews.count)
            return CGSize(width: count * itemWidth + spacing * max(count - 1, 0), height: height)
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            for (index, view) in subviews.enumerated() {
                view.place(
                    at: CGPoint(x: bounds.minX + CGFloat(index) * (itemWidth + spacing), y: bounds.minY),
                    anchor: .topLeading, proposal: ProposedViewSize(width: itemWidth, height: bounds.height))
            }
        }
    }
#endif
