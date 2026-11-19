import AssistantCore
import Foundation

extension AppStore {
    var manualStorageKey: String {
        "assistant.manualEntries." + (connection.useMockData ? "mock" : connection.baseURL)
    }

    @MainActor
    func loadManualState() {
        guard !manualStateLoaded else { return }
        manualStateLoaded = true
        if let data = manualDefaults.data(forKey: manualStorageKey + ".inbox"),
            let kept = try? JSONDecoder().decode([InboxReceipt].self, from: data)
        {
            inboxReceipts = kept
        }
        if let data = manualDefaults.data(forKey: manualStorageKey + ".hidden"),
            let kept = try? JSONDecoder().decode(UnsureDismissals.self, from: data)
        {
            dismissedUnsure = kept
        }
    }

    @MainActor
    func persistManualState() {
        if let data = try? JSONEncoder().encode(inboxReceipts) {
            manualDefaults.set(data, forKey: manualStorageKey + ".inbox")
        }
        if let data = try? JSONEncoder().encode(dismissedUnsure) {
            manualDefaults.set(data, forKey: manualStorageKey + ".hidden")
        }
    }

    func withInbox(_ messages: [BotMessage], bot: Bot) -> [BotMessage] {
        guard bot.isAssistant else { return messages }
        return (messages + inboxReceipts.map(\.message)).sorted { $0.date < $1.date }
    }

    var visibleUnsure: [UnsureItem] {
        guard let today else { return [] }
        return today.unsureItems.filter { !dismissedUnsure.contains($0.id, on: today.date) }
    }

    @MainActor
    func dismissUnsure(_ item: UnsureItem) {
        loadManualState()
        dismissedUnsure.hide(item.id, on: today?.date ?? "")
        persistManualState()
    }

    @MainActor
    func reconcileManualEntries() {
        loadManualState()
        let now = Date()
        var changed = false
        for index in inboxReceipts.indices where inboxReceipts[index].waiting {
            if inboxReceipts[index].finished(by: runs) {
                inboxReceipts[index].waiting = false
                changed = true
            } else if now.timeIntervalSince(inboxReceipts[index].date) > 900 {
                inboxReceipts[index].waiting = false
                message = "消息已收下，还没看到处理结果。稍后可在今天的总结和运行记录里查看。"
                changed = true
            }
        }
        if changed { persistManualState() }
        for (id, since) in openingUnsure {
            let item = today?.unsureItems.first { $0.id == id }
            let finished = runs.first {
                $0.job == "fetch:" + (item?.plugin ?? "") && $0.status != .running
                    && ($0.finishedAt ?? .distantPast) >= since
            }
            if item == nil || finished != nil || now.timeIntervalSince(since) > 900 {
                openingUnsure[id] = nil
                if finished?.status == .failed {
                    openFailures[id] = "这次没有读到全文，请稍后再试。"
                } else if item != nil {
                    openFailures[id] = "已请求读取，可稍后查看更新后的总结。"
                }
            }
        }
    }

    @MainActor
    func createTodo(_ request: TodoNew) async throws -> TodoCreated {
        let generation = connectionGeneration
        let target = backend
        let result = try await target.createTodo(request)
        guard generation == connectionGeneration else { throw CancellationError() }
        await refresh()
        // The sheet presents this exact todo, including duplicates and future deadlines that
        // do not appear on today's card. It is also available to the shared timeline immediately.
        guard generation == connectionGeneration else { throw CancellationError() }
        return result
    }

    @MainActor
    func submitInbox(_ item: InboxItem) async throws {
        loadManualState()
        let generation = connectionGeneration
        let accepted = try await backend.submitInbox(item)
        guard generation == connectionGeneration else { throw CancellationError() }
        if !inboxReceipts.contains(where: { $0.id == accepted.messageID }) {
            inboxReceipts.append(InboxReceipt(id: accepted.messageID, text: item.chatText, date: Date()))
            persistManualState()
        }
        await refresh()
    }

    @MainActor
    func openUnsure(_ item: UnsureItem) async {
        guard item.canOpen, let plugin = item.plugin, let chat = item.chatName,
            openingUnsure[item.id] == nil
        else { return }
        let generation = connectionGeneration
        openingUnsure[item.id] = Date()
        openFailures[item.id] = nil
        do {
            _ = try await backend.openChat(pluginID: plugin, chatName: chat)
            await refresh()
        } catch {
            guard generation == connectionGeneration else { return }
            openingUnsure[item.id] = nil
            openFailures[item.id] = (error as? APIError).map(ManualEntryText.openError) ?? "没能开始读全文，请稍后重试。"
        }
    }

    func busySince(_ bot: Bot) -> Date {
        let replies = (chats[bot.id] ?? []).filter { $0.author == .bot && $0.status == .pending }.map(
            \.createdAt)
        let inbox = bot.isAssistant ? inboxReceipts.filter(\.waiting).map(\.date) : []
        let jobs = runs.filter {
            $0.status == .running
                && (bot.isAssistant
                    ? $0.job == "extract_todos"
                    : bot.pluginIDs.contains(String($0.job.dropFirst("fetch:".count))))
        }.map(\.startedAt)
        return (replies + inbox + jobs + [sendingChats[bot.id]].compactMap { $0 }).min() ?? stateSince
    }
}
