import Foundation

public struct ExtractionStats: Codable, Hashable, Sendable {
    public var weeks: [ExtractionWeek]
    public init(weeks: [ExtractionWeek]) { self.weeks = weeks }
}
public struct ExtractionWeek: Codable, Hashable, Identifiable, Sendable {
    public var weekStart: String
    public var caught: Int
    public var withDue: Int
    public var late: Int
    public var medianLeadHours: Double?
    public var missed: Int
    public var notATodo: Int
    public var recall: Double?
    public var id: String { weekStart }
    public init(
        weekStart: String, caught: Int, withDue: Int, late: Int, medianLeadHours: Double?, missed: Int,
        notATodo: Int, recall: Double?
    ) {
        self.weekStart = weekStart
        self.caught = caught
        self.withDue = withDue
        self.late = late
        self.medianLeadHours = medianLeadHours
        self.missed = missed
        self.notATodo = notATodo
        self.recall = recall
    }
    enum CodingKeys: String, CodingKey {
        case caught, late, missed, recall
        case weekStart = "week_start"
        case withDue = "with_due"
        case medianLeadHours = "median_lead_hours"
        case notATodo = "not_a_todo"
    }
    public var hasData: Bool { caught > 0 || missed > 0 || notATodo > 0 }
    public var leadText: String {
        guard let hours = medianLeadHours else { return "没有可计算的截止时间" }
        if hours < 1 { return "\(Int((hours * 60).rounded())) 分钟" }
        return String(format: "%.1f 小时", hours)
    }
    public var recallText: String {
        recall.map { "\(Int(($0 * 100).rounded()))%" } ?? "暂无可计算的比例"
    }
    /// 「11月9日起」 of 「2026-11-09」; the date as it came when it is not one.
    public var startText: String {
        let parts = weekStart.split(separator: "-").compactMap { Int($0) }
        return parts.count == 3 ? "\(parts[1])月\(parts[2])日起" : weekStart
    }
    /// The week in one line under its count, only what there is to say: 「提前 26.5 小时 ·
    /// 晚了 1 条 · 你补了 1 条 · 抓到 75%」. A table of six columns did not fit a phone.
    public var detail: String {
        guard hasData else { return "这周没有数据" }
        var parts: [String] = []
        if medianLeadHours != nil { parts.append("提前 \(leadText)") }
        if late > 0 { parts.append("晚了 \(late) 条") }
        if missed > 0 { parts.append("你补了 \(missed) 条") }
        if notATodo > 0 { parts.append("\(notATodo) 条不是待办") }
        if recall != nil { parts.append("抓到 \(recallText)") }
        return parts.joined(separator: " · ")
    }
    public var summary: String {
        guard hasData else { return "这周没有数据" }
        var parts = ["这周从消息里抓到 \(caught) 条待办", "你自己补了 \(missed) 条"]
        if recall != nil { parts.append("抓到的比例为\(recallText)") }
        if medianLeadHours != nil { parts.append("通常提前 \(leadText)发现") }
        if withDue > 0 || late > 0 { parts.append("\(late) 条发现时已经晚了") }
        let text = parts.joined(separator: "，") + "。"
        return text
    }
}

/// A record of a submitted inbox item belongs to this device: the inbox API does not return a chat line.
public struct InboxReceipt: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    public var text: String
    public var date: Date
    public var waiting: Bool
    public init(id: String, text: String, date: Date, waiting: Bool = true) {
        self.id = id
        self.text = text
        self.date = date
        self.waiting = waiting
    }
    public var message: BotMessage { BotMessage(id: "inbox-\(id)", author: .me, date: date, text: text) }
    public func finished(by runs: [Run]) -> Bool {
        // A run may have started just before acceptance, when the inbox joined an existing job.
        runs.contains {
            $0.job == "extract_todos" && $0.status != .running && ($0.finishedAt ?? .distantPast) >= date
        }
    }
}
