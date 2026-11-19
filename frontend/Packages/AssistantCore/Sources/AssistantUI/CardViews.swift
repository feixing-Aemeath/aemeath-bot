import AssistantCore
import Charts
import SwiftUI

/// The fonts inside a card, a step under its title (`Typography` on the iPhone and the Mac; the
/// system's own on the watch).
enum CardFont {
    #if os(watchOS)
        static let body = Font.body
        static let note = Font.caption2
        static let headline = Font.headline
        static let stat = Font.title3.weight(.semibold)
        static let section = Font.caption.weight(.semibold)
    #elseif os(iOS)
        static var body: Font { Typography.cardBody }
        static var note: Font { Typography.cardNote }
        static var headline: Font { Typography.cardHeadline }
        static var stat: Font { Typography.statNumber }
        /// A section name in the fixed format (「新待办」): 13 points.
        static let section = Font.footnote.weight(.semibold)
    #else
        static var body: Font { Typography.cardBody }
        static var note: Font { Typography.cardNote }
        static var headline: Font { Typography.cardHeadline }
        static var stat: Font { Typography.statNumber }
        static let section = Font.callout.weight(.semibold)
    #endif
}

/// How a card is framed: on its own (a rounded card with a border), or as a cell of a group
/// that draws the frame around all of them (a Bot's group on 仪表盘).
enum CardChrome {
    case card
    /// Square, filling the place the group gives it, with a hairline on its top and leading
    /// edges: in a grid without gaps these are the lines between the cells, and along the
    /// group's edge they fall on its own border.
    case cell
}

private struct CardChromeKey: EnvironmentKey {
    static let defaultValue = CardChrome.card
}

extension EnvironmentValues {
    var cardChrome: CardChrome {
        get { self[CardChromeKey.self] }
        set { self[CardChromeKey.self] = newValue }
    }
}

/// A card's background and title font. On the Mac and the iPhone: a white card with a hairline
/// border on the warm page and a serif title, like Claude's app, in the chosen `CardStyle`; on
/// the watch the light gray it always had.
struct CardSurface: ViewModifier {
    #if !os(watchOS)
        static var titleFont: Font { Typography.cardTitle }
        /// Inside every card.
        static let padding: CGFloat = 14
        static let radius: CGFloat = 16
        @Environment(\.cardStyle) private var style
        @Environment(\.cardChrome) private var chrome

        func body(content: Content) -> some View {
            let shape = RoundedRectangle(cornerRadius: Self.radius, style: .continuous)
            switch (chrome, style) {
            case (.cell, _):
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .background {
                        ZStack {
                            Palette.card
                            if style == .soft { Rectangle().fill(.tint.opacity(0.05)) }
                        }
                    }
                    .overlay(alignment: .top) { CellLine(soft: style == .soft).frame(height: 1) }
                    .overlay(alignment: .leading) { CellLine(soft: style == .soft).frame(width: 1) }
            case (.card, .plain), (.card, .accent):
                content
                    .background(shape.fill(Palette.card).shadow(color: .black.opacity(0.035), radius: 6, y: 2))
                    .overlay(shape.strokeBorder(Palette.hairline))
            case (.card, .soft):
                content
                    .background {
                        ZStack {
                            shape.fill(Palette.card)
                            shape.fill(.tint.opacity(0.05))
                        }
                    }
                    .overlay(shape.strokeBorder(.tint.opacity(0.14)))
            }
        }
    #else
        static let titleFont = Font.headline
        static let padding: CGFloat = 12
        static let radius: CGFloat = 10

        func body(content: Content) -> some View {
            content.background(RoundedRectangle(cornerRadius: 10).fill(Color.secondary.opacity(0.08)))
        }
    #endif
}

#if !os(watchOS)
    /// The hairline between cells of a group, in the color of the group's border: the page's
    /// hairline, or the Bot's color in the 柔光 style.
    struct CellLine: View {
        var soft = false

        var body: some View {
            if soft {
                Rectangle().fill(.tint.opacity(0.14))
            } else {
                Rectangle().fill(Palette.hairline)
            }
        }
    }
#endif

extension View {
    func cardSurface() -> some View { modifier(CardSurface()) }
}

