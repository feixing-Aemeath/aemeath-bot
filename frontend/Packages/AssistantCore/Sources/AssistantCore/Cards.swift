import Foundation

/// The unit of server-driven UI: `{type, title, data}`. A new plugin only combines these
/// types, so the App never needs a rebuild for it. `data` per type is described in
/// docs/api.md ("卡片 data 约定"); anything unexpected becomes `.unsupported` instead of
/// failing the whole response.
public struct Card: Codable, Hashable, Identifiable, Sendable {
    public var type: CardType
    public var title: String
    public var data: JSONValue

    public init(type: CardType, title: String, data: JSONValue) {
        self.type = type
        self.title = title
        self.data = data
    }

    public var id: String { "\(type.rawValue):\(title)" }

    public var content: CardContent { CardContent(card: self) }

    /// How a list card lays out its items; plain rows unless `data.style` names another.
    public var listStyle: CardListStyle {
        data["style"]?.stringValue.flatMap(CardListStyle.init(rawValue:)) ?? .rows
    }

    /// An SF Symbol drawn large in the card's corner, e.g. the day's weather.
    public var symbol: String? { data["symbol"]?.stringValue }

    /// Shown before the Bot's other cards, e.g. today's classes before the homework.
    public var first: Bool { data["first"]?.boolValue ?? false }

    /// A small card with one number: two of them share a row on the iPhone, as squares. A wide
    /// one (`data.wide`, the page's big number) has a row of its own.
    public var isTile: Bool { type == .stat && !isWide }

    public var isWide: Bool { type == .stat && data["wide"]?.boolValue == true }
}

public enum CardListStyle: String, Hashable, Sendable {
    /// One row per item, with its progress bar under it.
    case rows
    /// Items in time order on a line, as a day's classes: `subtitle` is "08:00–09:35".
    case timeline
    /// A ring per item, two to a row: every item has `progress`.
    case rings
}

public enum CardType: Hashable, Sendable, Codable {
    case list, stat, chart, photoGrid, text, todoList
    /// A type this App version does not know yet.
    case unknown(String)

