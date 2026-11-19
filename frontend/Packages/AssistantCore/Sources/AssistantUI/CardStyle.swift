import AssistantCore
import SwiftUI

/// How the cards on a Bot's page look, picked in 设置 → 卡片风格 and kept on this device. Every
/// style takes the Bot's color from `.tint` (its sticker's accent); on the watch cards keep
/// their own look.
public enum CardStyle: String, CaseIterable, Identifiable, Sendable {
    /// A white card with a hairline border and a serif title: the App's first look.
    case plain
    /// The same card with a short bar of the Bot's color before the title.
    case accent
    /// A light wash of the Bot's color over the card, with the title in that color.
    case soft

    public static let storageKey = "assistant.cardStyle"

    public var id: String { rawValue }

    public var name: String {
        switch self {
        case .plain: return "素雅"
        case .accent: return "点彩"
        case .soft: return "柔光"
        }
    }

    public var summary: String {
        switch self {
        case .plain: return "白底细边框，和 Claude 的 App 一样安静。"
        case .accent: return "白底卡片，标题前一小道 Bot 的颜色。"
        case .soft: return "卡片铺一层很淡的 Bot 的颜色，标题也用这个颜色。"
        }
    }
}

private struct CardStyleKey: EnvironmentKey {
    static let defaultValue = CardStyle.plain
}

extension EnvironmentValues {
    var cardStyle: CardStyle {
        get { self[CardStyleKey.self] }
        set { self[CardStyleKey.self] = newValue }
    }

    /// The Bot's color for large text in its cards (a page's big number): darker on the light
    /// page, lighter on the dark one.
    var cardAccentText: Color {
        get { self[CardAccentTextKey.self] }
        set { self[CardAccentTextKey.self] = newValue }
    }
}

private struct CardAccentTextKey: EnvironmentKey {
    static let defaultValue = Color.accentColor
}

/// A card's title in the chosen style: the serif title, after a short bar of the Bot's color
/// (点彩) or in that color (柔光).
struct CardTitle: View {
    let text: String
    var icon: String = "square.text.square"
    @Environment(\.cardStyle) private var style

    init(_ text: String, icon: String = "square.text.square") {
        self.text = text
        self.icon = icon
    }

    var body: some View {
        #if os(watchOS)
            Text(text).font(CardSurface.titleFont)
        #else
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if style == .accent {
                    Capsule().fill(.tint).frame(width: 3, height: 14).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
                }
                Text(text)
                    .font(CardSurface.titleFont)
                    .foregroundStyle(style == .soft ? AnyShapeStyle(.tint) : AnyShapeStyle(.primary))
            }
        #endif
    }
}

extension Card {
    /// The icon before the title in the 点彩 style.
    var icon: String {
        switch content {
        case .list: return listStyle == .timeline ? "clock" : listStyle == .rings ? "circle.dashed" : "list.bullet"
        case .stat: return "number"
        case .chart: return "chart.bar.xaxis"
        case .photoGrid: return "photo.on.rectangle"
        case .text: return "text.alignleft"
        case .todoList: return "checklist"
        case .unsupported: return "questionmark.square.dashed"
        }
    }
}

/// Items in time order on a line, as a day's classes: start and end time on the left, a dot in
/// the Bot's color on the line, the title on the right. What is over is faded; what is going on
/// now has a filled dot and says so.
struct TimelineList: View {
    let items: [CardListItem]

    /// "08:00–09:35" (en dash or hyphen) as its two times; anything else is not a span.
    static func span(_ text: String?) -> (start: String, end: String)? {
        guard let text else { return nil }
        let parts = text.split(whereSeparator: { $0 == "–" || $0 == "-" || $0 == "~" })
            .map { $0.trimmingCharacters(in: .whitespaces) }
        guard parts.count == 2, parts.allSatisfy({ $0.count == 5 && $0.contains(":") }) else { return nil }
        return (parts[0], parts[1])
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                row(item, last: index == items.count - 1)
            }
        }
    }

    func row(_ item: CardListItem, last: Bool) -> some View {
        let span = Self.span(item.subtitle)
        return HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .trailing, spacing: 1) {
                Text(span?.start ?? "").font(.callout.weight(.semibold)).monospacedDigit()
                Text(span?.end ?? "").font(.caption2).foregroundStyle(.secondary).monospacedDigit()
            }
            .frame(width: 46, alignment: .trailing)
            // So 09:35, when a class ends, stays clear of the next one's 09:55.
            .padding(.bottom, last ? 0 : 8)
            VStack(spacing: 0) {
                ZStack {
                    Circle().strokeBorder(.tint, lineWidth: 2)
                    if item.state == .now {
                        Circle().fill(.tint).padding(2)
                    }
                }
                .frame(width: 11, height: 11)
                .padding(.top, 4)
                if !last {
                    Rectangle().fill(.tint.opacity(0.28)).frame(width: 2)
                }
            }
            .frame(width: 11)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .font(CardFont.body.weight(item.state == .now ? .semibold : .regular))
                if span == nil, let subtitle = item.subtitle {
                    Text(subtitle).font(CardFont.note).foregroundStyle(.secondary)
                }
                if let detail = item.detail {
                    Text(detail).font(CardFont.note).foregroundStyle(.secondary)
                }
                if item.state == .now {
                    Text("正在进行").font(.caption2.weight(.semibold)).foregroundStyle(.tint)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, last ? 0 : 14)
        }
        // As tall as its text, so the line between the dots reaches the next one.
        .fixedSize(horizontal: false, vertical: true)
        .opacity(item.state == .done ? 0.45 : 1)
    }
}

