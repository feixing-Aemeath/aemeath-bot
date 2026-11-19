import Foundation

/// 仪表盘 as an edited page rather than every plugin's cards one after another: what needs the
/// user comes first, a figure that says nothing yet steps back, and a card does not say again
/// what the card beside it shows.
public enum DashboardEdit {
    // MARK: Nothing to report

    /// 「—」, 「— / 4」, an empty string: a figure the backend has no number for yet.
    static func isPlaceholder(_ text: String) -> Bool {
        let figure = text.components(separatedBy: " / ")[0].trimmingCharacters(in: .whitespaces)
        return figure.allSatisfy { "—–-－".contains($0) }
    }

    /// 「0」, 「0 学分」, 「0 / 6」: a count of nothing.
    static func isZero(_ text: String) -> Bool {
        let figure = text.components(separatedBy: " / ")[0].trimmingCharacters(in: .whitespaces)
        let number = figure.prefix { $0.isASCII && ($0.isNumber || $0 == "." || $0 == ",") }
        guard !number.isEmpty, Double(number.replacingOccurrences(of: ",", with: "")) == 0 else { return false }
        // 「0 学分」 is nothing; 「0 元，还有 2,000」 says something after the zero.
        return !figure.dropFirst(number.count).contains { $0.isASCII && $0.isNumber }
    }

    static func isNothing(_ text: String) -> Bool { isPlaceholder(text) || isZero(text) }

    // MARK: 一览

    /// The Bots of 一览 in two parts: `tiles`, those whose figure says something, the ones that
    /// need a look (red, then orange) first and the rest in the page's order; `quiet`, those
    /// with nothing to report (no figure, 「—」, a plain zero), which share one line under the
    /// tiles instead of a tile each.
    public static func strip(_ glances: [(id: String, glance: Glance?)]) -> (tiles: [String], quiet: [String]) {
        func rank(_ glance: Glance?) -> Int {
            switch glance?.tone {
            case .alert: return 0
            case .warn: return 1
            case nil: return 2
            }
        }
        let live = glances.filter { $0.glance.map { !$0.isBlank } ?? false }
        let quiet = glances.filter { $0.glance.map(\.isBlank) ?? true }
        let ordered = live.enumerated()
            .sorted { (rank($0.element.glance), $0.offset) < (rank($1.element.glance), $1.offset) }
            .map(\.element.id)
        return (ordered, quiet.map(\.id))
    }

    /// A Bot's cards with the ones that say nothing yet after the others, so they are the ones
    /// behind 「还有 N 张卡片」.
    public static func blankLast(_ cards: [Card]) -> [Card] {
        cards.filter { !$0.isBlank } + cards.filter(\.isBlank)
    }

    public static func panel(_ cards: [Card]) -> (glance: Glance?, cards: [Card]) {
        let selection = DashboardGlance.selection(cards)
        let tile = selection.flatMap { $0.glance.isBlank ? nil : $0 }
        let rest = cards.filter { $0.id != tile?.cardID }
        return (tile?.glance, rest.allSatisfy(\.isBlank) ? [] : blankLast(rest))
    }

    public static func preview<T>(_ items: [T], compact: Bool) -> (items: [T], remaining: Int) {
        let shown = Array(items.prefix(compact ? 3 : 5))
        return (shown, items.count - shown.count)
    }

    /// Count configured switches, including an enabled source that currently reports an error.
    public static func sourceSummary(_ inputs: [PluginView]) -> String {
        let enabled = inputs.filter(\.enabled).count
        return "\(enabled) 个来源启用，\(inputs.count - enabled) 个停用"
    }

    // MARK: 今天