    public init(rawValue: String) {
        switch rawValue {
        case "list": self = .list
        case "stat": self = .stat
        case "chart": self = .chart
        case "photo_grid": self = .photoGrid
        case "text": self = .text
        case "todo_list": self = .todoList
        default: self = .unknown(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .list: return "list"
        case .stat: return "stat"
        case .chart: return "chart"
        case .photoGrid: return "photo_grid"
        case .text: return "text"
        case .todoList: return "todo_list"
        case .unknown(let value): return value
        }
    }

    public init(from decoder: Decoder) throws {
        self.init(rawValue: try decoder.singleValueContainer().decode(String.self))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

public struct CardListItem: Hashable, Sendable {
    public var title: String
    public var subtitle: String?
    public var detail: String?
    /// A bar under the row: the gray track is `max`, the colored part `value`.
    public var progress: Progress? = nil
    /// On a timeline: the item is over, or going on now.
    public var state: State? = nil

    public enum State: String, Hashable, Sendable {
        case done, now
    }

    public struct Progress: Hashable, Sendable {
        public var value: Double
        public var max: Double
        /// Drawn in orange (warn) or red (alert) instead of the Bot's color.
        public var tone: Tone? = nil

        public enum Tone: String, Hashable, Sendable {
            case warn, alert
        }

        public var fraction: Double { max > 0 ? Swift.min(Swift.max(value / max, 0), 1) : 0 }
    }
}

public struct CardStat: Hashable, Sendable {
    /// Shown as is; numbers are formatted by the backend's precision.
    public var value: String
    public var unit: String?
    public var label: String?
    public var delta: String?
    public var trend: Trend?
    /// The page's big number: larger, in the Bot's color, on a row of its own.
    public var wide = false
    public var bar: Bar? = nil

    public enum Trend: String, Hashable, Sendable {
        case up, down, flat
    }

    /// A bar under the number: the gray track is `max`, the segments (a donut's slices, in its
    /// colors and order) fill it from the left; past `max` it is full.
    public struct Bar: Hashable, Sendable {
        public var max: Double
        public var segments: [CardChart.Point]
        /// The track in orange (warn) or red (alert), and a red number for alert.
        public var tone: CardListItem.Progress.Tone? = nil
    }
}

public struct CardChart: Hashable, Sendable {
    public enum Kind: String, Hashable, Sendable {
        /// `donut`: one series whose points are the slices of a ring, with a total beside it.
        case line, bar, donut
    }

    public struct Point: Hashable, Sendable {
        public var x: String
        public var y: Double
        /// The number for people, as "344 元": beside a slice, over a bar.
        public var label: String? = nil
    }

    /// A dashed line at `value`, such as the month's allowance; bars over it are orange.
    public struct Target: Hashable, Sendable {
        public var value: Double
        public var label: String?
    }

    public struct Series: Hashable, Sendable {
        public var name: String
        public var points: [Point]
    }

    public var kind: Kind
    public var unit: String?
    public var series: [Series]
    /// The total a donut shows in its ring, and a line under it.
    public var value: String? = nil
    public var label: String? = nil
    public var target: Target? = nil
}

public struct CardPhoto: Hashable, Sendable {
    public var id: String
    public var caption: String?
    /// A path on the Mac; the API serves no image downloads, so the App shows a placeholder.
    public var path: String?
}

public struct CardText: Hashable, Sendable {
    public var text: String
    public var headline: String?
    /// Drawn instead of `text` (which says the same for Apps without tables), with `footnote`
    /// under it.
    public var table: CardTable? = nil
    public var footnote: String? = nil
}

/// Rows of cells under column titles; the first column names the row, e.g. the start time of
/// a week's classes, and `highlight` (a column index) is today's column.
public struct CardTable: Hashable, Sendable {
    public var columns: [String]
    public var rows: [[String]]
    public var highlight: Int?
}

public enum CardContent: Hashable, Sendable {
    case list([CardListItem])
    case stat(CardStat)
    case chart(CardChart)
    case photoGrid([CardPhoto])
    case text(CardText)
    case todoList([Todo])
    case unsupported(type: String, reason: String)

    public init(card: Card) {
        let data = card.data
        switch card.type {
        case .text:
            if let text = data["text"]?.stringValue {
                self = .text(
                    CardText(
                        text: text, headline: data["headline"]?.stringValue,
                        table: CardContent.parseTable(data["table"]), footnote: data["footnote"]?.stringValue))
            } else {
                self = .unsupported(type: "text", reason: "missing text")
            }
        case .stat:
            self = CardContent.parseStat(data)
        case .list:
            guard let items = data["items"]?.arrayValue else {
                self = .unsupported(type: "list", reason: "missing items")
                return
            }
            self = .list(items.compactMap(CardContent.parseListItem))
        case .chart:
            self = CardContent.parseChart(data)
        case .photoGrid:
            guard let photos = data["photos"]?.arrayValue else {
                self = .unsupported(type: "photo_grid", reason: "missing photos")
                return
            }
            self = .photoGrid(
                photos.enumerated().compactMap { index, photo in
                    guard let object = photo.objectValue else { return nil }
                    return CardPhoto(
                        id: object["id"]?.stringValue ?? String(index),
                        caption: object["caption"]?.stringValue,
                        path: object["path"]?.stringValue)
                })
        case .todoList:
            guard let todos = data["todos"], let decoded = try? todos.decode([Todo].self) else {
                self = .unsupported(type: "todo_list", reason: "missing or invalid todos")
                return
            }
            self = .todoList(decoded)
        case .unknown(let type):
            self = .unsupported(type: type, reason: "unknown card type")
        }
    }

    static func display(_ value: JSONValue?) -> String? {
        guard let value else { return nil }
        switch value {
        case .string(let text): return text
        case .number(let number):
            if number.rounded() == number, abs(number) < 1e15 { return String(Int64(number)) }
            return String(number)
        case .bool(let flag): return flag ? "是" : "否"
        default: return nil
        }
    }

    static func parseStat(_ data: JSONValue) -> CardContent {
        var value = display(data["value"])
        var label = data["label"]?.stringValue
        if value == nil, let object = data.objectValue {
            // Tolerate simple counters such as {"count": 3}.
            let numbers = object.filter { $0.value.doubleValue != nil }
            if numbers.count == 1, let only = numbers.first {
                value = display(only.value)
                label = label ?? only.key
            }
        }
        guard let shown = value else {
            return .unsupported(type: "stat", reason: "missing value")
        }
        var bar: CardStat.Bar?
        if let max = data["bar"]?["max"]?.doubleValue {
            bar = CardStat.Bar(
                max: max, segments: parsePoints(data["bar"]?["segments"]) ?? [],
                tone: data["bar"]?["tone"]?.stringValue.flatMap(CardListItem.Progress.Tone.init(rawValue:)))
        }
        return .stat(
            CardStat(
                value: shown,
                unit: data["unit"]?.stringValue,
                label: label,
                delta: display(data["delta"]),
                trend: data["trend"]?.stringValue.flatMap(CardStat.Trend.init(rawValue:)),
                wide: data["wide"]?.boolValue ?? false, bar: bar))
    }

    static func parseListItem(_ item: JSONValue) -> CardListItem? {
        if let text = item.stringValue { return CardListItem(title: text) }
        guard let title = display(item["title"]) else { return nil }
        var progress: CardListItem.Progress?
        if let value = item["progress"]?["value"]?.doubleValue, let max = item["progress"]?["max"]?.doubleValue {
            progress = CardListItem.Progress(
                value: value, max: max,
                tone: item["progress"]?["tone"]?.stringValue.flatMap(CardListItem.Progress.Tone.init(rawValue:)))
        }
        return CardListItem(
            title: title, subtitle: display(item["subtitle"]), detail: display(item["detail"]),
            progress: progress, state: item["state"]?.stringValue.flatMap(CardListItem.State.init(rawValue:)))
    }

    /// A table whose rows all have a cell per column; anything else is no table (the card
    /// shows its text).
    static func parseTable(_ value: JSONValue?) -> CardTable? {
        guard let columns = value?["columns"]?.arrayValue?.compactMap(display), !columns.isEmpty,
            let rows = value?["rows"]?.arrayValue?.map({ $0.arrayValue?.compactMap(display) ?? [] }),
            !rows.isEmpty, rows.allSatisfy({ $0.count == columns.count })
        else { return nil }
        let highlight = value?["highlight"]?.doubleValue.map { Int($0) }
        return CardTable(
            columns: columns, rows: rows,
            highlight: highlight.flatMap { (1..<columns.count).contains($0) ? $0 : nil })
    }

    static func parsePoints(_ value: JSONValue?) -> [CardChart.Point]? {
        guard let array = value?.arrayValue else { return nil }
        return array.compactMap { point in
            guard let x = display(point["x"]), let y = point["y"]?.doubleValue else { return nil }
            return CardChart.Point(x: x, y: y, label: point["label"]?.stringValue)
        }
    }

    static func parseChart(_ data: JSONValue) -> CardContent {
        var series: [CardChart.Series] = []
        if let points = parsePoints(data["points"]) {
            series = [CardChart.Series(name: data["name"]?.stringValue ?? "", points: points)]
        } else if let many = data["series"]?.arrayValue {
            series = many.compactMap { item in
                guard let points = parsePoints(item["points"]) else { return nil }
                return CardChart.Series(name: item["name"]?.stringValue ?? "", points: points)
            }
        }
        guard !series.isEmpty else {
            return .unsupported(type: "chart", reason: "missing points")
        }
        let kind = data["kind"]?.stringValue.flatMap(CardChart.Kind.init(rawValue:)) ?? .line
        let target = data["target"]?["value"]?.doubleValue.map {
            CardChart.Target(value: $0, label: data["target"]?["label"]?.stringValue)
        }
        return .chart(
            CardChart(
                kind: kind, unit: data["unit"]?.stringValue, series: series,
                value: display(data["value"]), label: data["label"]?.stringValue, target: target))
    }
}
