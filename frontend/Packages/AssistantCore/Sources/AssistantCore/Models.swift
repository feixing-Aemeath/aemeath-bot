import Foundation

// Data structures of docs/api.md (v1). Field names follow the API through CodingKeys;
// exact bounds and enums are in docs/openapi.json.

public enum TodoStatus: String, Codable, CaseIterable, Sendable {
    case pending, completed, deleted
}

public enum SyncStatus: String, Codable, Sendable {
    case pending, synced
}

/// One cited message of a todo: which plugin, chat and sender it came from.
public struct TodoSource: Codable, Hashable, Sendable {
    public var messageID: String
    public var plugin: String
    public var chatName: String
    public var sender: String
    public var sentAt: Date

    public init(messageID: String, plugin: String, chatName: String, sender: String, sentAt: Date) {
        self.messageID = messageID
        self.plugin = plugin
        self.chatName = chatName
        self.sender = sender
        self.sentAt = sentAt
    }

    enum CodingKeys: String, CodingKey {
        case plugin, sender
        case messageID = "message_id"
        case chatName = "chat_name"
        case sentAt = "sent_at"
    }
}

public struct Todo: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    public var title: String
    public var notes: String
    /// Local midnight of the day when `hasTime` is false.
    public var dueAt: Date?
    public var hasTime: Bool
    /// Comma-separated when the same task came from several sources.
    public var sourcePlugin: String
    public var sourceRefs: [String]
    public var sourceExcerpt: String
    public var confidence: Double
    public var status: TodoStatus
    public var dedupKey: String
    public var reminderID: String?
    public var calendarEventID: String?
    public var createdBy: String
    public var createdAt: Date
    public var updatedAt: Date
    public var syncStatus: SyncStatus
    public var syncError: String?
    /// Read-only provenance from the API; absent in todos inside plugin cards.
    public var sources: [TodoSource]?

    enum CodingKeys: String, CodingKey {
        case id, title, notes, confidence, status, sources
        case dueAt = "due_at"
        case hasTime = "has_time"
        case sourcePlugin = "source_plugin"
        case sourceRefs = "source_refs"
        case sourceExcerpt = "source_excerpt"
        case dedupKey = "dedup_key"
        case reminderID = "reminder_id"
        case calendarEventID = "calendar_event_id"
        case createdBy = "created_by"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case syncStatus = "sync_status"
        case syncError = "sync_error"
    }

    public init(
        id: String, title: String, notes: String = "", dueAt: Date? = nil, hasTime: Bool = false,
        sourcePlugin: String, sourceRefs: [String] = [], sourceExcerpt: String = "",
        confidence: Double = 1, status: TodoStatus = .pending, dedupKey: String = "",
        reminderID: String? = nil, calendarEventID: String? = nil, createdBy: String = "agent",
        createdAt: Date, updatedAt: Date, syncStatus: SyncStatus = .synced,
        syncError: String? = nil, sources: [TodoSource]? = nil
    ) {
        self.id = id
        self.title = title
        self.notes = notes
        self.dueAt = dueAt
        self.hasTime = hasTime
        self.sourcePlugin = sourcePlugin
        self.sourceRefs = sourceRefs
        self.sourceExcerpt = sourceExcerpt
        self.confidence = confidence
        self.status = status
        self.dedupKey = dedupKey
        self.reminderID = reminderID
        self.calendarEventID = calendarEventID
        self.createdBy = createdBy
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.syncStatus = syncStatus
        self.syncError = syncError
        self.sources = sources
    }
}

/// `{items, total}` list responses.
public struct Page<Item: Codable & Sendable>: Codable, Sendable {
    public var items: [Item]
    public var total: Int
}

extension Page: Equatable where Item: Equatable {}

public struct Digest: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    /// Calendar day, `YYYY-MM-DD`.
    public var date: String
    public var plugin: String
    public var summary: String
    public var createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, date, plugin, summary
        case createdAt = "created_at"
    }
}

public enum RunStatus: String, Codable, Sendable {
    case running, succeeded, failed, cancelled
}