private struct ToggleTodoKey: EnvironmentKey {
    static var defaultValue: ((Todo) -> Void)? { nil }
}

extension EnvironmentValues {
    /// Ticks a todo shown in a plugin's card; nil where nothing can save it (a sample card).
    var toggleTodo: ((Todo) -> Void)? {
        get { self[ToggleTodoKey.self] }
        set { self[ToggleTodoKey.self] = newValue }
    }
}

/// A todo in a plugin's card (作业Bot's 「还没交」). Its circle looks like the one in 今日待办, so
/// a tap ticks it the same way; it used to do nothing.
struct CardTodoRow: View {
    @Environment(\.toggleTodo) private var toggle
    let todo: Todo

    var body: some View {
        if let toggle {
            Button { toggle(todo) } label: {
                TodoRow(todo: todo).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(todo.status == .completed ? "标记为未完成" : "标记为完成")
        } else {
            TodoRow(todo: todo)
        }
    }
}

/// Only dashboard cells inherit a shortened, read-only list and a route to the full cards.
struct DashboardPreview {
    let compact: Bool
    let openAll: () -> Void
}

private struct DashboardPreviewKey: EnvironmentKey {
    static let defaultValue: DashboardPreview? = nil
}

extension EnvironmentValues {
    var dashboardPreview: DashboardPreview? {
        get { self[DashboardPreviewKey.self] }
        set { self[DashboardPreviewKey.self] = newValue }
    }
}

struct DashboardRows<Item, Rows: View>: View {
    @Environment(\.dashboardPreview) private var preview
    let items: [Item]
    @ViewBuilder var rows: ([Item]) -> Rows

    var body: some View {
        if let preview {
            let page = DashboardEdit.preview(items, compact: preview.compact)
            rows(page.items)
            if page.remaining > 0 {
                Button("还有 \(page.remaining) 项", action: preview.openAll)
                    .font(CardFont.note)
                    .buttonStyle(.borderless)
                    .accessibilityHint("打开全部卡片")
            }
        } else {
            rows(items)
        }
    }
}

/// One server-driven card (`{type, title, data}`), drawn by its type. Unknown types and broken
/// data show a short note instead of breaking the screen.
public struct CardView: View {
    let card: Card
    /// A long text card shows its first lines until opened.
    @State private var expanded = false
    @Environment(\.cardChrome) private var chrome

    /// Lines a text card shows before 「展开全文」, and how many it must have to be cut.
    static let clampedLines = 10
    static let clampFrom = 14

