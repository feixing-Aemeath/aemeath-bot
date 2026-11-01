import AssistantCore
import SwiftUI

/// How the cards on a Bot's page look, picked in 设置 → 卡片风格 and kept on this device. Every
/// style takes the Bot's color from `.tint` (its sticker's accent); on the watch cards keep
/// their own look.
public enum CardStyle: String, CaseIterable, Identifiable, Sendable {
    /// A white card with a hairline border and a serif title: the App's first look.
    case plain
    /// The same card with a strip of the Bot's color along the top and an icon before the title.
    case accent
    /// A wash of the Bot's color over the card, with the title in that color and no gray border.
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
        case .accent: return "卡片顶上一道 Bot 的颜色，标题前带一个小图标。"
        case .soft: return "整张卡片铺一层 Bot 的颜色，标题也用这个颜色。"
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
}

/// A card's title in the chosen style: the serif title, after the card's icon (点彩) or in the
/// Bot's color (柔光).
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
                    Image(systemName: icon)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tint)
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
                    .font(.callout.weight(item.state == .now ? .semibold : .regular))
                if span == nil, let subtitle = item.subtitle {
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }
                if let detail = item.detail {
                    Text(detail).font(.caption).foregroundStyle(.secondary)
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

/// A ring per item, two to a row: the gray ring is the full mark, the colored arc the score so
/// far, with the score in the middle (`detail` "72 / 100": 72 large, / 100 small).
struct RingsList: View {
    let items: [CardListItem]

    var body: some View {
        let pairs = stride(from: 0, to: items.count, by: 2).map { Array(items[$0..<min($0 + 2, items.count)]) }
        VStack(spacing: 16) {
            ForEach(Array(pairs.enumerated()), id: \.offset) { _, pair in
                HStack(alignment: .top, spacing: 12) {
                    ForEach(Array(pair.enumerated()), id: \.offset) { _, item in
                        ring(item).frame(maxWidth: .infinity)
                    }
                    if pair.count == 1 {
                        Color.clear.frame(maxWidth: .infinity, maxHeight: 1)
                    }
                }
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