    /// The parts of the morning brief (今日提醒) by the bold name each starts with: 「**天气**
    /// 多云，9–21°C」 and the items under it are the part 「天气」. Lines before the first part
    /// have no name.
    static func briefParts(_ text: String) -> [(name: String?, lines: [String])] {
        var parts: [(name: String?, lines: [String])] = []
        for line in text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline).map(String.init) {
            if let name = partName(line) {
                parts.append((name, [line]))
            } else if parts.isEmpty {
                parts.append((nil, [line]))
            } else {
                parts[parts.count - 1].lines.append(line)
            }
        }
        return parts
    }

    /// 「天气」 of 「**天气** 多云，9–21°C」 or of 「**天气**」.
    static func partName(_ line: String) -> String? {
        let text = line.trimmingCharacters(in: .whitespaces)
        guard text.hasPrefix("**"), let close = text.dropFirst(2).range(of: "**") else { return nil }
        let name = text.dropFirst(2)[..<close.lowerBound].trimmingCharacters(in: .whitespaces)
        return name.isEmpty || name.contains("*") ? nil : name
    }

    /// Whether a text card is the morning brief: it has a part 「天气」.
    public static func isBrief(_ text: String) -> Bool {
        briefParts(text).contains { $0.name == "天气" }
    }

    /// The brief without the parts named: on 仪表盘 today's todos are the card above it and the
    /// weather the cards beside it, so it keeps the classes and what to watch out for. Empty
    /// when nothing else is left.
    public static func trimBrief(
        _ text: String, dropping names: Set<String>, systemAlerts: [String] = [], weatherFacts: Set<String> = []
    ) -> String {
        let alerts = systemAlerts.map(normalized).filter { !$0.isEmpty }
        let blocks = briefParts(text).compactMap { part -> String? in
            if let name = part.name, names.contains(name) { return nil }
            if part.name == "注意", !alerts.isEmpty || !weatherFacts.isEmpty {
                return editedWarnings(part.lines, alerts: alerts, facts: weatherFacts)
            }
            return part.lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return blocks.filter { !$0.isEmpty }.joined(separator: "\n\n")
    }

    /// Keep a rain/class conflict or another warning when the adjacent todo already names a system alert.
    private static func editedWarnings(_ lines: [String], alerts: [String], facts: Set<String>) -> String? {
        let heading = "**注意**"
        let summary = String(lines[0].trimmingCharacters(in: .whitespaces).dropFirst(heading.count))
        let kept = ([summary] + lines.dropFirst()).enumerated().compactMap { index, line -> String? in
            let text = line.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty, !alerts.contains(where: normalized(text).contains) else { return nil }
            let bullet = text.hasPrefix("- ")
            let body = bullet ? String(text.dropFirst(2)) : text
            let rest = removingFacts(facts, from: body)
            guard !rest.isEmpty else { return nil }
            return (index == 0 ? " " : "\n") + (bullet ? "- " : "") + rest
        }
        return kept.isEmpty ? nil : heading + kept.joined()
    }

    /// Deduplicate only the dashboard copy. Tomorrow's numbers do not describe today's outing.
    public static func todayCards(_ cards: [Card], systemAlerts: [String] = []) -> [Card] {
        let weather = cards.filter { $0.plugin == "weather" || $0.plugin == nil }
        let forecasts = weather.filter { $0.type == .stat && $0.title.hasSuffix("今天") }
        let facts = Set(forecasts.flatMap { clauses($0.data["label"]?.stringValue ?? "") }
            .filter { $0.contains(where: \.isNumber) }.map(normalized))
        let advice = weather.filter { $0.type == .list && $0.title == "出门提醒" }
        let adviceFacts = advice.flatMap { $0.data["items"]?.arrayValue ?? [] }
            .flatMap { clauses($0["subtitle"]?.stringValue ?? "") }.map(normalized)

        return cards.compactMap { card in
            if advice.contains(card) { return editedAdvice(card, facts: facts) }
            guard card.type == .text, let text = card.data["text"]?.stringValue,
                card.title == "今日提醒" || isBrief(text) else { return card }
            let hasForecast = forecasts.contains { $0.plugin == card.plugin }
            let trimmed = trimBrief(text, dropping: hasForecast ? ["待办", "天气"] : ["待办"],
                                    systemAlerts: systemAlerts, weatherFacts: facts.union(adviceFacts))
            return trimmed.isEmpty ? nil : card.replacingText(trimmed)
        }
    }

    private static func editedAdvice(_ card: Card, facts: Set<String>) -> Card {
        guard !facts.isEmpty, case .object(var data) = card.data, let items = data["items"]?.arrayValue else { return card }
        data["items"] = .array(items.map { item in
            guard case .object(var row) = item, let subtitle = row["subtitle"]?.stringValue else { return item }
            let kept = removingFacts(facts, from: subtitle)
            row["subtitle"] = kept.isEmpty ? nil : .string(kept)
            return .object(row)
        })
        var copy = card
        copy.data = .object(data)
        return copy
    }

    private static func clauses(_ text: String) -> [String] {
        text.components(separatedBy: CharacterSet(charactersIn: "，,·；;"))
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
    }

    private static func normalized(_ text: String) -> String {
        text.filter { !$0.isWhitespace && $0 != "*" }.lowercased()
            .trimmingCharacters(in: CharacterSet(charactersIn: "。.!！"))
    }

    private static func removingFacts(_ facts: Set<String>, from text: String) -> String {
        let parts = clauses(text)
        let kept = parts.filter { !facts.contains(normalized($0)) }
        return kept.count == parts.count ? text : kept.filter { !$0.isEmpty }.joined(separator: "，")
    }

    /// The open todos that are not on today's card: a later deadline, or none. The soonest
    /// first, then the ones without a date, newest first. Without this list a todo the user
    /// just wrote for next week was nowhere in the App.
    public static func later(_ all: [Todo], today: [Todo]) -> [Todo] {
        let shown = Set(today.map(\.id))
        let open = all.filter { $0.status == .pending && !shown.contains($0.id) }
        let dated = open.compactMap { todo in todo.dueAt.map { (todo, $0) } }
            .sorted { ($0.1, $0.0.createdAt) < ($1.1, $1.0.createdAt) }.map(\.0)
        let undated = open.filter { $0.dueAt == nil }.sorted { $0.createdAt > $1.createdAt }
        return dated + undated
    }

    /// The lines under 「**还不确定**」 in today's summary: what the Agent read but could not
    /// settle (a preview cut short, a deadline it could not make out). They need the user.
    public static func unsure(in summary: String) -> [String] {
        var items: [String] = []
        var inside = false
        for line in RichLine.lines(summary) {
            switch line {
            case .section(let name): inside = name == "还不确定"
            case .heading: inside = false
            case .bullet(let item), .numbered(_, let item):
                if inside { items.append(item.replacingOccurrences(of: "**", with: "")) }
            default: continue
            }
        }
        return items
    }
}