    public init(card: Card) {
        self.card = card
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            CardTitle(card.title, icon: card.icon)
                // Clear of the picture in the corner.
                .padding(.trailing, card.symbol == nil ? 0 : 56)
            content
        }
        .padding(CardSurface.padding)
        // As tall as the grid row makes it (CardGrid); its own height wherever none is given.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(alignment: .topTrailing) {
            if let symbol = card.symbol {
                CardIllustration(symbol: symbol).padding(8)
            }
        }
        .background {
            // The page's big number: a wash of the Bot's color fading from the top corner (square
            // in a group's cell, which the group rounds where it has a corner).
            if card.isWide {
                RoundedRectangle(cornerRadius: chrome == .cell ? 0 : CardSurface.radius, style: .continuous)
                    .fill(.tint.opacity(0.14))
                    .mask(LinearGradient(colors: [.black, .clear], startPoint: .topLeading, endPoint: .bottomTrailing))
            }
        }
        .cardSurface()
    }

    @ViewBuilder var content: some View {
        switch card.content {
        case .text(let text):
            VStack(alignment: .leading, spacing: 4) {
                if let headline = text.headline {
                    Text(headline)
                        .font(CardFont.headline)
                        .padding(.trailing, card.symbol == nil ? 0 : 56)
                }
                if let table = text.table {
                    TableView(table: table)
                    if let footnote = text.footnote {
                        Text(footnote).font(.caption).foregroundStyle(.secondary).padding(.top, 4)
                    }
                } else {
                    let lines = text.text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline)
                    let long = lines.filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }.count >= Self.clampFrom
                    BulletedText(
                        source: long && !expanded ? lines.prefix(Self.clampedLines).joined(separator: "\n") : text.text
                    )
                    .font(CardFont.body)
                    if long {
                        Button(expanded ? "收起" : "展开全文") {
                            withAnimation(.snappy) { expanded.toggle() }
                        }
                        .buttonStyle(.borderless)
                        .font(CardFont.note.weight(.medium))
                        .padding(.top, 2)
                    }
                }
            }
        case .stat(let stat):
            StatView(stat: stat)
        case .list(let items):
            DashboardRows(items: items) { shown in
                if card.listStyle == .timeline {
                    TimelineList(items: shown)
                } else if card.listStyle == .rings && items.allSatisfy({ $0.progress != nil }) {
                    RingsList(items: shown)
                } else {
                    plainList(shown)
                }
            }
        case .chart(let chart):
            ChartCardView(chart: chart)
        case .photoGrid(let photos):
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 72), spacing: 8)], spacing: 8) {
                ForEach(photos, id: \.id) { photo in
                    VStack(spacing: 2) {
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color.secondary.opacity(0.2))
                            .aspectRatio(1, contentMode: .fit)
                            .overlay(Image(systemName: "photo").foregroundStyle(.secondary))
                        if let caption = photo.caption {
                            Text(caption).font(.caption2).lineLimit(1)
                        }
                    }
                }
            }
        case .todoList(let todos):
            DashboardRows(items: todos) { shown in
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(shown) { todo in CardTodoRow(todo: todo) }
                }
            }
        case .unsupported(let type, _):
            Text("这张卡片（\(type)）需要更新 App 才能显示")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    func plainList(_ items: [CardListItem]) -> some View {
        VStack(alignment: .leading, spacing: items.contains { $0.progress != nil } ? 12 : 8) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: 5) {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.title).font(CardFont.body.weight(.medium))
                            if let subtitle = item.subtitle {
                                Text(subtitle).font(CardFont.note).foregroundStyle(.secondary)
                            }
                        }
                        Spacer(minLength: 8)
                        // The number the row is about (a score, a deadline, a share): as
                        // dark as the title, not hidden in gray. A state in words (审批中,
                        // 还没报名) is a quiet label instead: bold and black it read as a
                        // second title on every row.
                        if let detail = item.detail {
                            if item.detailIsFigure {
                                Text(detail).font(CardFont.body.weight(.semibold)).monospacedDigit()
                            } else {
                                Text(detail)
                                    .font(CardFont.note.weight(.medium))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .padding(.horizontal, 7)
                                    .padding(.vertical, 2)
                                    .background(Capsule().fill(Color.secondary.opacity(0.12)))
                                    .fixedSize()
                            }
                        }
                    }
                    if let progress = item.progress {
                        ProgressBar(fraction: progress.fraction, tone: progress.tone)
                    }
                }
            }
        }
    }
}

/// A table such as a week's classes: the column titles on top (today's in the Bot's color),
/// the first column naming each row, every other cell a small tile, tinted where it has
/// something and more strongly in today's column.
struct TableView: View {
    let table: CardTable

    var body: some View {
        Grid(horizontalSpacing: 3, verticalSpacing: 3) {
            GridRow {
                ForEach(Array(table.columns.enumerated()), id: \.offset) { index, title in
                    Text(title)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(index == table.highlight ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                        .frame(maxWidth: index == 0 ? nil : .infinity)
                }
            }
            ForEach(Array(table.rows.enumerated()), id: \.offset) { _, row in
                GridRow {
                    ForEach(Array(row.enumerated()), id: \.offset) { index, value in
                        if index == 0 {
                            Text(value)
                                .font(.caption2)
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                                .fixedSize()
                        } else {
                            cell(value, today: index == table.highlight)
                        }
                    }
                }
            }
        }
    }

    func cell(_ value: String, today: Bool) -> some View {
        let fill: AnyShapeStyle =
            value.isEmpty
            ? AnyShapeStyle(Color.secondary.opacity(today ? 0.1 : 0.05))
            : AnyShapeStyle(.tint.opacity(today ? 0.24 : 0.12))
        return Text(value)
            .font(.caption2)
            .multilineTextAlignment(.center)
            .lineLimit(4)
            .minimumScaleFactor(0.85)
            // As many lines as the name needs; the row grows to the tallest cell.
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 2)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, minHeight: 40)
            .background(RoundedRectangle(cornerRadius: 6).fill(fill))
    }
}

