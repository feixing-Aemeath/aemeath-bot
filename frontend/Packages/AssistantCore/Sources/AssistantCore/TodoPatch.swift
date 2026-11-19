import Foundation

/// `PATCH /api/v1/todos/{id}`: only the fields that change are sent. `due_at` has three
/// states: leave it, set it, or clear it (sent as JSON null, which also removes the
/// calendar event).
public struct TodoPatch: Encodable, Hashable, Sendable {
    public enum DueChange: Hashable, Sendable {
        case set(Date)
        case clear
    }

    public var title: String?
    public var notes: String?
    public var due: DueChange?
    public var hasTime: Bool?
    public var reason: TodoDeleteReason?
    public var status: TodoStatus?

    public init(
        title: String? = nil, notes: String? = nil, due: DueChange? = nil, hasTime: Bool? = nil,
        status: TodoStatus? = nil, reason: TodoDeleteReason? = nil
    ) {
        self.title = title
        self.notes = notes
        self.due = due
        self.hasTime = hasTime
        self.status = status
        self.reason = reason
    }

    static func validText(title: String, notes: String) -> Bool {
        !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && title.unicodeScalars.count <= 500 && notes.unicodeScalars.count <= 50000
    }

    public static func complete() -> TodoPatch { TodoPatch(status: .completed) }
    /// Deleting in the App also deletes the reminder.
    public static func delete(reason: TodoDeleteReason? = nil) -> TodoPatch { TodoPatch(status: .deleted, reason: reason) }

    public var isEmpty: Bool {
        title == nil && notes == nil && due == nil && hasTime == nil && status == nil
    }

    enum CodingKeys: String, CodingKey {
        case title, notes, status, reason
        case dueAt = "due_at"
        case hasTime = "has_time"
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(title, forKey: .title)
        try container.encodeIfPresent(notes, forKey: .notes)
        try container.encodeIfPresent(hasTime, forKey: .hasTime)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encodeIfPresent(reason, forKey: .reason)
        switch due {
        case .set(let date): try container.encode(date, forKey: .dueAt)
        case .clear: try container.encodeNil(forKey: .dueAt)
        case nil: break
        }
    }
}

/// The edit form of a todo. `patch(from:)` sends only what changed; a date-only deadline is
/// stored at local midnight, as the API expects.
public struct TodoDraft: Equatable, Sendable {
    public var title: String
    public var notes: String
    public var hasDue: Bool
    public var due: Date
    public var hasTime: Bool

    public init(_ todo: Todo, now: Date = Date(), calendar: Calendar = DueText.calendar()) {
        title = todo.title
        notes = todo.notes
        hasDue = todo.dueAt != nil
        hasTime = todo.hasTime
        due = todo.dueAt ?? calendar.date(bySettingHour: 18, minute: 0, second: 0, of: now) ?? now
    }

    public var isValid: Bool {
        TodoPatch.validText(title: title.trimmingCharacters(in: .whitespacesAndNewlines), notes: notes)
    }

    public func patch(from todo: Todo, calendar: Calendar = DueText.calendar()) -> TodoPatch {
        var patch = TodoPatch()
        let title = self.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if title != todo.title { patch.title = title }
        if notes != todo.notes { patch.notes = notes }
        if hasDue {
            if due != todo.dueAt || hasTime != todo.hasTime {
                let date = hasTime ? due : calendar.startOfDay(for: due)
                patch.due = .set(date)
                patch.hasTime = hasTime
            }
        } else if todo.dueAt != nil {
            patch.due = .clear
        }
        return patch
    }
}
