import AssistantCore
import Charts
import SwiftUI

/// A card's background and title font. On the Mac and the iPhone: a white card with a hairline
/// border on the warm page and a serif title, like Claude's app, in the chosen `CardStyle`; on
/// the watch the light gray it always had.
struct CardSurface: ViewModifier {
    #if !os(watchOS)
        static var titleFont: Font { Typography.cardTitle }
        @Environment(\.cardStyle) private var style

        func body(content: Content) -> some View {
            let shape = RoundedRectangle(cornerRadius: 12)
            switch style {
            case .plain:
                content
                    .background(shape.fill(Palette.card))
                    .overlay(shape.strokeBorder(Palette.hairline))
            case .accent:
                content
                    .background {
                        // The strip inside the card's rounded corners.
                        shape.fill(Palette.card)
                            .overlay(alignment: .top) { Rectangle().fill(.tint).frame(height: 3) }
                            .clipShape(shape)
                    }
                    .overlay(shape.strokeBorder(Palette.hairline))
            case .soft:
                content
                    .background {
                        ZStack {
                            shape.fill(Palette.card)
                            shape.fill(.tint.opacity(0.08))
                        }
                    }
                    .overlay(shape.strokeBorder(.tint.opacity(0.22)))
            }
        }
    #else
        static let titleFont = Font.headline

        func body(content: Content) -> some View {
            content.background(RoundedRectangle(cornerRadius: 10).fill(Color.secondary.opacity(0.08)))
        }
    #endif
}

extension View {
    func cardSurface() -> some View { modifier(CardSurface()) }
}

/// One server-driven card (`{type, title, data}`), drawn by its type. Unknown types and broken
/// data show a short note instead of breaking the screen.
public struct CardView: View {
    let card: Card

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
        .padding(12)
        // As tall as the grid row makes it (CardGrid); its own height wherever none is given.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(alignment: .topTrailing) {
            if let symbol = card.symbol {
                CardIllustration(symbol: symbol).padding(8)
            }
        }
        .background {
            // The page's big number: a wash of the Bot's color fading from the top corner.
            if card.isWide {
                RoundedRectangle(cornerRadius: 12)
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
                        .font(.headline)
                        .padding(.trailing, card.symbol == nil ? 0 : 56)
                }
                if let table = text.table {
                    TableView(table: table)
                    if let footnote = text.footnote {
                        Text(footnote).font(.caption).foregroundStyle(.secondary).padding(.top, 4)
                    }
                } else {
                    BulletedText(source: text.text)
                }
            }
        case .stat(let stat):
            StatView(stat: stat)
        case .list(let items) where card.listStyle == .timeline:
            TimelineList(items: items)
        case .list(let items) where card.listStyle == .rings && items.allSatisfy { $0.progress != nil }:
            RingsList(items: items)
        case .list(let items):
            VStack(alignment: .leading, spacing: items.contains { $0.progress != nil } ? 12 : 6) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(alignment: .firstTextBaseline) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(item.title)
                                if let subtitle = item.subtitle {
                                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            if let detail = item.detail {
                                Text(detail).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                            }
                        }
                        if let progress = item.progress {
                            ProgressBar(fraction: progress.fraction, tone: progress.tone)
                        }
                    }
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
            VStack(alignment: .leading, spacing: 4) {
                ForEach(todos) { todo in
                    TodoRow(todo: todo)
                }
            }
        case .unsupported(let type, _):
            Text("这张卡片（\(type)）需要更新 App 才能显示")
                .font(.caption)
                .foregroundStyle(.secondary)
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
                    .foregroundStyle(stat.bar?.tone == .alert ? AnyShapeStyle(Color.red) : AnyShapeStyle(.tint))
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
                Text(label).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    var number: some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(stat.value).font(.title2.weight(.semibold))
            if let unit = stat.unit {
                Text(unit).font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder var change: some View {
        if let symbol = trendSymbol {
            Image(systemName: symbol).font(.caption).foregroundStyle(.secondary)
        }
        if let delta = stat.delta {
            Text(delta).font(.caption).foregroundStyle(.secondary)
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
        let parts = bar.segments.map { max($0.y, 0) }
        let total = max(bar.max, parts.reduce(0, +), .leastNonzeroMagnitude)
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
        .accessibilityValue(Text("\(Int((parts.reduce(0, +) / max(bar.max, 1) * 100).rounded()))%"))
    }
}

/// Line or bar chart of one or more series, or a donut of one series' slices.
struct ChartCardView: View {
    let chart: CardChart

    /// Swift Charts' series colors in their order, without red: the fifth series (the naps on
    /// the sleep chart) came out red and read as a warning.
    static let seriesColors: [Color] = [.blue, .green, .orange, .purple, .teal, .pink, .yellow, .indigo]

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
                        .foregroundStyle(.tint)
                        BarMark(
                            x: .value("日期", point.x), yStart: .value("数值", target.value),
                            yEnd: .value("数值", point.y)
                        )
                        .foregroundStyle(Color.orange)
                        .cornerRadius(4)
                        .annotation(position: .top, spacing: 2) { barLabel(point) }
                    } else if chart.kind == .bar && chart.series.count == 1 {
                        BarMark(x: .value("日期", point.x), y: .value("数值", point.y))
                            .foregroundStyle(.tint)
                            .cornerRadius(4)
                            .annotation(position: .top, spacing: 2) { barLabel(point) }
                    } else if chart.kind == .bar {
                        BarMark(x: .value("日期", point.x), y: .value("数值", point.y))
                            .foregroundStyle(by: .value("系列", series.name))
                            .cornerRadius(3)
                    } else {
                        LineMark(x: .value("日期", point.x), y: .value("数值", point.y))
                            .foregroundStyle(by: .value("系列", series.name))
                    }
                }
            }
            if let target = chart.target {
                RuleMark(y: .value("目标", target.value))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                    .foregroundStyle(Color.secondary)
            }
        }
        // One series in the Bot's color; several in the fixed colors, told apart by the legend.
        .chartForegroundStyleScale(range: chart.series.count == 1 ? [AnyShapeStyle(.tint)] : Self.seriesColors.map { AnyShapeStyle($0) })
        .chartLegend(chart.series.count > 1 ? .visible : .hidden)
        // Above the axis, not over the chart, where it covered the top tick ("10" under "小时").
        .chartYAxisLabel(position: .top, alignment: .trailing) {
            if let unit = chart.unit {
                Text(unit)
            }
        }
        .frame(height: chart.target == nil ? 140 : 160)
    }

    /// A bar's number over it, when the backend gives one.
    @ViewBuilder func barLabel(_ point: CardChart.Point) -> some View {
        if let label = point.label {
            Text(label).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
        }
    }
}