/// A list row's bar: the full mark in gray, the score so far in the Bot's color laid over it,
/// or in orange or red when the backend says it is getting high (a usage limit nearly used).
struct ProgressBar: View {
    let fraction: Double
    var tone: CardListItem.Progress.Tone? = nil

    var fill: AnyShapeStyle {
        switch tone {
        case .warn: return AnyShapeStyle(Color.orange)
        case .alert: return AnyShapeStyle(Color.red)
        case nil: return AnyShapeStyle(.tint)
        }
    }

    var body: some View {
        Capsule()
            .fill(Color.secondary.opacity(0.18))
            .overlay(alignment: .leading) {
                GeometryReader { size in
                    Capsule()
                        .fill(fill)
                        .frame(width: fraction > 0 ? max(size.size.width * fraction, 6) : 0)
                }
            }
            .frame(height: 6)
            .accessibilityElement()
            .accessibilityValue(Text("\(Int((fraction * 100).rounded()))%"))
    }
}

/// A big number with its unit, label and change.
struct StatView: View {
    let stat: CardStat
    @Environment(\.cardAccentText) private var accentText

    var trendSymbol: String? {
        switch stat.trend {
        case .up: return "arrow.up.right"
        case .down: return "arrow.down.right"
        case .flat: return "arrow.right"
        case nil: return nil
        }
    }

    var body: some View {
        if stat.wide { wide } else { tile }
    }

    /// The page's big number: large and rounded in the Bot's color (red when the bar says it is
    /// over), the bar under it, then the label.
    var wide: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(stat.value)
                    .font(.system(size: 44, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(stat.bar?.tone == .alert ? AnyShapeStyle(Color.red) : AnyShapeStyle(accentText))
                    .minimumScaleFactor(0.6)
                    .lineLimit(1)
                if let unit = stat.unit {
                    Text(unit).font(.title3.weight(.medium)).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                change
            }
            if let bar = stat.bar {
                SegmentBar(bar: bar)
            }
            if let label = stat.label {
                Text(label).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    var tile: some View {
        VStack(alignment: .leading, spacing: 2) {
            // On one line where it fits; in a narrow tile the change goes under the number.
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    number
                    change
                }
                VStack(alignment: .leading, spacing: 2) {
                    number
                    HStack(spacing: 4) { change }
                }
            }
            if let bar = stat.bar {
                SegmentBar(bar: bar, height: 6).padding(.vertical, 4)
            }
            if let label = stat.label {
                Text(label).font(CardFont.note).foregroundStyle(.secondary).lineLimit(4)
            }
        }
    }

    var number: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(stat.value).font(CardFont.stat).monospacedDigit().lineLimit(1).minimumScaleFactor(0.6)
            if let unit = stat.unit {
                Text(unit).font(CardFont.body.weight(.medium)).foregroundStyle(.secondary)
            }
        }
    }

    /// The change since before, as a small gray capsule with its arrow.
    @ViewBuilder var change: some View {
        if trendSymbol != nil || stat.delta != nil {
            HStack(spacing: 2) {
                if let symbol = trendSymbol {
                    Image(systemName: symbol).font(.caption2.weight(.bold))
                }
                if let delta = stat.delta {
                    Text(delta).lineLimit(1)
                }
            }
            .font(.caption.weight(.medium))
            .monospacedDigit()
            .foregroundStyle(.secondary)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .background(Capsule().fill(Color.secondary.opacity(0.1)))
        }
    }
}

/// A capsule for `max`, filled from the left by the segments in a donut's colors, with a hair
/// of the card between them; past `max` it is full. A warning tone tints the empty part.
struct SegmentBar: View {
    let bar: CardStat.Bar
    var height: CGFloat = 12

    var track: Color {
        switch bar.tone {
        case .warn: return .orange.opacity(0.22)
        case .alert: return .red.opacity(0.22)
        case nil: return .secondary.opacity(0.15)
        }
    }

