import Foundation

public enum ReachKind: String, Codable, CaseIterable, Sendable {
    case morning, unsure, question, departure

    public var title: String {
        switch self {
        case .morning: return "早上的简报"
        case .unsure: return "有条目要你确认"
        case .question: return "有人问你问题"
        case .departure: return "明早出门提醒"
        }
    }
}

public struct ReachPreferences: Codable, Equatable, Sendable {
    public var morning = true
    public var unsure = true
    public var question = true
    public var departure = false
    public var hour = 7
    public var minute = 30
    public init() {}
    public subscript(_ kind: ReachKind) -> Bool {
        get {
            switch kind {
            case .morning: return morning
            case .unsure: return unsure
            case .question: return question
            case .departure: return departure
            }
        }
        set {
            switch kind {
            case .morning: morning = newValue
            case .unsure: unsure = newValue
            case .question: question = newValue
            case .departure: departure = newValue
            }
        }
    }
    public static let key = "phone.reach.preferences"
    public static func load(_ defaults: UserDefaults = .standard) -> Self {
        defaults.data(forKey: key).flatMap { try? JSONDecoder().decode(Self.self, from: $0) } ?? Self()
    }
    public func save(_ defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.key) }
    }
}

public enum PhoneDestination: Equatable, Sendable {
    case dashboard
    case bot(String)

    public var url: URL {
        var parts = URLComponents()
        parts.scheme = "aemeath"
        parts.host = "open"
        if case .bot(let id) = self { parts.queryItems = [URLQueryItem(name: "bot", value: id)] }
        return parts.url!
    }
    public init?(url: URL) {
        guard url.scheme == "aemeath", url.host == "open",
            let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else { return nil }
        if let id = parts.queryItems?.first(where: { $0.name == "bot" })?.value, !id.isEmpty {
            self = .bot(id)
        } else {
            self = .dashboard
        }
    }
}

public struct ReachNotice: Equatable, Sendable {
    public var id: String
    public var kind: ReachKind
    public var title: String
    public var body: String
    public var destination: PhoneDestination
    public var fireAt: Date?
    public var questionID: String?
    public var replies: [String] = []
    /// The things one notification stands for (several 「还不确定」 items in one), each
    /// remembered with it so none of them is announced again.
    public var covers: [String] = []
}

/// Persist only successfully submitted immediate notices. Weather uses one replaceable,
/// date-specific system request instead, so a newer forecast updates the pending notice.
public struct ReachLedger: Codable, Sendable {
    public private(set) var sent: [String: Date] = [:]
    public init() {}
    public func contains(_ id: String) -> Bool { sent[id] != nil }
    public mutating func record(_ id: String, at date: Date) {
        sent = sent.filter { date.timeIntervalSince($0.value) < 32 * 86400 }
        sent[id] = date
    }
    public static func load(_ defaults: UserDefaults, key: String) -> Self {
        defaults.data(forKey: key).flatMap { try? JSONDecoder().decode(Self.self, from: $0) } ?? Self()
    }
    public func save(_ defaults: UserDefaults, key: String) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: key) }
    }
}

public struct ReachInput: Sendable {
    public var today: Today?
    public var reports: [HealthReport]
    public var chats: [String: [ChatMessage]]
    public var names: [String: String]
    public init(
        today: Today?, reports: [HealthReport] = [], chats: [String: [ChatMessage]] = [:], names: [String: String] = [:]
    ) {
        self.today = today
        self.reports = reports
        self.chats = chats
        self.names = names
    }
}

public enum ReachPolicy {
    /// Stable across launches; Swift's randomized Hasher must never be used for identities.
    public static func identity(_ text: String) -> String {
        let normalized = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let hash = normalized.utf8.reduce(UInt64(14_695_981_039_346_656_037)) { ($0 ^ UInt64($1)) &* 1_099_511_628_211 }
        return String(hash, radix: 16)
    }

