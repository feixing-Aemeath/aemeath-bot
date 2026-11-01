import Foundation

/// Everything the App screens read and change. `LiveBackend` talks to the Mac over HTTP;
/// `MockBackend` keeps invented data in memory so the screens work before the Mac exists.
public protocol Backend: Sendable {
    func status() async throws -> Status
    func today() async throws -> Today
    /// Not deleted when `status` is nil.
    func todos(status: TodoStatus?) async throws -> [Todo]
    func updateTodo(id: String, _ patch: TodoPatch) async throws -> Todo
    func digests() async throws -> [Digest]
    /// Newest first.
    func runs() async throws -> [Run]
    func runJob(_ jobID: String) async throws -> JobAccepted
    func plugins() async throws -> [PluginView]
    func updatePlugin(id: String, _ patch: PluginPatch) async throws -> PluginView
    func cards(pluginID: String) async throws -> [Card]
    /// Newest first.
    func healthReports() async throws -> [HealthReport]
    /// Health samples, e.g. a readiness check-in (`ReadinessCheckin`).
    func uploadHealth(_ batch: HealthBatch) async throws -> HealthAccepted
    func settings() async throws -> RuntimeSettings
    func updateSettings(_ patch: SettingsPatch) async throws -> RuntimeSettings
    /// A Bot's chat, oldest first.
    func chat(botID: String) async throws -> [ChatMessage]
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
        try await client.todos(status: status, limit: 200).items
    }

    public func updateTodo(id: String, _ patch: TodoPatch) async throws -> Todo {
        try await client.updateTodo(id: id, patch)
    }

    public func digests() async throws -> [Digest] {
        try await client.digests(limit: 100).items
    }

    public func runs() async throws -> [Run] {
        try await client.runs(limit: 50).items
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

    public func settings() async throws -> RuntimeSettings { try await client.settings() }

    public func updateSettings(_ patch: SettingsPatch) async throws -> RuntimeSettings {
        try await client.updateSettings(patch)
    }

    public func chat(botID: String) async throws -> [ChatMessage] {
        try await client.chatMessages(botID: botID, limit: 100).items.reversed()
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