    var body: some View {
        // Work in units of the largest value so both the sum and the geometry stay finite.
        let scale = max(bar.max, bar.segments.map(\.y).max() ?? 0, .leastNonzeroMagnitude)
        let parts = bar.segments.map { max($0.y, 0) / scale }
        let filled = parts.reduce(0, +)
        let capacity = max(0, bar.max) / scale
        let total = max(capacity, filled, .leastNonzeroMagnitude)
        GeometryReader { size in
            HStack(spacing: 2) {
                ForEach(Array(parts.enumerated()), id: \.offset) { index, value in
                    Rectangle()
                        .fill(DonutChartView.color(index))
                        .frame(width: max(size.size.width * value / total - 2, 2))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .background(track)
            .clipShape(Capsule())
        }
        .frame(height: height)
        .accessibilityElement()
        .accessibilityValue(Text((capacity > 0 ? filled / capacity : 0).formatted(.percent.precision(.fractionLength(0)))))
    }
}

/// The colors of charts with several series and of donuts: muted to sit on the warm paper
/// (Swift Charts' defaults were loud and looked like a template), a little lighter in dark mode;
/// no red, which is kept for warnings. Sleep stages have their own, close to Apple Health's.
enum ChartPalette {
    static let series: [Color] = [
        tone(0xD97757, 0xE58C6E),  // clay
        tone(0x7FA77A, 0x93BB8E),  // sage
        tone(0x5B7DB1, 0x7996C6),  // slate
        tone(0x9A6FB0, 0xB08BC4),  // plum
        tone(0xC9A15B, 0xD8B577),  // sand
        tone(0x4E9C9A, 0x67B3B1),  // teal
        tone(0xB97A8E, 0xCB94A6),  // rose
        tone(0x7A7FB8, 0x9398CB),  // indigo
    ]

    static let stages: [String: Color] = [
        "深睡": tone(0x3B4199, 0x6268CC),
        "核心": tone(0x5B7FD6, 0x7C9AE3),
        "REM": tone(0x6FB8E8, 0x8CC8EE),
        "清醒": tone(0xEE9455, 0xF2A874),
        "小睡": tone(0xB3AE9F, 0x8F8B7F),
    ]

    static func color(_ index: Int) -> Color { series[index % series.count] }

    /// A series' color: its sleep stage's, or the next in the palette.
    static func colors(for names: [String]) -> [Color] {
        names.enumerated().map { index, name in stages[name] ?? color(index) }
    }

    static func tone(_ light: UInt32, _ dark: UInt32) -> Color {
        #if os(watchOS)
            Color(red: Double(dark >> 16 & 0xFF) / 255, green: Double(dark >> 8 & 0xFF) / 255, blue: Double(dark & 0xFF) / 255)
        #else
            Color(light: light, dark: dark)
        #endif
    }
}

/// Line or bar chart of one or more series, or a donut of one series' slices.
struct ChartCardView: View {
    /// A bar is a large patch of the Bot's color: at full strength a red like 成绩's shouted over
    /// the page's quiet paper, so bars are drawn a step lighter than lines and rings.
    static let barOpacity = 0.62

    let chart: CardChart

    static var chartHeight: CGFloat {
        #if os(iOS)
            130
        #else
            140
        #endif
    }

    /// Some bar of the one series goes past the target.
    var overTarget: Bool {
        guard let target = chart.target, chart.kind == .bar, chart.series.count == 1 else { return false }
        return chart.series[0].points.contains { $0.y > target.value }
    }

    var body: some View {
        if chart.kind == .donut, let slices = chart.series.first?.points {
            DonutChartView(chart: chart, slices: slices)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                lines
                if let label = chart.target?.label {
                    legend(label)
                }
            }
        }
    }

    /// What the dashed line is, and the orange part when a bar goes past it.
    func legend(_ label: String) -> some View {
        HStack(spacing: 14) {
            HStack(spacing: 5) {
                Path { path in
                    path.move(to: CGPoint(x: 0, y: 0.5))
                    path.addLine(to: CGPoint(x: 16, y: 0.5))
                }
                .stroke(Color.secondary, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
                .frame(width: 16, height: 1)
                Text(label)
            }
            if overTarget {
                HStack(spacing: 5) {
                    RoundedRectangle(cornerRadius: 2).fill(Color.orange).frame(width: 10, height: 10)
                    Text("超出的部分")
                }
            }
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
    }

    var lines: some View {
        Chart {
            ForEach(chart.series, id: \.name) { series in
                ForEach(series.points, id: \.self) { point in
                    if chart.kind == .bar && chart.series.count == 1, let target = chart.target,
                        point.y > target.value
                    {
                        // Up to the target in the Bot's color, what goes past it in orange.
                        BarMark(
                            x: .value("日期", point.x), yStart: .value("数值", 0),
                            yEnd: .value("数值", target.value)
                        )
                        .foregroundStyle(.tint.opacity(Self.barOpacity))
                        BarMark(
                            x: .value("日期", point.x), yStart: .value("数值", target.value),
                            yEnd: .value("数值", point.y)
                        )
                        .foregroundStyle(Color.orange)
                        .cornerRadius(4)
                        .annotation(position: .top, spacing: 2) { barLabel(point) }
                    } else if chart.kind == .bar && chart.series.count == 1 {
                        BarMark(x: .value("日期", point.x), y: .value("数值", point.y))
                            .foregroundStyle(.tint.opacity(Self.barOpacity))
                            .cornerRadius(4)
                            .annotation(position: .top, spacing: 2) { barLabel(point) }
                    } else if chart.kind == .bar {
                        BarMark(x: .value("日期", point.x), y: .value("数值", point.y))
                            .foregroundStyle(by: .value("系列", series.name))
                            .cornerRadius(3)
                    } else if chart.series.count == 1 {
                        // One line: a soft fill of the Bot's color under it, its last value marked.
                        if chart.zero {
                            AreaMark(x: .value("日期", point.x), y: .value("数值", point.y))
                                .foregroundStyle(.tint.opacity(0.12))
                        }
                        LineMark(x: .value("日期", point.x), y: .value("数值", point.y))
                            .foregroundStyle(.tint)
                            .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        if point == series.points.last {
                            PointMark(x: .value("日期", point.x), y: .value("数值", point.y))
                                .foregroundStyle(.tint)
                                .symbolSize(30)
                                .annotation(position: .top, spacing: 4) {
                                    Text(point.label ?? Self.short(point.y))
                                        .font(.caption2.weight(.semibold))
                                        .monospacedDigit()
                                        .foregroundStyle(.secondary)
                                }
                        }
                    } else {
                        LineMark(x: .value("日期", point.x), y: .value("数值", point.y))
                            .foregroundStyle(by: .value("系列", series.name))
                            .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    }
                }
            }
            if let target = chart.target {
                RuleMark(y: .value("目标", target.value))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                    .foregroundStyle(Color.secondary)
            }
        }
        // One series in the Bot's color; several in the palette (sleep stages in theirs), told
        // apart by the legend.
        .chartForegroundStyleScale(
            domain: chart.series.map(\.name),
            range: chart.series.count == 1
                ? [AnyShapeStyle(.tint)] : ChartPalette.colors(for: chart.series.map(\.name)).map { AnyShapeStyle($0) }
        )
        .chartLegend(chart.series.count > 1 ? .visible : .hidden)
        .chartYScale(domain: .automatic(includesZero: chart.zero ? nil : false))
        // Only faint horizontal lines behind the marks.
        .chartYAxis {
            AxisMarks { _ in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5)).foregroundStyle(Color.secondary.opacity(0.3))
                AxisValueLabel()
            }
        }
        // A label under every bar of a week; a long chart (30 nights) labels about seven days,
        // the newest set to the left of its tick so the edge does not cut it to 「…」.
        // A mark for every day with a label on the chosen ones: on the iPhone a mark for the chosen
        // ones only still labeled all 30.
        .chartXAxis {
            let all = chart.categories
            let labelled = Set(chart.axisLabels())
            AxisMarks(values: all) { value in
                if labelled.count == all.count {
                    AxisValueLabel()
                } else if value.index == value.count - 1 {
                    AxisValueLabel(anchor: .topTrailing, collisionResolution: .disabled)
                } else if let day = value.as(String.self), labelled.contains(day) {
                    AxisValueLabel(collisionResolution: .disabled)
                }
            }
        }
        // Above the axis, not over the chart, where it covered the top tick ("10" under "小时").
        .chartYAxisLabel(position: .top, alignment: .trailing) {
            if let unit = chart.unit {
                Text(unit)
            }
        }
        .frame(height: chart.target == nil ? Self.chartHeight : Self.chartHeight + 20)
    }

    /// 1234 as 「1,234」, 2.5 as 「2.5」.
    static func short(_ value: Double) -> String {
        value.rounded() == value ? value.formatted(.number.precision(.fractionLength(0))) : value.formatted(.number.precision(.fractionLength(1)))
    }

    /// A bar's number over it, when the backend gives one.
    @ViewBuilder func barLabel(_ point: CardChart.Point) -> some View {
        if let label = point.label {
            Text(label).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
        }
    }
}

/// Markdown in the Bots' fixed format, in cards and in the chat: a section name on a line of its
/// own (「**新待办**」) small and in the Bot's color with room above it, "- " lines as items (a
/// dot in the Bot's color, the text indented under it so a long item wraps clear of the dot),
/// numbered items the same way. Plain text stays one `MarkdownText`, selectable at once.
struct BulletedText: View {
    let source: String

