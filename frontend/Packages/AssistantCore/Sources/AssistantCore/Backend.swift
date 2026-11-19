import Foundation

/// One page of a Bot's chat, oldest first. `nextBefore` reads the page before it; nil at the start.
public struct ChatHistory: Equatable, Sendable {
    public var items: [ChatMessage]
    public var nextBefore: String?

    public init(items: [ChatMessage], nextBefore: String? = nil) {
        self.items = items
        self.nextBefore = nextBefore
    }
}

/// How many runs went well, and the newest of them, counted by the backend.
public struct RunCount: Equatable, Sendable {
    public var total: Int
    public var latest: Run?

    public init(total: Int, latest: Run? = nil) {
        self.total = total
        self.latest = latest
    }
}

/// Everything the App screens read and change. `LiveBackend` talks to the Mac over HTTP;
/// `MockBackend` keeps invented data in memory so the screens work before the Mac exists.
public protocol Backend: Sendable {
    func status() async throws -> Status
    func today() async throws -> Today
    /// Neither deleted nor cancelled when `status` is nil.
    func todos(status: TodoStatus?) async throws -> [Todo]
    func updateTodo(id: String, _ patch: TodoPatch) async throws -> Todo
    func createTodo(_ todo: TodoNew) async throws -> TodoCreated
    func submitInbox(_ item: InboxItem) async throws -> InboxAccepted
    func todoSources(id: String) async throws -> TodoSourceTexts
    func openChat(pluginID: String, chatName: String) async throws -> OpenChatAccepted
    func extractionStats(weeks: Int) async throws -> ExtractionStats
    func digests() async throws -> [Digest]
    /// Newest first.
    func runs() async throws -> [Run]
    /// The runs of `job` that succeeded and started from `since` up to `until`.
    func succeeded(job: String, since: Date, until: Date) async throws -> RunCount
    func runJob(_ jobID: String) async throws -> JobAccepted
    func plugins() async throws -> [PluginView]
    func updatePlugin(id: String, _ patch: PluginPatch) async throws -> PluginView
    func cards(pluginID: String) async throws -> [Card]
    /// Newest first.
    func healthReports() async throws -> [HealthReport]
    /// Health samples, e.g. a readiness check-in (`ReadinessCheckin`).
    func uploadHealth(_ batch: HealthBatch) async throws -> HealthAccepted
    func botPreferences() async throws -> BotPreferenceSnapshot
    func updateBotPreferences(_ entries: [String: BotPreferences.Entry], expectedVersion: Int) async throws -> BotPreferenceSnapshot
    func settings() async throws -> RuntimeSettings
    func updateSettings(_ patch: SettingsPatch) async throws -> RuntimeSettings
    /// A Bot's newest chat lines, or those before the cursor of a page already read.
    func chat(botID: String, before: String?) async throws -> ChatHistory
    func sendChat(botID: String, text: String, botName: String?, choice: ChatChoice) async throws -> ChatAccepted
    /// The knowledge base: its folders and who may read each (docs/knowledge.md).
    func knowledge() async throws -> KnowledgeOverview
    func updateKnowledge(_ patch: KnowledgePatch) async throws -> KnowledgeOverview
    func setReaders(_ readers: [String], area path: String) async throws -> KnowledgeArea
    func createTopic(name: String, readers: [String]?) async throws -> KnowledgeArea
    /// Every file under `path`, newest first.
    func knowledgeFiles(path: String) async throws -> KnowledgeFiles
    func knowledgeFile(path: String) async throws -> KnowledgeFile
    /// The original bytes, to show a PDF or a picture.
    func knowledgeOriginal(path: String) async throws -> Data
    func searchKnowledge(_ query: String) async throws -> [KnowledgeSearchResult]
    /// A new source in a topic; a file of that name already there is a conflict, never replaced.
    func uploadKnowledge(path: String, data: Data, contentType: String) async throws -> KnowledgeEntry
}

/// The real backend through `APIClient`, with list sizes that fit one screen.
public struct LiveBackend: Backend {
    public let client: APIClient

    public init(client: APIClient) {
        self.client = client
    }

    public func status() async throws -> Status { try await client.status() }

    public func today() async throws -> Today { try await client.today() }

    public func todos(status: TodoStatus?) async throws -> [Todo] {
        var items: [Todo] = []
        while true {
            let page = try await client.todos(status: status, limit: 200, offset: items.count)
            items += page.items
            if items.count >= page.total { return items }
            guard !page.items.isEmpty else { throw APIError.invalidResponse }
        }
    }

