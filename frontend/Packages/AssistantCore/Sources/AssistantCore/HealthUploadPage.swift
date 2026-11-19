import Foundation

/// One anchored-query result. Its cursor advances only after every upload succeeds.
public struct HealthUploadPage {
    public var samples: [HealthSample]
    public var deleted: [String]
    public var anchor: Data
    public var addedCount: Int

    public init(samples: [HealthSample], deleted: [String], anchor: Data, addedCount: Int) {
        // HealthKit may report a sample added and deleted together; the backend refuses both.
        self.samples = samples.filter { !deleted.contains($0.id) }
        self.deleted = deleted
        self.anchor = anchor
        self.addedCount = addedCount
    }

    public func hasMore(pageSize: Int) -> Bool { addedCount + deleted.count >= pageSize }

    @MainActor
    public func upload(
        kind: String, progress: HealthSyncState, batchSize: Int = 1000,
        send: (HealthBatch) async throws -> Void
    ) async throws {
        for start in stride(from: 0, to: max(samples.count, deleted.count), by: batchSize) {
            try await send(HealthBatch(
                samples: Array(samples.dropFirst(start).prefix(batchSize)),
                deleted: Array(deleted.dropFirst(start).prefix(batchSize))))
        }
        progress.saveAnchor(anchor, for: kind)
    }
}