    var body: some View {
        let lines = RichLine.lines(source)
        if RichLine.isPlain(lines) {
            MarkdownText(source)
        } else {
            VStack(alignment: .leading, spacing: 5) {
                ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                    switch line {
                    case .section(let title):
                        Text(MarkdownText.inline(title))
                            .font(CardFont.section)
                            .foregroundStyle(.tint)
                            .padding(.top, index == 0 || lines[index - 1] == .blank ? 0 : 6)
                    case .heading(let title):
                        Text(MarkdownText.inline(title)).font(CardFont.headline).padding(.top, index == 0 ? 0 : 4)
                    case .bullet(let text):
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("•").fontWeight(.heavy).foregroundStyle(.tint)
                            MarkdownText(text)
                        }
                    case .numbered(let number, let text):
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(number).monospacedDigit().foregroundStyle(.secondary)
                            MarkdownText(text)
                        }
                    case .table(let table):
                        MarkdownTableView(table: table).padding(.vertical, 4)
                    case .blank:
                        Color.clear.frame(height: 3)
                    case .text(let text):
                        MarkdownText(text)
                    }
                }
            }
        }
    }
}

/// A table in a Bot's message, as in Claude's and ChatGPT's apps: the column names bold, a
/// hairline between rows, the whole in a rounded frame as wide as its columns.
/// Cells wrap when the column is narrow; a table of many columns that does not fit scrolls
/// sideways instead.
struct MarkdownTableView: View {
    let table: MarkdownTable

