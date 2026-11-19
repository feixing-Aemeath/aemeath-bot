import AssistantCore
import Foundation

extension AppStore {
    func cacheShareConfiguration() {
        guard let shareQueue else { return }
        let old = try? shareQueue.configuration()
        let names = old?.baseURL == connection.baseURL ? old?.conversations ?? [] : []
        try? shareQueue.configure(ShareConfiguration(
            baseURL: connection.baseURL, useMockData: connection.useMockData,
            conversations: connection.useMockData ? [] : names))
    }

    @MainActor
    func refreshShareInbox(reached: Bool, pluginsLoaded: Bool) async {
        guard let queue = shareQueue else { return }
        if pluginsLoaded, !connection.useMockData {
            try? queue.configure(ShareConfiguration(
                baseURL: connection.baseURL, useMockData: false,
                conversations: Array(Set(ManualEntryText.conversations(plugins))).sorted {
                    ($0.plugin, $0.chatName) < ($1.plugin, $1.chatName)
                }))
        }
        guard !connection.useMockData else { pendingShareCount = 0; return }
        importShareReceipts(queue)
        guard reached, !drainingShares else { return }
        drainingShares = true
        defer { drainingShares = false; importShareReceipts(queue) }
        let destination = connection.baseURL
        guard let url = URL(string: destination), ["http", "https"].contains(url.scheme), url.host != nil else { return }
        #if canImport(Security)
        let session = ShareDelivery.session()
        defer { session.invalidateAndCancel() }
        let api = APIClient(baseURL: url, tokens: AppTokenStore(), session: session)
        let pending = (try? queue.entries(destination: destination)) ?? []
        // Yield back to normal refresh after at most five requests; the next refresh continues.
        for entry in pending.filter({ $0.receipt == nil }).prefix(5) {
            guard !Task.isCancelled, connection.baseURL == destination, !connection.useMockData else { break }
            let result = await ShareDelivery.send(entry: entry, queue: queue) { try await api.submitInbox($0) }
            if result != .sent { break }
        }
        #endif
    }

    @MainActor
    func importShareReceipts(_ queue: ShareQueue) {
        guard !connection.useMockData else { pendingShareCount = 0; return }
        loadManualState()
        guard let entries = try? queue.entries(destination: connection.baseURL) else { return }
        pendingShareCount = entries.filter { $0.receipt == nil }.count
        var imported = Set<UUID>()
        for entry in entries {
            guard let receipt = entry.receipt else { continue }
            if !inboxReceipts.contains(where: { $0.id == receipt.id }) { inboxReceipts.append(receipt) }
            imported.insert(entry.id)
        }
        guard !imported.isEmpty else { return }
        // Persist first; a crash before acknowledge reimports by the same message ID.
        persistManualState()
        guard manualDefaults.synchronize() else { return }
        try? queue.acknowledge(ids: imported)
    }
}