/// A ring per item, as many to a row as the card's width holds (rows kept even: four rings go
/// two and two rather than three and one): the gray ring is the full mark, the colored arc the
/// score so far, with the score in the middle (`detail` "72 / 100": 72 large, / 100 small).
struct RingsList: View {
    let items: [CardListItem]

    var body: some View {
        RingsLayout(count: items.count) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                ring(item).frame(maxWidth: .infinity)
            }
        }
        .padding(.top, 4)
    }

    func ring(_ item: CardListItem) -> some View {
        let fraction = item.progress?.fraction ?? 0
        let parts = (item.detail ?? "").components(separatedBy: " / ")
        return VStack(spacing: 6) {
            ZStack {
                Circle().stroke(Color.secondary.opacity(0.16), lineWidth: 7)
                Circle()
                    .trim(from: 0, to: fraction)
                    .stroke(.tint, style: StrokeStyle(lineWidth: 7, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                VStack(spacing: 0) {
                    Text(parts.first.flatMap { $0.isEmpty ? nil : $0 } ?? "\(Int((fraction * 100).rounded()))%")
                        .font(.headline)
                        .monospacedDigit()
                    if parts.count == 2 {
                        Text("/ \(parts[1])").font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
            }
            .frame(width: 78, height: 78)
            .accessibilityElement()
            .accessibilityLabel(Text(item.title))
            .accessibilityValue(Text(item.detail ?? "\(Int((fraction * 100).rounded()))%"))
            Text(item.title).font(.subheadline.weight(.semibold))
            if let subtitle = item.subtitle {
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// Rings in even rows of at most as many as fit at `minimum` points each.
struct RingsLayout: Layout {
    let count: Int
    var minimum: CGFloat = 110
    var spacing: CGFloat = 12
    var rowSpacing: CGFloat = 16

    func columns(_ width: CGFloat) -> Int {
        let fit = max(1, Int((width + spacing) / (minimum + spacing)))
        let columns = min(max(count, 1), fit)
        let rows = (count + columns - 1) / max(columns, 1)
        return max(1, (count + rows - 1) / max(rows, 1))
    }

    func cells(_ width: CGFloat, _ subviews: Subviews) -> (width: CGFloat, heights: [CGFloat], columns: Int) {
        let columns = columns(width)
        let cell = (width - spacing * CGFloat(columns - 1)) / CGFloat(columns)
        var heights: [CGFloat] = []
        for start in stride(from: 0, to: subviews.count, by: columns) {
            let row = subviews[start..<min(start + columns, subviews.count)]
            heights.append(row.map { $0.sizeThatFits(ProposedViewSize(width: cell, height: nil)).height }.max() ?? 0)
        }
        return (cell, heights, columns)
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width.flatMap { $0.isFinite ? $0 : nil } ?? minimum * 2 + spacing
        let layout = cells(width, subviews)
        return CGSize(
            width: width,
            height: layout.heights.reduce(0, +) + rowSpacing * CGFloat(max(layout.heights.count - 1, 0)))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let layout = cells(bounds.width, subviews)
        var y = bounds.minY
        for (row, height) in layout.heights.enumerated() {
            let start = row * layout.columns
            let members = min(layout.columns, subviews.count - start)
            // A short last row is centered under the full ones.
            let offset = CGFloat(layout.columns - members) * (layout.width + spacing) / 2
            for slot in 0..<members {
                subviews[start + slot].place(
                    at: CGPoint(x: bounds.minX + offset + CGFloat(slot) * (layout.width + spacing), y: y),
                    anchor: .topLeading, proposal: ProposedViewSize(width: layout.width, height: height))
            }
            y += height + rowSpacing
        }
    }
}

/// A card's SF Symbol drawn as a small picture in its corner: the day's weather in color, on a
/// soft disc in the colors of the sky it shows; anything else (记账's gift) in the Bot's color.
struct CardIllustration: View {
    let symbol: String

    static let skies = ["sun", "cloud", "moon", "rain", "drizzle", "snow", "bolt", "fog", "wind", "hail", "smoke"]

    var isWeather: Bool { Self.skies.contains { symbol.contains($0) } }

    var sky: [Color] {
        if symbol.contains("bolt") { return [.purple, .indigo] }
        if symbol.contains("snow") { return [.cyan, .blue] }
        if symbol.contains("rain") || symbol.contains("drizzle") { return [.blue, .teal] }
        if symbol.contains("fog") || symbol == "cloud.fill" { return [.gray, .secondary] }
        if symbol.contains("cloud") { return [.orange, .blue] }
        if symbol.contains("moon") { return [.indigo, .purple] }
        return [.yellow, .orange]
    }

    var body: some View {
        ZStack {
            if isWeather {
                Circle()
                    .fill(LinearGradient(colors: sky, startPoint: .topLeading, endPoint: .bottomTrailing))
                    .opacity(0.2)
                    .frame(width: 58, height: 58)
                Image(systemName: symbol)
                    .symbolRenderingMode(.multicolor)
                    .font(.system(size: 30))
            } else {
                Circle().fill(.tint.opacity(0.14)).frame(width: 58, height: 58)
                Image(systemName: symbol)
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(.tint)
                    .font(.system(size: 26))
            }
        }
        .accessibilityHidden(true)
    }
}