    var body: some View {
        Group {
            if table.header.count >= 5 {
                ViewThatFits(in: .horizontal) {
                    grid
                    ScrollView(.horizontal) { grid.fixedSize().padding(.bottom, 1) }
                        .scrollIndicators(.hidden)
                }
            } else {
                grid
            }
        }
        .font(Self.font)
    }

    #if os(iOS)
        static let font = Font.subheadline
    #elseif os(macOS)
        static let font = Font.callout
    #else
        static let font = Font.footnote
    #endif

    #if os(watchOS)
        static let line = Color.white.opacity(0.15)
    #else
        static let line = Palette.hairline
    #endif

    var grid: some View {
        Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
            GridRow {
                ForEach(Array(table.header.enumerated()), id: \.offset) { column, name in
                    cell(name, column).fontWeight(.semibold)
                }
            }
            ForEach(Array(table.rows.enumerated()), id: \.offset) { _, row in
                Self.line.frame(height: 1).gridCellUnsizedAxes(.horizontal)
                GridRow {
                    ForEach(Array(row.enumerated()), id: \.offset) { column, text in
                        cell(text, column)
                    }
                }
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Self.line))
    }

    func cell(_ text: String, _ column: Int) -> some View {
        let alignment = column < table.alignments.count ? table.alignments[column] : .leading
        return Text(MarkdownText.inline(text))
            .monospacedDigit()
            .multilineTextAlignment(alignment == .trailing ? .trailing : alignment == .center ? .center : .leading)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .gridColumnAlignment(Self.columnAlignment(alignment))
    }

    static func columnAlignment(_ alignment: MarkdownTable.Alignment) -> HorizontalAlignment {
        switch alignment {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }
}

