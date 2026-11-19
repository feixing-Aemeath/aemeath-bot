import Foundation

/// One figure that sums a Bot up in the row at the top of 仪表盘 (今天一览), taken from what the
/// App already has: today's todos for the assistant, a character's cards for the others.
public struct Glance: Hashable, Sendable {
    /// What the figure is: 「今日待办」, 「平均绩点」, the card's title.
    public var label: String
    /// The figure as the card gives it: 「8.2」, 「已用 82%」, 「高等数学I（A）」.
    public var value: String
    public var unit: String?
    /// One short line under it: the change since before, the card's own label, when a class runs.
    public var note: String?
    /// Something to look at (a limit nearly used up, a todo overdue): the note and the bar in
    /// orange (warn) or red (alert).
    public var tone: CardListItem.Progress.Tone?
    /// A bar under the figure, from 0 to 1.
    public var progress: Double?
    public var trend: CardStat.Trend?

    /// The note's first part (「标准 4.0」 of 「标准 4.0 · 按学分加权」), for a tile too narrow.
    public var shortNote: String? { note?.components(separatedBy: " · ").first }

    public init(
        label: String, value: String, unit: String? = nil, note: String? = nil,
        tone: CardListItem.Progress.Tone? = nil, progress: Double? = nil, trend: CardStat.Trend? = nil
    ) {
        self.label = label
        self.value = value
        self.unit = unit
        self.note = note
        self.tone = tone
        self.progress = progress
        self.trend = trend
    }
}

public enum DashboardGlance {
    /// The figure a Bot's cards (in the order its page shows them) lead with, or nil when none
    /// sums it up. In this order:
    /// 1. something getting high: a row or a bar the backend marked red, then orange (Claude's
    ///    week nearly used);
    /// 2. the page's big number (a `wide` stat) or a stat the plugin puts `first`;
    /// 3. today's timeline (a card put first, or with items marked done or now): what is on now,
    ///    else what comes next; a day that is over says nothing;
    /// 4. the first stat (准备度, 第二课堂学分);
    /// 5. the first row with a bar or a ring (平均绩点 3.62 / 4);
    /// 6. a donut's total.
    public static func lead(_ cards: [Card]) -> Glance? {
        selection(cards)?.glance
    }

    static func selection(_ cards: [Card]) -> (cardID: String, glance: Glance)? {
        let parsed = cards.map { (card: $0, content: $0.content) }
        for tone in [CardListItem.Progress.Tone.alert, .warn] {
            for (card, content) in parsed {
                switch content {
                case .list(let items):
                    if let item = items.first(where: { $0.progress?.tone == tone }) {
                        return (card.id, glance(item, in: card, tone: tone))
                    }
                case .stat(let stat) where stat.bar?.tone == tone:
                    return (card.id, glance(stat, in: card))
                default:
                    continue
                }
            }
        }
        for (card, content) in parsed where card.isWide || card.first {
            if case .stat(let stat) = content { return (card.id, glance(stat, in: card)) }
        }
        for (card, content) in parsed where card.listStyle == .timeline {
            guard case .list(let items) = content, card.first || items.contains(where: { $0.state != nil })
            else { continue }
            if let now = items.first(where: { $0.state == .now }) {
                return (card.id, Glance(label: card.title, value: now.title, note: joined(["正在进行", now.subtitle])))
            }
            if let next = items.first(where: { $0.state == nil }) {
                return (card.id, Glance(label: card.title, value: next.title, note: joined(["接下来", next.subtitle])))
            }
        }
        for (card, content) in parsed {
            if case .stat(let stat) = content { return (card.id, glance(stat, in: card)) }
        }
        for (card, content) in parsed {
            if case .list(let items) = content, let item = items.first(where: { $0.progress != nil }) {
                return (card.id, glance(item, in: card, tone: item.progress?.tone))
            }
        }
        for (card, content) in parsed {
            if case .chart(let chart) = content, chart.kind == .donut, let total = chart.value {
                return (card.id, Glance(label: card.title, value: total, unit: chart.unit, note: chart.label))
            }
        }
        return nil
    }

    /// The assistant's figure: today's todos still open, then how many are overdue (red), or
    /// the nearest deadline, or that all are done.
    public static func todos(_ todos: [Todo], now: Date, calendar: Calendar) -> Glance {
        let open = todos.filter { $0.status == .pending }
        let label = "今日待办"
        guard !open.isEmpty else {
            return Glance(label: label, value: "0", unit: "件", note: "今天的都做完了")
        }
        let overdue = open.filter { DueText.isOverdue($0, now: now, calendar: calendar) }.count
        if overdue > 0 {
            return Glance(label: label, value: "\(open.count)", unit: "件", note: "\(overdue) 件已逾期", tone: .alert)
        }
        let nearest = open.min { ($0.dueAt ?? .distantFuture) < ($1.dueAt ?? .distantFuture) }
        let note = nearest.map { "最近截止 " + DueText.describe($0, now: now, calendar: calendar) }
        return Glance(label: label, value: "\(open.count)", unit: "件", note: note)
    }

    /// A stat as a figure: its number and unit, the change since before (or the first line of
    /// its label) under it, its bar filled to the share used.
    static func glance(_ stat: CardStat, in card: Card) -> Glance {
        var progress: Double?
        if let bar = stat.bar, bar.max > 0 {
            progress = min(max(bar.segments.reduce(0) { $0 + max($1.y, 0) } / bar.max, 0), 1)
        }
        let label = stat.label?.split(whereSeparator: \.isNewline).first.map(String.init)
        return Glance(
            label: card.title, value: stat.value, unit: stat.unit, note: stat.delta ?? label,
            tone: stat.bar?.tone, progress: progress, trend: stat.trend)
    }

    /// A row with a bar or a ring as a figure. A ring's 「72 / 100」 is 72 with 「/ 100」 as its
    /// unit; a red or orange row keeps its card's title (「Claude 用量限额 · Max」) and says
    /// which row it is under the figure.
    static func glance(_ item: CardListItem, in card: Card, tone: CardListItem.Progress.Tone?) -> Glance {
        let fraction = item.progress?.fraction
        let shown = item.detail ?? fraction.map { "\(Int(($0 * 100).rounded()))%" } ?? ""
        let parts = shown.components(separatedBy: " / ")
        let (value, unit) = parts.count == 2 ? (parts[0], "/ \(parts[1])") : (shown, nil as String?)
        if tone != nil {
            return Glance(
                label: card.title, value: value, unit: unit, note: joined([item.title, item.subtitle]), tone: tone,
                progress: fraction)
        }
        return Glance(label: item.title, value: value, unit: unit, note: item.subtitle, progress: fraction)
    }

    static func joined(_ parts: [String?]) -> String? {
        let words = parts.compactMap { $0?.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return words.isEmpty ? nil : words.joined(separator: " · ")
    }

    /// 「今天」's own cards on 仪表盘: the small numbers (天气's today and tomorrow), which sit
    /// beside the todos on a wide screen; then the rest in their order, prose last: the morning
    /// brief (今日提醒) says again what the others show.
    public static func todayOrder(_ cards: [Card]) -> (tiles: [Card], rest: [Card]) {
        let tiles = cards.filter(\.isTile)
        let rest = cards.filter { !$0.isTile }
        return (tiles, rest.filter { $0.type != .text } + rest.filter { $0.type == .text })
    }
}