    public static func firstContent(_ text: String?) -> String? {
        guard let text else { return nil }
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.isEmpty else { continue }
            if line.hasPrefix("**"), line.hasSuffix("**"), line.dropFirst(2).dropLast(2).contains("**") == false {
                continue
            }
            let cleaned = line.trimmingCharacters(in: CharacterSet(charactersIn: "#*- >"))
                .replacingOccurrences(of: "**", with: "")
            guard !cleaned.isEmpty else { continue }
            return String(cleaned.prefix(180))
        }
        return nil
    }

    public static func notices(
        _ input: ReachInput, preferences: ReachPreferences, ledger: ReachLedger,
        now: Date, calendar: Calendar, botPreferences: BotPreferences = BotPreferences()
    ) -> [ReachNotice] {
        let day = DueText.format(now, "yyyy-MM-dd", calendar)
        let fresh = input.today?.date == day ? input.today : nil
        var result: [ReachNotice] = []
        if preferences.morning {
            if let report = input.reports.filter({ $0.date == day && $0.kind == .morning && $0.status == .ready })
                .max(by: { $0.updatedAt < $1.updatedAt }),
                let body = firstContent(report.title) ?? firstContent(report.body)
            {
                result.append(
                    .init(
                        id: "morning-health-\(day)", kind: .morning, title: botPreferences.displayName(for: "health", fallback: "健康") + "的晨间简报到了", body: body,
                        destination: .bot("health")))
            }
            // The morning brief is known by its shape (a part 「天气」), not by its card's title.
            if let card = fresh?.cards.first(where: {
                $0.type == .text && $0.data["text"]?.stringValue.map(DashboardEdit.isBrief) == true
            }),
                let body = firstContent(card.data["text"]?.stringValue)
            {
                result.append(
                    .init(
                        id: "morning-assistant-\(day)", kind: .morning, title: botPreferences.displayName(for: Bot.assistantID, fallback: "助理") + "的今日提醒到了", body: body,
                        destination: .dashboard))
            }
        }
        if preferences.unsure, let fresh {
            // One notification for everything new, not one each: five lines the Agent could not
            // settle used to be five banners at once.
            let items = DashboardEdit.unsure(in: fresh.summary).compactMap { item in
                firstContent(item).map { (id: "unsure-\(day)-\(identity(item))", body: $0) }
            }.filter { !ledger.contains($0.id) }
            if let first = items.first {
                let ids = items.map(\.id)
                result.append(
                    .init(
                        id: "unsure-\(day)-" + identity(ids.joined(separator: "|")), kind: .unsure,
                        title: items.count == 1 ? "有一件事想请你确认" : "有 \(items.count) 件事想请你确认",
                        body: items.count == 1 ? first.body : first.body + "（还有 \(items.count - 1) 件）",
                        destination: .dashboard, covers: ids))
            }
        }
        if preferences.question {
            for id in input.chats.keys.sorted() {
                guard let question = BotChat.replyQuestion(input.chats[id] ?? [], now: now, calendar: calendar),
                    let body = firstContent(question.text)
                else { continue }
                let replies = Array(
                    (question.replies ?? []).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
                        .prefix(4))
                guard !replies.isEmpty else { continue }
                result.append(
                    .init(
                        id: "question-\(identity(id + ":" + question.id))", kind: .question,
                        title: botPreferences.displayName(for: id, fallback: input.names[id] ?? "助手") + "想问问你", body: body,
                        destination: .bot(id), questionID: question.id, replies: replies))
            }
        }
        if preferences.departure, let fresh {
            let forecasts = fresh.cards.filter { $0.plugin == "weather" && $0.type == .stat }
            func departure(on day: Date, from card: Card?) -> ReachNotice? {
                guard
                    let fire = calendar.date(
                        bySettingHour: min(max(preferences.hour, 0), 23), minute: min(max(preferences.minute, 0), 59),
                        second: 0, of: day), fire > now, let card, case .stat(let stat) = card.content
                else { return nil }
                let body = [stat.value + (stat.unit ?? "°C"), firstContent(stat.label)].compactMap { $0 }.joined(
                    separator: " · ")
                return .init(
                    id: "departure-\(DueText.format(fire, "yyyy-MM-dd", calendar))", kind: .departure,
                    title: "早上出门前看一眼", body: body, destination: .dashboard, fireAt: fire)
            }
            // This morning's, when the App wakes before its time (it was not open last night, or
            // the forecast changed overnight), from today's own forecast; then tomorrow's.
            if let notice = departure(on: now, from: forecasts.first { $0.title != "明天" }) { result.append(notice) }
            if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now),
                let notice = departure(on: tomorrow, from: forecasts.first { $0.title == "明天" })
            {
                result.append(notice)
            }
        }
        var ids = Set<String>()
        return result.filter { ids.insert($0.id).inserted && ($0.fireAt != nil || !ledger.contains($0.id)) }
    }

    /// Whether a notice is only remembered, not shown, because the user is looking at it: with
    /// the App open, a brief or an item to confirm is on 仪表盘 already (a banner at three in
    /// the afternoon saying the morning brief 「到了」 was noise), and a Bot's question is on
    /// its own page when that page is the open one. A question from another Bot still shows,
    /// as in any chat app. A notice set for later (tomorrow's weather) is never silent.
    public static func staysSilent(_ notice: ReachNotice, appActive: Bool, openPage: String?) -> Bool {
        guard appActive, notice.fireAt == nil else { return false }
        if notice.kind == .question, case .bot(let id) = notice.destination { return id == openPage }
        return true
    }

    public static func reply(_ offered: String, questionID: String, chat: [ChatMessage], now: Date, calendar: Calendar)
        -> String?
    {
        guard let question = BotChat.replyQuestion(chat, now: now, calendar: calendar), question.id == questionID,
            question.replies?.contains(offered) == true
        else { return nil }
        return offered
    }

    /// A button is still valid only while that exact question remains the latest line.
    public static func reply(_ index: Int, questionID: String, chat: [ChatMessage], now: Date, calendar: Calendar)
        -> String?
    {
        guard let question = BotChat.replyQuestion(chat, now: now, calendar: calendar), question.id == questionID,
            let replies = question.replies, replies.indices.contains(index)
        else { return nil }
        return replies[index]
    }
}