    public func updateTodo(id: String, _ patch: TodoPatch) async throws -> Todo {
        try await client.updateTodo(id: id, patch)
    }

    public func createTodo(_ todo: TodoNew) async throws -> TodoCreated { try await client.createTodo(todo) }
    public func submitInbox(_ item: InboxItem) async throws -> InboxAccepted { try await client.submitInbox(item) }
    public func todoSources(id: String) async throws -> TodoSourceTexts { try await client.todoSources(id: id) }
    public func openChat(pluginID: String, chatName: String) async throws -> OpenChatAccepted {
        try await client.openChat(pluginID: pluginID, chatName: chatName)
    }
    public func extractionStats(weeks: Int) async throws -> ExtractionStats { try await client.extractionStats(weeks: weeks) }

    public func digests() async throws -> [Digest] {
        try await client.digests(limit: 100).items
    }

    public func runs() async throws -> [Run] {
        try await client.runs(limit: 50).items
    }

    public func succeeded(job: String, since: Date, until: Date) async throws -> RunCount {
        let page = try await client.runs(
            limit: 1, job: job, status: .succeeded, since: since, until: until)
        return RunCount(total: page.total, latest: page.items.first)
    }

    public func runJob(_ jobID: String) async throws -> JobAccepted {
        try await client.runJob(jobID)
    }

    public func plugins() async throws -> [PluginView] { try await client.plugins() }

    public func updatePlugin(id: String, _ patch: PluginPatch) async throws -> PluginView {
        try await client.updatePlugin(id: id, patch)
    }

    public func cards(pluginID: String) async throws -> [Card] {
        try await client.cards(pluginID: pluginID)
    }

    public func healthReports() async throws -> [HealthReport] {
        try await client.healthReports(limit: 30).items
    }

    public func uploadHealth(_ batch: HealthBatch) async throws -> HealthAccepted {
        try await client.uploadHealth(batch)
    }

    public func botPreferences() async throws -> BotPreferenceSnapshot { try await client.botPreferences() }

    public func updateBotPreferences(_ entries: [String: BotPreferences.Entry], expectedVersion: Int) async throws -> BotPreferenceSnapshot {
        try await client.updateBotPreferences(entries, expectedVersion: expectedVersion)
    }

    public func settings() async throws -> RuntimeSettings { try await client.settings() }

    public func updateSettings(_ patch: SettingsPatch) async throws -> RuntimeSettings {
        try await client.updateSettings(patch)
    }

    public func chat(botID: String, before: String?) async throws -> ChatHistory {
        let page = try await client.chatMessages(botID: botID, limit: 100, before: before)
        return ChatHistory(items: page.items.reversed(), nextBefore: page.nextBefore)
    }

    public func sendChat(botID: String, text: String, botName: String?, choice: ChatChoice) async throws
        -> ChatAccepted
    {
        try await client.sendChat(botID: botID, text: text, botName: botName, choice: choice)
    }

    public func knowledge() async throws -> KnowledgeOverview { try await client.knowledge() }

    public func updateKnowledge(_ patch: KnowledgePatch) async throws -> KnowledgeOverview {
        try await client.updateKnowledge(patch)
    }

    public func setReaders(_ readers: [String], area path: String) async throws -> KnowledgeArea {
        try await client.setReaders(readers, area: path)
    }

    public func createTopic(name: String, readers: [String]?) async throws -> KnowledgeArea {
        try await client.createTopic(name: name, readers: readers)
    }

    public func knowledgeFiles(path: String) async throws -> KnowledgeFiles {
        try await client.knowledgeFiles(path: path)
    }

    public func knowledgeFile(path: String) async throws -> KnowledgeFile {
        try await client.knowledgeFile(path: path)
    }

    public func knowledgeOriginal(path: String) async throws -> Data {
        try await client.knowledgeOriginal(path: path)
    }

    public func searchKnowledge(_ query: String) async throws -> [KnowledgeSearchResult] {
        try await client.searchKnowledge(query)
    }

    public func uploadKnowledge(path: String, data: Data, contentType: String) async throws -> KnowledgeEntry {
        try await client.uploadKnowledge(path: path, data: data, contentType: contentType)
    }
}

extension Backend {
    /// A Bot's newest chat lines, oldest first.
    public func chat(botID: String) async throws -> [ChatMessage] {
        try await chat(botID: botID, before: nil).items
    }
}
