import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum ShareDelivery {
    /// Both the App and extension use a bounded foreground request. Never wait for connectivity.
    public static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 10
        #if !os(Linux)
        config.waitsForConnectivity = false
        #endif
        return URLSession(configuration: config)
    }
    public static func send(
        entry: ShareQueueEntry, queue: ShareQueue,
        submit: (InboxItem) async throws -> InboxAccepted
    ) async -> ShareDeliveryStatus {
        do {
            guard let claimed = try queue.claim(id: entry.id) else {
                return entry.receipt == nil ? .queued : .sent
            }
            let accepted: InboxAccepted
            do {
                accepted = try await submit(claimed.item)
            } catch {
                try? queue.release(id: entry.id)
                return .queued
            }
            // Commit acceptance before clearing the payload or publishing a success. If this
            // fails, preserve the lease and row; retry may rely on the server's 10 min window.
            try queue.accepted(id: entry.id, response: accepted)
            return .sent
        } catch { return .unavailable }
    }
}