extension Glance {
    /// Nothing to report: 「—」 or a plain zero that nobody marked red or orange.
    public var isBlank: Bool {
        tone == nil && DashboardEdit.isNothing(value)
    }
}

extension Card {
    /// A card that says nothing yet: a number that is 「—」 or zero, rings and rows that all
    /// are, a chart of zeros, an empty text. It still shows on the Bot's own page.
    public var isBlank: Bool {
        switch content {
        case .stat(let stat):
            if DashboardEdit.isPlaceholder(stat.value) { return true }
            return DashboardEdit.isZero(stat.value) && stat.bar == nil && stat.delta == nil
        case .list(let items):
            return items.allSatisfy { item in item.detail.map(DashboardEdit.isNothing) ?? false }
        case .chart(let chart):
            return chart.series.allSatisfy { $0.points.allSatisfy { $0.y == 0 } }
        case .text(let text):
            return text.table == nil && text.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        case .photoGrid(let photos):
            return photos.isEmpty
        case .todoList(let todos): return todos.isEmpty
        case .unsupported:
            return false
        }
    }

    /// The same text card saying `text` instead.
    func replacingText(_ text: String) -> Card {
        guard case .object(var object) = data else { return self }
        object["text"] = .string(text)
        var copy = self
        copy.data = .object(object)
        return copy
    }
}

extension CardListItem {
    /// Whether the word at the end of a row is a figure (「456 元」, 「已用 88%」) or a state in
    /// words (「审批中」, 「还没报名」): a figure is as dark as the title, a state a quiet label.
    public var detailIsFigure: Bool {
        detail?.contains { $0.isASCII && $0.isNumber } ?? false
    }
}

extension Bot {
    /// A Bot about how the App itself runs (用量: tokens and limits), not about the user's
    /// day: its group is the last on 仪表盘.
    public var isUpkeep: Bool { id == "token_usage" }
}
