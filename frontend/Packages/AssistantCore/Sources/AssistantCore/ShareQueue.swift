import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

public struct ShareConfiguration: Codable, Equatable, Sendable {
    public var baseURL: String
    public var useMockData: Bool
    public var conversations: [MissedSource]
    public init(baseURL: String, useMockData: Bool, conversations: [MissedSource] = []) {
        self.baseURL = baseURL
        self.useMockData = useMockData
        self.conversations = conversations
    }
}

public struct ShareQueueEntry: Codable, Equatable, Identifiable, Sendable {
    public var id: UUID
    public var destination: String
    public var item: InboxItem
    public var createdAt: Date
    public var receipt: InboxReceipt?
    public var leaseUntil: Date?
    public init(destination: String, item: InboxItem, createdAt: Date = Date()) {
        id = UUID()
        self.destination = destination
        self.item = item
        self.createdAt = createdAt
    }
}

public enum ShareQueueError: Error { case unavailable, invalid, mock, configurationChanged }

/// One locked read/modify/atomic-write transaction per mutation. Never hold the lock across a
/// network await. A short delivery lease prevents the App and extension sending the same row.
public struct ShareQueue: Sendable {
    public let directory: URL
    public init(directory: URL) { self.directory = directory }

    public static func appGroup(bundle: Bundle = .main) -> ShareQueue? {
        #if os(iOS)
        guard let group = bundle.object(forInfoDictionaryKey: "AssistantAppGroup") as? String,
              let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group) else { return nil }
        return ShareQueue(directory: root.appendingPathComponent("ShareInbox", isDirectory: true))
        #else
        return nil
        #endif
    }

    private struct State: Codable {
        var configuration: ShareConfiguration?
        var entries: [ShareQueueEntry] = []
    }

    private func transaction<T>(_ edit: (inout State) throws -> T) throws -> T {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let lock = directory.appendingPathComponent("queue.lock").path
        let descriptor = open(lock, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw ShareQueueError.unavailable }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX) == 0 else { throw ShareQueueError.unavailable }
        defer { flock(descriptor, LOCK_UN) }
        let file = directory.appendingPathComponent("queue.json")
        var state: State
        if FileManager.default.fileExists(atPath: file.path) {
            state = try JSONDecoder().decode(State.self, from: Data(contentsOf: file))
        } else {
            state = State()
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        let before = try encoder.encode(state)
        let result = try edit(&state)
        let after = try encoder.encode(state)
        if after != before {
            #if os(iOS)
            try after.write(to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            #else
            try after.write(to: file, options: .atomic)
            #endif
        }
        return result
    }

    public func configuration() throws -> ShareConfiguration? { try transaction { $0.configuration } }
    public func configure(_ value: ShareConfiguration) throws { try transaction { $0.configuration = value } }
    public func entries(destination: String) throws -> [ShareQueueEntry] {
        try transaction { $0.entries.filter { $0.destination == destination } }
    }
    @discardableResult
    public func enqueue(_ item: InboxItem, configuration: ShareConfiguration, now: Date = Date()) throws -> ShareQueueEntry {
        guard item.isValid else { throw ShareQueueError.invalid }
        return try transaction { state in
            guard state.configuration == configuration else { throw ShareQueueError.configurationChanged }
            guard !configuration.useMockData else { throw ShareQueueError.mock }
            // Match the backend's text-only idempotency; never collapse different destinations.
            if let existing = state.entries.first(where: {
                $0.destination == configuration.baseURL && $0.item.text == item.text
                    && ($0.receipt == nil || now.timeIntervalSince($0.receipt!.date) < 600)
            }) { return existing }
            let entry = ShareQueueEntry(destination: configuration.baseURL, item: item, createdAt: now)
            state.entries.append(entry)
            return entry
        }
    }
    public func claim(id: UUID, now: Date = Date()) throws -> ShareQueueEntry? {
        try transaction { state in
            guard let index = state.entries.firstIndex(where: { $0.id == id }),
                  let config = state.configuration, !config.useMockData,
                  state.entries[index].destination == config.baseURL,
                  state.entries[index].receipt == nil,
                  (state.entries[index].leaseUntil ?? .distantPast) <= now else { return nil }
            state.entries[index].leaseUntil = now.addingTimeInterval(60)
            return state.entries[index]
        }
    }
    public func release(id: UUID) throws {
        try transaction { state in
            guard let index = state.entries.firstIndex(where: { $0.id == id }) else { return }
            state.entries[index].leaseUntil = nil
        }
    }
    public func accepted(id: UUID, response: InboxAccepted, now: Date = Date()) throws {
        try transaction { state in
            guard let index = state.entries.firstIndex(where: { $0.id == id }) else { return }
            state.entries[index].receipt = InboxReceipt(id: response.messageID, text: state.entries[index].item.chatText, date: now)
            state.entries[index].leaseUntil = nil
        }
    }
    /// Called only after ManualEntryStore has durably persisted the receipt. Pending rows stay.
    public func acknowledge(ids: Set<UUID>) throws {
        try transaction { $0.entries.removeAll { ids.contains($0.id) && $0.receipt != nil } }
    }
}