public struct Run: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    public var job: String
    public var harness: String?
    public var startedAt: Date
    public var finishedAt: Date?
    public var status: RunStatus
    /// A fixed error category, never CLI output.
    public var error: String?
    public var attempt: Int

    enum CodingKeys: String, CodingKey {
        case id, job, harness, status, error, attempt
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

public struct AgentStatus: Codable, Hashable, Sendable {
    /// Only means the CLI was found; `authentication` stays "unchecked".
    public var available: Bool
    public var authentication: String
    /// Models a chat line may pick for this Agent; empty when the backend does not know them
    /// (or is older than this field).
    public var models: [AgentModel]

    public init(available: Bool, authentication: String = "unchecked", models: [AgentModel] = []) {
        self.available = available
        self.authentication = authentication
        self.models = models
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        available = try container.decode(Bool.self, forKey: .available)
        authentication = try container.decodeIfPresent(String.self, forKey: .authentication) ?? "unchecked"
        models = try container.decodeIfPresent([AgentModel].self, forKey: .models) ?? []
    }
}

/// One model of an Agent, e.g. Claude Code's `opus` or a Codex model.
public struct AgentModel: Codable, Hashable, Sendable {
    public var id: String
    public var name: String
    public var isDefault: Bool

    public init(id: String, name: String, isDefault: Bool = false) {
        self.id = id
        self.name = name
        self.isDefault = isDefault
    }

    enum CodingKeys: String, CodingKey {
        case id, name
        case isDefault = "default"
    }
}

public struct Status: Codable, Hashable, Sendable {
    public var online: Bool
    public var platform: String
    public var appleMode: String
    public var agents: [String: AgentStatus]
    public var lastRun: Run?
    public var pendingSync: Int

    enum CodingKeys: String, CodingKey {
        case online, platform, agents
        case appleMode = "apple_mode"
        case lastRun = "last_run"
        case pendingSync = "pending_sync"
    }
}

public struct Today: Codable, Hashable, Sendable {
    /// Calendar day in the backend's time zone, `YYYY-MM-DD`.
    public var date: String
    public var summary: String
    /// Due today or overdue, not completed.
    public var todos: [Todo]
    public var cards: [Card]

    public init(date: String, summary: String, todos: [Todo], cards: [Card]) {
        self.date = date
        self.summary = summary
        self.todos = todos
        self.cards = cards
    }
}

public enum PluginState: String, Codable, Sendable {
    case disabled, enabled, unsupported, error
}

/// `mascot` in a plugin manifest: its sticker (a file name in the stickers folder, without
/// ".png"; nil means the placeholder) and its accent color.
public struct PluginMascot: Codable, Hashable, Sendable {
    public var sticker: String?
    /// `#RRGGBB`.
    public var accent: String

    public init(sticker: String?, accent: String) {
        self.sticker = sticker
        self.accent = accent
    }
}

public struct PluginView: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var version: String
    public var icon: String
    public var description: String
    public var platforms: [String]
    public var permissions: [String]
    public var schedule: String?
    public var provides: [String]
    public var configSchema: JSONValue
    /// Null when the manifest declares none.
    public var mascot: PluginMascot?
    public var enabled: Bool
    public var config: JSONValue
    public var lastRunAt: String?
    public var state: PluginState
    public var error: String?

    enum CodingKeys: String, CodingKey {
        case id, name, version, icon, description, platforms, permissions, schedule, provides
        case enabled, config, state, error, mascot
        case configSchema = "config_schema"
        case lastRunAt = "last_run_at"
    }
}

public struct PluginPatch: Codable, Hashable, Sendable {
    public var enabled: Bool?
    /// Replaces the whole configuration.
    public var config: JSONValue?

    public init(enabled: Bool? = nil, config: JSONValue? = nil) {
        self.enabled = enabled
        self.config = config
    }
}

public struct RuntimeSettings: Codable, Hashable, Sendable {
    public var timezone: String
    public var sourceCron: String
    public var extractionCron: String
    public var confidenceThreshold: Double
    public var harnessOrder: [String]
    public var jobHarness: [String: String]
    public var agentTimeout: Int
    public var retryAttempts: Int
    public var retryBaseSeconds: Double

    enum CodingKeys: String, CodingKey {
        case timezone
        case sourceCron = "source_cron"
        case extractionCron = "extraction_cron"
        case confidenceThreshold = "confidence_threshold"
        case harnessOrder = "harness_order"
        case jobHarness = "job_harness"
        case agentTimeout = "agent_timeout"
        case retryAttempts = "retry_attempts"
        case retryBaseSeconds = "retry_base_seconds"
    }
}

/// Only the set fields are sent; at least one is required by the API.
public struct SettingsPatch: Codable, Hashable, Sendable {
    public var timezone: String?
    public var sourceCron: String?
    public var extractionCron: String?
    public var confidenceThreshold: Double?
    public var harnessOrder: [String]?
    public var jobHarness: [String: String]?
    public var agentTimeout: Int?
    public var retryAttempts: Int?
    public var retryBaseSeconds: Double?

    public init() {}

