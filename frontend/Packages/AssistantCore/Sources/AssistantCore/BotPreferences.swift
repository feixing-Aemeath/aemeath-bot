import Foundation

/// Sidebar and dashboard choices, keyed by the Bot's stable id.
public struct BotPreferences: Codable, Equatable, Sendable {
    public struct Entry: Codable, Equatable, Sendable {
        public var pinned = false
        public var customName = ""
        public var position: Int?
        public var visible = true
        // The API returns fixed-width UTC timestamps, so string order preserves microseconds.
        public fileprivate(set) var updatedAt = "1970-01-01T00:00:00.000000Z"

        enum CodingKeys: String, CodingKey {
            case pinned, position, visible, customName = "custom_name", updatedAt = "updated_at"
        }
    }

    public static let nameLimit = 20
    public private(set) var entries: [String: Entry] = [:]
    private static let key = "assistant.botPreferences.v1"

    public init() {}

    public subscript(id: String) -> Entry {
        get { entries[id] ?? Entry() }
        set {
            let previous = self[id]
            var entry = newValue
            entry.customName = ShareContent.limit(entry.customName, to: Self.nameLimit)
            entry.updatedAt = previous.updatedAt
            guard entry != previous else { return }
            entry.updatedAt = Self.nextTimestamp(after: previous.updatedAt)
            entries[id] = entry  // Keep resets so an offline device can learn about them.
        }
    }

    private static func nextTimestamp(after previous: String) -> String {
        // APIDateCoding reads whole milliseconds. Advance by at least one, including
        // when the previous timestamp came from a device whose clock is ahead.
        let last = APIDateCoding.date(from: previous)?.timeIntervalSince1970 ?? 0
        let now = Int64(Date().timeIntervalSince1970 * 1000)
        let millis = max(now, Int64((last * 1000).rounded()) + 1)
        let seconds = Date(timeIntervalSince1970: Double(millis / 1000))
        return seconds.formatted(.iso8601).dropLast() + String(format: ".%03lld000Z", millis % 1000)
    }

    public func merging(_ remote: [String: Entry]) -> Self {
        var result = self
        result.entries.merge(remote) { local, server in
            local.updatedAt > server.updatedAt ? local : server
        }
        return result
    }

    public func displayName(_ bot: Bot) -> String {
        bot.kind == .source ? bot.name : displayName(for: bot.id, fallback: bot.name)
    }

    public func displayName(for id: String, fallback: String) -> String {
        let name = self[id].customName.trimmingCharacters(in: .whitespacesAndNewlines)
        return name.isEmpty ? fallback : name
    }

    /// Preserve the supplied order for new Bots and for ties.
    public func ordered(_ bots: [Bot]) -> [Bot] {
        bots.enumerated().sorted { left, right in
            let a = self[left.element.id], b = self[right.element.id]
            return (a.pinned ? 0 : 1, a.position ?? Int.max, left.offset)
                < (b.pinned ? 0 : 1, b.position ?? Int.max, right.offset)
        }.map(\.element)
    }

    /// Whether the user has put any Bot in a place of their own.
    public var hasCustomOrder: Bool { entries.values.contains { $0.position != nil } }

    public func dashboardBots(_ bots: [Bot]) -> [Bot] {
        ordered(bots.filter { $0.kind != .source && self[$0.id].visible })
    }

    public mutating func setOrder(_ ids: [String]) {
        for (position, id) in ids.enumerated() { self[id].position = position }
    }

    public mutating func resetLayout() {
        for (id, entry) in entries {
            self[id] = Entry(customName: entry.customName)
        }
    }

    public static func load(from defaults: UserDefaults = ComplicationData.defaults()) -> Self {
        guard let data = defaults.data(forKey: key),
            let saved = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return saved
    }

    public func save(to defaults: UserDefaults = ComplicationData.defaults()) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.key) }
    }
}

extension BotPreferences.Entry {
    private enum LegacyKey: String, CodingKey { case customName }

    public init(from decoder: Decoder) throws {
        let fields = try decoder.container(keyedBy: CodingKeys.self)
        pinned = try fields.decode(Bool.self, forKey: .pinned)
        position = try fields.decodeIfPresent(Int.self, forKey: .position)
        visible = try fields.decode(Bool.self, forKey: .visible)
        if fields.contains(.customName) {
            customName = try fields.decode(String.self, forKey: .customName)
            updatedAt = try fields.decode(String.self, forKey: .updatedAt)
        } else {
            // Preferences saved before sync used camelCase and had no modification time.
            let legacy = try decoder.container(keyedBy: LegacyKey.self)
            customName = ShareContent.limit(try legacy.decode(String.self, forKey: .customName), to: BotPreferences.nameLimit)
        }
    }
}

public struct BotPreferenceSnapshot: Codable, Sendable {
    public let version: Int
    public let entries: [String: BotPreferences.Entry]
}
