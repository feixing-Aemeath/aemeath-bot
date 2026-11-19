import Foundation

public struct MissedSource: Codable, Hashable, Sendable {
    public var plugin: String
    public var chatName: String
    public init(plugin: String, chatName: String) {
        self.plugin = plugin
        self.chatName = chatName
    }
    enum CodingKeys: String, CodingKey {
        case plugin
        case chatName = "chat_name"
    }
}

public struct TodoNew: Codable, Hashable, Sendable {
    public var title: String
    public var notes: String
    public var dueAt: Date?
    public var hasTime: Bool
    public var missed: Bool
    public var missedSource: MissedSource?
    public init(
        title: String, notes: String = "", dueAt: Date? = nil, hasTime: Bool = false,
        missed: Bool = false, missedSource: MissedSource? = nil
    ) {
        self.title = title
        self.notes = notes
        self.dueAt = dueAt
        self.hasTime = hasTime
        self.missed = missed
        self.missedSource = missed ? missedSource : nil
    }
    public var isValid: Bool {
        TodoPatch.validText(title: title, notes: notes) && (!hasTime || dueAt != nil)
    }
    enum CodingKeys: String, CodingKey {
        case title, notes, missed
        case dueAt = "due_at"
        case hasTime = "has_time"
        case missedSource = "missed_source"
    }
}

public struct TodoCreated: Sendable {
    public var todo: Todo
    public var duplicate: Bool
    public init(todo: Todo, duplicate: Bool) {
        self.todo = todo
        self.duplicate = duplicate
    }
}

public struct InboxItem: Codable, Hashable, Sendable {
    public var text: String
    public var note: String?
    public var sourceApp: String?
    public var chatName: String?
    public var missed: Bool
    public init(
        text: String, note: String? = nil, sourceApp: String? = nil, chatName: String? = nil,
        missed: Bool = false
    ) {
        self.text = text
        self.note = note
        self.sourceApp = sourceApp
        self.chatName = chatName
        self.missed = missed
    }
    public var isValid: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && text.unicodeScalars.count <= ShareContent.textLimit
            && (note?.unicodeScalars.count ?? 0) <= ShareContent.noteLimit
            && (sourceApp?.unicodeScalars.count ?? 0) <= 40 && (chatName?.unicodeScalars.count ?? 0) <= 80
    }
    public var chatText: String {
        var result = "转给你一段消息"
        let place = [sourceApp, chatName].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
        if !place.isEmpty { result += "（\(place)）" }
        result += "：\n" + String(text.prefix(300)) + (text.count > 300 ? "…" : "")
        if let note, !note.isEmpty { result += "\n备注：\(note)" }
        if missed { result += "\n这是助手漏掉的。" }
        return result
    }
    enum CodingKeys: String, CodingKey {
        case text, note, missed
        case sourceApp = "source_app"
        case chatName = "chat_name"
    }
}

public struct InboxAccepted: Codable, Sendable {
    public var messageID: String
    public var jobStarted: Bool
    public init(messageID: String, jobStarted: Bool) {
        self.messageID = messageID
        self.jobStarted = jobStarted
    }
    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case jobStarted = "job_started"
    }
}