    enum CodingKeys: String, CodingKey {
        case timezone
        case sourceCron = "source_cron"
        case extractionCron = "extraction_cron"
        case confidenceThreshold = "confidence_threshold"
        case harnessOrder = "harness_order"
        case jobHarness = "job_harness"
        case agentTimeout = "agent_timeout"
        case retryAttempts = "retry_attempts"
        case retryBaseSeconds = "retry_base_seconds"
    }
}

public struct HealthSample: Codable, Hashable, Sendable {
    public var id: String
    public var type: String
    public var value: Double
    public var unit: String
    public var startAt: Date
    public var endAt: Date
    public var source: String

    public init(
        id: String, type: String, value: Double, unit: String, startAt: Date, endAt: Date,
        source: String
    ) {
        self.id = id
        self.type = type
        self.value = value
        self.unit = unit
        self.startAt = startAt
        self.endAt = endAt
        self.source = source
    }

    enum CodingKeys: String, CodingKey {
        case id, type, value, unit, source
        case startAt = "start_at"
        case endAt = "end_at"
    }
}

public struct HealthBatch: Codable, Hashable, Sendable {
    public var samples: [HealthSample]

    public init(samples: [HealthSample]) {
        self.samples = samples
    }
}

public struct HealthAccepted: Codable, Hashable, Sendable {
    public var accepted: Int
    public var duplicates: Int
}

public struct JobAccepted: Codable, Hashable, Sendable {
    public var jobID: String
    public var status: String

    enum CodingKeys: String, CodingKey {
        case status
        case jobID = "job_id"
    }
}

public enum HealthReportKind: String, Codable, CaseIterable, Sendable {
    case morning, weekly
}

public enum HealthReportStatus: String, Codable, Sendable {
    case pending, ready, failed
}

/// A morning brief or weekly report (`GET /api/v1/health/reports`). Title and body are set
/// once the report is ready.
public struct HealthReport: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    /// Calendar day, `YYYY-MM-DD`.
    public var date: String
    public var kind: HealthReportKind
    public var status: HealthReportStatus
    public var title: String?
    /// Markdown.
    public var body: String?
    /// A fixed error category when failed.
    public var error: String?
    public var harness: String?
    public var createdAt: Date
    public var updatedAt: Date

    public init(
        id: String, date: String, kind: HealthReportKind, status: HealthReportStatus,
        title: String? = nil, body: String? = nil, error: String? = nil, harness: String? = nil,
        createdAt: Date, updatedAt: Date
    ) {
        self.id = id
        self.date = date
        self.kind = kind
        self.status = status
        self.title = title
        self.body = body
        self.error = error
        self.harness = harness
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, date, kind, status, title, body, error, harness
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public enum ChatAuthor: String, Codable, Sendable {
    case me, bot
}

public enum ChatStatus: String, Codable, Sendable {
    /// The Agent is still writing the reply; `text` is empty.
    case pending
    case ready, failed
}

/// One line of a Bot chat (`/api/v1/bots/{bot_id}/messages`).
public struct ChatMessage: Codable, Hashable, Identifiable, Sendable {
    public var id: String
    public var botID: String
    public var author: ChatAuthor
    /// Markdown when the Bot wrote it.
    public var text: String
    public var status: ChatStatus
    /// A fixed category when failed, e.g. `all_harnesses_failed`, `interrupted`.
    public var error: String?
    public var createdAt: Date
    /// When a reply was written.
    public var updatedAt: Date

    public init(
        id: String, botID: String, author: ChatAuthor, text: String, status: ChatStatus,
        error: String? = nil, createdAt: Date, updatedAt: Date
    ) {
        self.id = id
        self.botID = botID
        self.author = author
        self.text = text
        self.status = status
        self.error = error
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, author, text, status, error
        case botID = "bot_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct ChatSend: Encodable, Sendable {
    public var text: String
    /// What the App calls the Bot, e.g. 作业Bot.
    public var botName: String?
    /// The Agent (and its model) to answer first; nil for the backend's usual order.
    public var harness: String?
    public var model: String?

    public init(text: String, botName: String?, choice: ChatChoice = .automatic) {
        self.text = text
        self.botName = botName
        harness = choice.harness
        model = choice.harness == nil ? nil : choice.model
    }

    enum CodingKeys: String, CodingKey {
        case text, harness, model
        case botName = "bot_name"
    }
}

/// The line that was sent, and the reply that is now pending.
public struct ChatAccepted: Codable, Hashable, Sendable {
    public var message: ChatMessage
    public var reply: ChatMessage
}
