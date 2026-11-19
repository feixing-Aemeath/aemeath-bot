import Foundation

public struct ReachRefresh: Sendable {
    public var input: ReachInput
    public var plugins: [PluginView]

    /// Each endpoint fails independently. A missing chat is omitted, never interpreted as
    /// an answered question. At most four requests for Bots run concurrently.
    public static func load(backend: any Backend) async throws -> Self {
        async let today = try? backend.today()
        async let plugins = try? backend.plugins()
        async let reports = try? backend.healthReports()
        let loaded = await (today, plugins, reports)
        try Task.checkCancellation()
        let bots = BotList.build(plugins: loaded.1 ?? [], catalog: .main).filter { $0.kind != .source }
        var chats: [String: [ChatMessage]] = [:]
        await withTaskGroup(of: (String, [ChatMessage]?).self) { group in
            var remaining = bots.makeIterator()
            for _ in 0..<min(4, bots.count) {
                if let bot = remaining.next() {
                    group.addTask { (bot.id, try? await backend.chat(botID: bot.id, before: nil).items) }
                }
            }
            while let (id, lines) = await group.next() {
                if let lines { chats[id] = lines }
                if !Task.isCancelled, let bot = remaining.next() {
                    group.addTask { (bot.id, try? await backend.chat(botID: bot.id, before: nil).items) }
                }
            }
        }
        try Task.checkCancellation()
        guard loaded.0 != nil || loaded.1 != nil || loaded.2 != nil || !chats.isEmpty else {
            throw APIError.invalidResponse
        }
        return Self(
            input: ReachInput(
                today: loaded.0, reports: loaded.2 ?? [], chats: chats,
                names: Dictionary(uniqueKeysWithValues: bots.map { ($0.id, $0.name) })), plugins: loaded.1 ?? [])
    }
}