/// A ring of slices on the left with the total and its unit in the middle; each slice's name,
/// number and share on the right.
struct DonutChartView: View {
    let chart: CardChart
    let slices: [CardChart.Point]

    /// Clay first: the first slice is Claude on the token card.
    static func color(_ index: Int) -> Color { ChartPalette.color(index) }

    private let scale: Double
    private let total: Double

    init(chart: CardChart, slices: [CardChart.Point]) {
        self.chart = chart
        self.slices = slices
        let scale = max(slices.map(\.y).max() ?? 0, .leastNonzeroMagnitude)
        self.scale = scale
        total = slices.reduce(0) { $0 + max($1.y, 0) / scale }
    }

    func share(_ value: Double) -> String {
        guard total > 0 else { return "0%" }
        let percent = max(value, 0) / scale / total * 100
        return percent > 0 && percent < 1 ? "<1%" : "\(Int(percent.rounded()))%"
    }

    var body: some View {
        // Side by side where the legend fits beside the ring; in a narrow card the legend goes
        // under it rather than squeezing its numbers onto two lines.
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 16) {
                ring
                legend.frame(minWidth: 180)
            }
            VStack(alignment: .leading, spacing: 12) {
                ring.frame(maxWidth: .infinity)
                legend
            }
        }
    }

    var ring: some View {
        ZStack {
            Chart {
                ForEach(Array(slices.enumerated()), id: \.offset) { index, slice in
                    SectorMark(
                        angle: .value("数值", max(slice.y, 0) / scale), innerRadius: .ratio(0.64),
                        angularInset: 1.5
                    )
                    .cornerRadius(3)
                    .foregroundStyle(Self.color(index))
                }
            }
            .chartLegend(.hidden)
            if let value = chart.value {
                VStack(spacing: 0) {
                    Text(value)
                        .font(.system(.title3, design: .rounded).weight(.bold))
                        .monospacedDigit()
                        .minimumScaleFactor(0.6)
                        .lineLimit(1)
                    if let unit = chart.unit {
                        Text(unit).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .frame(width: 74)
            }
        }
        .frame(width: 120, height: 120)
    }

    var legend: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(Array(slices.enumerated()), id: \.offset) { index, slice in
                HStack(spacing: 6) {
                    RoundedRectangle(cornerRadius: 2).fill(Self.color(index)).frame(width: 8, height: 8)
                    Text(slice.x).font(.caption).lineLimit(1)
                    Spacer(minLength: 4)
                    if let label = slice.label {
                        Text(label).font(.caption.weight(.medium)).monospacedDigit().lineLimit(1).fixedSize()
                    }
                    Text(share(slice.y))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                        .frame(minWidth: 28, alignment: .trailing)
                }
            }
            if let label = chart.label {
                Text(label).font(.caption2).foregroundStyle(.secondary).padding(.top, 2)
            }
        }
    }
}

/// Markdown with line breaks kept: "###" headings in a heading font, inline syntax on every
/// line, lists as plain lines. One Text, so a whole message can be selected at once.
public struct MarkdownText: View {
    let source: String

    public init(_ source: String) {
        self.source = source
    }

    var attributed: AttributedString {
        var result = AttributedString()
        for (index, line) in MarkdownLine.lines(source).enumerated() {
            if index > 0 { result.append(AttributedString("\n")) }
            switch line {
            case .heading(let title):
                var heading = Self.inline(title)
                heading.font = .headline
                result.append(heading)
            case .text(let text):
                result.append(Self.inline(text))
            }
        }
        return result
    }

    static func inline(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }

    public var body: some View {
        Text(attributed)
            .selectableText()
            .fixedSize(horizontal: false, vertical: true)
    }
}

extension View {
    /// Text selection where the platform has it (not on the watch).
    @ViewBuilder func selectableText() -> some View {
        #if os(watchOS)
            self
        #else
            self.textSelection(.enabled)
        #endif
    }
}
