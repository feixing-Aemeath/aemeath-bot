import Foundation

/// Watch actions stay on the phone until the Mac accepts them, including across launches.
public final class WatchCompletions {
    private struct Entry: Codable, Equatable {
        var id: String
        var done: Bool
        var revision = UUID()
        var destination: String?
    }

    private let defaults: UserDefaults
    private let key = "watch.pendingCompletions"
    private var entries: [Entry]
    private var draining = false
    private var destination: String?

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        entries = defaults.data(forKey: key).flatMap { try? JSONDecoder().decode([Entry].self, from: $0) } ?? []
    }

    /// What the Mac has not accepted yet, as the watch left it.
    public var pending: [(id: String, done: Bool)] {
        entries.filter { $0.destination == destination }.map { ($0.id, $0.done) }
    }

    /// Older entries belonged to the backend configured when the phone first opens this version.
    public func selectDestination(_ destination: String) {
        self.destination = destination
        guard entries.contains(where: { $0.destination == nil }) else { return }
        for index in entries.indices where entries[index].destination == nil {
            entries[index].destination = destination
        }
        save()
    }

    @MainActor
    public func enqueue(id: String, done: Bool) {
        entries.removeAll { $0.id == id && $0.destination == destination }
        entries.append(Entry(id: id, done: done, destination: destination))
        save()
    }

    @MainActor
    public func retry(_ apply: (String, Bool) async throws -> Void) async {
        guard !draining else { return }
        draining = true
        defer { draining = false }
        let target = destination
        for entry in entries where entry.destination == target {
            guard destination == target, !Task.isCancelled else { return }
            do {
                try await apply(entry.id, entry.done)
                entries.removeAll { $0.revision == entry.revision }
                save()
            } catch APIError.notFound, APIError.conflict, APIError.invalidRequest {
                // Deleted on the Mac meanwhile: asking again every refresh cannot help.
                entries.removeAll { $0.revision == entry.revision }
                save()
            } catch {
                continue  // offline or a server error: a later refresh retries
            }
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(entries) { defaults.set(data, forKey: key) }
    }
}