public struct TodoSourceTexts: Codable, Hashable, Sendable {
    public var items: [TodoSourceTextItem]
    public init(items: [TodoSourceTextItem]) { self.items = items }
}
public struct TodoSourceTextItem: Codable, Hashable, Identifiable, Sendable {
    public var messageID: String
    public var plugin: String
    public var chatName: String
    public var sender: String
    public var sentAt: Date
    public var text: String
    public var id: String { messageID }
    public init(
        messageID: String, plugin: String, chatName: String, sender: String, sentAt: Date, text: String
    ) {
        self.messageID = messageID
        self.plugin = plugin
        self.chatName = chatName
        self.sender = sender
        self.sentAt = sentAt
        self.text = text
    }
    enum CodingKeys: String, CodingKey {
        case plugin, sender, text
        case messageID = "message_id"
        case chatName = "chat_name"
        case sentAt = "sent_at"
    }
}
public struct UnsureItem: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    public var text: String
    public var plugin: String?
    public var chatName: String?
    public var canOpen: Bool
    public init(
        id: String, text: String, plugin: String? = nil, chatName: String? = nil, canOpen: Bool = false
    ) {
        self.id = id
        self.text = text
        self.plugin = plugin
        self.chatName = chatName
        self.canOpen = canOpen
    }
    enum CodingKeys: String, CodingKey {
        case id, text, plugin
        case chatName = "chat_name"
        case canOpen = "can_open"
    }
}
public struct OpenChatRequest: Codable, Sendable {
    public var chatName: String
    public init(chatName: String) { self.chatName = chatName }
    enum CodingKeys: String, CodingKey { case chatName = "chat_name" }
}
public struct OpenChatAccepted: Codable, Sendable {
    public var accepted: Bool
    public var jobStarted: Bool
    public init(accepted: Bool, jobStarted: Bool) {
        self.accepted = accepted
        self.jobStarted = jobStarted
    }
    enum CodingKeys: String, CodingKey {
        case accepted
        case jobStarted = "job_started"
    }
}
public enum TodoDeleteReason: String, Codable, CaseIterable, Sendable {
    case notATodo = "not_a_todo"
    case doneElsewhere = "done_elsewhere"
    case duplicate
    case wrongTime = "wrong_time"
    public var title: String {
        switch self {
        case .notATodo: return "不是待办"
        case .doneElsewhere: return "已经做了"
        case .duplicate: return "重复了"
        case .wrongTime: return "时间不对"
        }
    }
}
public enum ManualEntryText {
    public static let backendUpdate = "Mac 上的后端还没更新"
    public static func openError(_ error: APIError) -> String {
        switch error {
        case .screenOpen("screen_open_not_whitelisted"): return "这个会话已不在白名单里，请先到设置里检查。"
        case .screenOpen("screen_open_not_allowed"): return "这个会话没有开启读全文，请先在 Mac 上允许点开。"
        case .screenOpen("screen_open_daily_limit"): return "今天读全文的次数用完了，明天再试。"
        case .notFound: return backendUpdate + "，或这个消息来源已被移除。"
        default: return "没能开始读全文，请稍后重试。"
        }
    }
    public static func elapsed(since: Date, now: Date) -> String {
        let seconds = max(0, Int(now.timeIntervalSince(since)))
        return seconds < 60 ? "已等 \(seconds) 秒" : "已等 \(seconds / 60) 分 \(seconds % 60) 秒"
    }
    public static func needsDeleteReason(_ todo: Todo) -> Bool { todo.createdBy != "user" }
    public static func conversations(_ plugins: [PluginView]) -> [MissedSource] {
        plugins.filter { PluginConfigEdit.hasPreviewMode($0) }.flatMap { plugin in
            PluginConfigEdit.names(plugin, "whitelist").map { MissedSource(plugin: plugin.id, chatName: $0) }
        }
    }
}
extension Today {
    public var unsureItems: [UnsureItem] {
        unsure
            ?? DashboardEdit.unsure(in: summary).map {
                // Keep the entire text in a local fallback key: stable across launches, no random hash seed.
                UnsureItem(id: "legacy:\(date):\($0)", text: $0)
            }
    }
}

/// Local-only hiding, scoped to the backend's calendar date. Old days are pruned on save.
public struct UnsureDismissals: Codable, Equatable, Sendable {
    public var day: String
    public var ids: Set<String>
    public init(day: String = "", ids: Set<String> = []) {
        self.day = day
        self.ids = ids
    }
    public func contains(_ id: String, on day: String) -> Bool { self.day == day && ids.contains(id) }
    public mutating func hide(_ id: String, on day: String) {
        if self.day != day {
            self.day = day
            ids = []
        }
        ids.insert(id)
    }
}