/// A text card's Markdown, with "- " lines as items: a dot in the Bot's color and the text
/// indented under it, so a long item wraps clear of the dot. Other lines as `MarkdownText`.
struct BulletedText: View {
    let source: String

    var body: some View {
        let lines = source.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline).map(String.init)
        if lines.contains(where: { $0.hasPrefix("- ") }) {
            VStack(alignment: .leading, spacing: 5) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    if line.hasPrefix("- ") {
                        HStack(alignment: .firstTextBaseline, spacing: 7) {
                            Text("•").fontWeight(.heavy).foregroundStyle(.tint)
                            MarkdownText(String(line.dropFirst(2)))
                        }
                    } else if line.trimmingCharacters(in: .whitespaces).isEmpty {
                        Color.clear.frame(height: 2)
                    } else {
                        MarkdownText(line)
                    }
                }
            }
        } else {
            MarkdownText(source)
        }
    }
}

/// A ring of slices on the left with the total and its unit in the middle; each slice's name,
/// number and share on the right.
struct DonutChartView: View {
    let chart: CardChart
    let slices: [CardChart.Point]

    /// Orange first: the first slice is Claude on the token card.
    static let colors: [Color] = [.orange, .green, .blue, .purple, .teal, .pink, .yellow, .indigo]

    static func color(_ index: Int) -> Color { colors[index % colors.count] }

    var total: Double { slices.reduce(0) { $0 + max($1.y, 0) } }

    func share(_ value: Double) -> String {
        guard total > 0 else { return "0%" }
        let percent = value / total * 100
        return percent > 0 && percent < 1 ? "<1%" : "\(Int(percent.rounded()))%"
    }

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            ZStack {
                Chart {
                    ForEach(Array(slices.enumerated()), id: \.offset) { index, slice in
                        SectorMark(
                            angle: .value("数值", max(slice.y, 0)), innerRadius: .ratio(0.64),
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
            VStack(alignment: .leading, spacing: 7) {
                ForEach(Array(slices.enumerated()), id: \.offset) { index, slice in
                    HStack(spacing: 6) {
                        RoundedRectangle(cornerRadius: 2).fill(Self.color(index)).frame(width: 8, height: 8)
                        Text(slice.x).font(.caption)
                        Spacer(minLength: 4)
                        if let label = slice.label {
                            Text(label).font(.caption.weight(.medium)).monospacedDigit()
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
