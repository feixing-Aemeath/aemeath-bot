import Combine

/// The reader switches update immediately; each change saves the complete selection.
@MainActor
public final class KnowledgeReaderSelection: ObservableObject {
    @Published public private(set) var chosen: [String]
    private var saved: [String]
    private var pending: Task<Void, Never>?
    private var revision = 0

    public init(chosen: [String]) {
        self.chosen = chosen
        self.saved = chosen
    }

    @discardableResult
    public func set(
        _ name: String, _ on: Bool,
        save: @escaping @MainActor ([String]) async throws -> [String],
        onError: @escaping @MainActor (Error) -> Void
    ) -> Task<Void, Never> {
        var names = chosen.filter { $0 != name }
        if on { names.append(name) }
        chosen = names
        revision += 1
        let current = revision
        let previous = pending
        let task = Task {
            await previous?.value
            do {
                saved = try await save(names)
                if current == revision { chosen = saved }
            } catch {
                if current == revision { chosen = saved }
                onError(error)
            }
            if current == revision { pending = nil }
        }
        pending = task
        return task
    }
}
