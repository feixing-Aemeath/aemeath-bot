import AssistantCore
import Combine
import Foundation
#if os(macOS)
    import AppKit
#elseif os(iOS)
    import UIKit
#endif

/// Where the App connects. Stored in UserDefaults; the token stays in the Keychain.
public struct ConnectionSettings: Equatable, Sendable {
    public var baseURL: String
    public var useMockData: Bool

    public static let defaultURL = "http://127.0.0.1:8765"

    public init(baseURL: String = ConnectionSettings.defaultURL, useMockData: Bool = true) {
        self.baseURL = baseURL
        self.useMockData = useMockData
    }

    var destination: String { useMockData ? "mock" : baseURL }

    static let urlKey = "assistant.baseURL"
    static let mockKey = "assistant.useMockData"

    public static func load(_ defaults: UserDefaults = .standard) -> ConnectionSettings {
        ConnectionSettings(
            baseURL: defaults.string(forKey: urlKey) ?? defaultURL,
            // Mock data until the Mac mini is set up.
            useMockData: defaults.object(forKey: mockKey) as? Bool ?? true)
    }

    public func save(_ defaults: UserDefaults = .standard) {
        defaults.set(baseURL, forKey: Self.urlKey)
        defaults.set(useMockData, forKey: Self.mockKey)
    }
}

/// The state all screens share: loads everything from a `Backend`, applies changes and polls
/// while a job runs. Views only read it and call its actions.
public final class AppStore: ObservableObject {
    @Published public private(set) var status: Status?
    /// False when the backend could not be reached on the last refresh.
    @Published public private(set) var online = true
    @Published public private(set) var today: Today? { didSet { timelines = [:] } }
    @Published public private(set) var todos: [Todo] = [] {
        didSet {
            timelines = [:]
            todosByID = Dictionary(todos.lazy.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        }
    }
    private(set) var todosByID: [String: Todo] = [:]
    @Published public private(set) var digests: [Digest] = [] { didSet { timelines = [:] } }
    @Published public private(set) var runs: [Run] = [] { didSet { timelines = [:] } }
    @Published public private(set) var plugins: [PluginView] = []
    @Published public private(set) var cards: [String: [Card]] = [:]
    /// The last /today named every card's plugin, and `cards` was filled from it for every
    /// plugin: the dashboard and the pages need not ask each plugin for its own.
    @Published public private(set) var cardsFromToday = false
    @Published public private(set) var healthReports: [HealthReport] = [] { didSet { timelines = [:] } }
    @Published public private(set) var settings: RuntimeSettings?
    @Published public private(set) var connection: ConnectionSettings
    @Published public private(set) var lastRefresh: Date?
    /// When the cached Today (including its date-relative cards) was read successfully.
    @Published public private(set) var todayUpdatedAt: Date?
    /// Whether the first refresh has come back, loaded or not: until then a Bot that was open
    /// last is unknown rather than gone.
    @Published public private(set) var settled = false
    /// The last failed action, in words, until dismissed.
    @Published public var message: String?
    /// When the assistant's current state began (for the "done" bounce).
    @Published public private(set) var stateSince = Date()
    /// Each Bot's chat from the backend, oldest first, by Bot id.
    @Published public private(set) var chats: [String: [ChatMessage]] = [:] { didSet { timelines = [:] } }
    /// Reads the chat before what `chats` holds; no entry once it reaches a Bot's first line.
    @Published public private(set) var chatCursors: [String: String] = [:]
    /// Today's finished todo extractions, as the backend counts them.
    @Published public private(set) var extractionsToday: RunCount? { didSet { timelines = [:] } }
    /// When each Bot's page was last open, for the unread marks; kept in UserDefaults.
    @Published public private(set) var seen: [String: Date] = AppStore.loadSeen()
    /// Which Agent and model answer what is typed to a Bot; kept in UserDefaults.
    @Published public var chatChoice = ChatChoice(stored: UserDefaults.standard.string(forKey: AppStore.choiceKey)) {
        didSet { UserDefaults.standard.set(chatChoice.stored, forKey: Self.choiceKey) }
    }

    @Published var botPreferences: BotPreferences {
        didSet {
            guard oldValue != botPreferences else { return }
            botPreferences.save(to: preferenceDefaults)
            if !applyingPreferences { schedulePreferencePush() }
        }
    }
    private let preferenceDefaults: UserDefaults
    private var applyingPreferences = false
    private var preferenceDebounce: Task<Void, Never>?
    private var preferenceSync: Task<Void, Never>?
    private var preferenceForeground: AnyCancellable?
    private var preferenceEndpointMissing = false

    static let choiceKey = "assistant.chatChoice"

    /// Each Bot's timeline, worked out once and kept until what it is made of changes (or the
    /// day does): every sidebar row, the page and its unread mark ask for it on each redraw, and
    /// working it out goes through all the digests, which made every click lag.
    private var timelines: [String: [BotMessage]] = [:]
    private var timelinesDay = Date.distantPast

    /// The choices for `chatChoice`: 自动 and the models of the Agents the Mac has.
    public var chatOptions: [ChatOption] { ChatOption.build(status: status) }
    /// Called after every refresh, e.g. to send the watch a new snapshot.
    public var onRefresh: (@MainActor () -> Void)?

    private(set) var backend: any Backend
    private var pollTask: Task<Void, Never>?
    private var chatPolls: [String: Task<Void, Never>] = [:]
    private var lastAssistantState: MascotState?
    private let watchCompletions: WatchCompletions
    private(set) var connectionGeneration = 0
    private var refreshGeneration = 0
    /// PATCH responses accepted while the current refresh's GETs are still in flight.
    private var editsDuringRefresh: [String: Todo]?
    private var pollingPaused = false
    @Published private(set) var authorizationIssue: String?
    /// Refreshes in a row that reached nothing.
    private(set) var misses = 0
    public let calendar: Calendar

    public init(
        backend: any Backend, connection: ConnectionSettings = ConnectionSettings(),
        calendar: Calendar = DueText.calendar(), watchCompletions: WatchCompletions = WatchCompletions(),
        preferenceDefaults: UserDefaults = ComplicationData.defaults()
    ) {
        self.preferenceDefaults = preferenceDefaults
        self.botPreferences = BotPreferences.load(from: preferenceDefaults)
        self.backend = backend
        self.connection = connection
        self.calendar = calendar
        self.watchCompletions = watchCompletions
        watchCompletions.selectDestination(connection.destination)
        _ = Self.firstLaunch()
    }

    /// The App's store: mock data or the Mac, as configured in Settings.
    public static func standard() -> AppStore {
        #if DEBUG && canImport(Security)
            // Development builds: `open --env ASSISTANT_IMPORT_TOKEN=… App.app` hands the App a
            // device token, which it saves in its own Keychain item. An item that another tool
            // (such as `security`) created makes an unsigned build ask for the login password.
            if let token = ProcessInfo.processInfo.environment["ASSISTANT_IMPORT_TOKEN"], !token.isEmpty {
                try? AppTokenStore().save(token)
            }
        #endif
        let connection = ConnectionSettings.load()
        #if canImport(Security)
        AppTokenStore().synchronize()
        #endif
        let store = AppStore(backend: makeBackend(connection), connection: connection)
        store.shareQueue = ShareQueue.appGroup()
        store.cacheShareConfiguration()
        return store
    }

    static func makeBackend(_ connection: ConnectionSettings) -> any Backend {
        #if canImport(Security)
            if !connection.useMockData, let url = URL(string: connection.baseURL) {
                return LiveBackend(client: APIClient(baseURL: url, tokens: AppTokenStore()))
            }
        #endif
        return MockBackend()
    }

    // MARK: Derived state

    public var assistantState: MascotState {
        MascotState.forAssistant(status: online ? status : nil, runs: runs, now: Date())
    }

    public var isJobRunning: Bool { runs.contains { $0.status == .running } || inboxReceipts.contains(where: \.waiting) || !openingUnsure.isEmpty }

    public var remainingToday: Int { MenuBarSummary.remaining(today) }

    public func plugin(_ id: String) -> PluginView? { plugins.first { $0.id == id } }

    public func pluginState(_ plugin: PluginView) -> MascotState {
        online ? MascotState.forPlugin(plugin, runs: runs, now: Date()) : .offline
    }

    public func statusLine(_ plugin: PluginView) -> String {
        PluginStatusLine.describe(plugin, runs: runs, todos: todos, now: Date(), calendar: calendar)
    }

    public var digestSections: [DigestSection] {
        DigestSections.build(digests: digests, todos: todos, runs: runs, calendar: calendar)
    }

    /// The Mac sidebar: the assistant, then one Bot per plugin.
    public var bots: [Bot] {
        let all = BotList.build(plugins: plugins, catalog: .main)
        return botPreferences.ordered(all.filter { $0.kind != .source }) + all.filter { $0.kind == .source }
    }

    func displayName(_ bot: Bot) -> String { botPreferences.displayName(bot) }

    public func botState(_ bot: Bot) -> MascotState {
        if online, isReplying(bot) { return .working }
        if bot.kind == .knowledge {
            return online ? MascotState.forKnowledge(inputs: inputs(of: bot), runs: runs, now: Date()) : .offline
        }
        return plugin(bot.id).map(pluginState) ?? assistantState
    }

    /// The line under a Bot's name on its page: how its plugin is doing, what 知识库Bot's inputs
    /// are up to, or the Agent's headline for the assistant.
    public func statusLine(_ bot: Bot) -> String {
        if bot.kind == .knowledge {
            return KnowledgeStatusLine.describe(inputs: inputs(of: bot), runs: runs, now: Date(), calendar: calendar)
        }
        return plugin(bot.id).map(statusLine) ?? assistantStatusLine
    }

    /// 「空闲 · 9 分钟前提炼过」: the assistant's state and its latest todo extraction.
    public var assistantStatusLine: String {
        // Before the Mac's first answer: connecting, not 「连不上 Mac」.
        if online, status == nil, lastRefresh == nil { return "正在连接 Mac…" }
        let extractions = runs.filter { $0.job == "extract_todos" }
        let counted = extractionsToday?.latest
        let success = extractions.first { $0.status == .succeeded }
        let newerCounted = counted.flatMap { latest in
            success.map { ($0.finishedAt ?? $0.startedAt) >= (latest.finishedAt ?? latest.startedAt) } == true ? nil : latest
        }
        return AgentText.status(
            assistantState, lastRun: extractions.first ?? status?.lastRun, lastSuccess: newerCounted ?? success,
            now: Date(), calendar: calendar)
    }

    /// The plugins that joined a Bot, e.g. 知识库Bot's GoodNotes, 课堂照片 and 视频.
    public func inputs(of bot: Bot) -> [PluginView] { bot.joined.compactMap(plugin) }

    /// The Agent is writing this Bot's answer.
    public func isReplying(_ bot: Bot) -> Bool {
        sendingChats[bot.id] != nil || BotChat.isReplying(chats[bot.id] ?? []) || (bot.isAssistant && inboxReceipts.contains(where: \.waiting))
    }

    /// What the Bot said and the chat with it, oldest first.
    public func messages(_ bot: Bot) -> [BotMessage] {
        let now = Date()
        let day = calendar.startOfDay(for: now)
        if day != timelinesDay {
            timelines = [:]
            timelinesDay = day
        }
        if let kept = timelines[bot.id] { return withInbox(kept, bot: bot) }
        let timeline = BotTimeline.messages(
            for: bot, today: today, digests: digests, todos: todos, runs: runs,
            reports: healthReports, chat: BotChat.messages(chats[bot.id] ?? []), now: now,
            calendar: calendar)
        timelines[bot.id] = timeline
        return withInbox(timeline, bot: bot)
    }

    // MARK: Loading

    private func schedulePreferencePush() {
        guard !connection.useMockData, !preferenceEndpointMissing else { return }
        preferenceDebounce?.cancel()
        preferenceDebounce = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(1)) } catch { return }
            // An earlier PUT may still be in flight. It cannot acknowledge this edit.
            await self?.preferenceSync?.value
            guard let self, !Task.isCancelled else { return }
            preferenceDebounce = nil
            await syncBotPreferences()
        }
    }

    @MainActor
    private func syncBotPreferences() async {
        guard !connection.useMockData else { return }
        if let preferenceSync {
            await preferenceSync.value
            return
        }
        let task = Task { @MainActor [backend] in
            defer { if !Task.isCancelled { preferenceSync = nil } }
            do {
                for attempt in 0...1 {
                    try Task.checkCancellation()
                    let remote = try await backend.botPreferences()
                    try Task.checkCancellation()
                    preferenceEndpointMissing = false
                    let merged = botPreferences.merging(remote.entries)
                    guard merged.entries.count <= 100 else {
                        message = "两台设备合并后超过 100 项，偏好已分别保存在本机和后端。"
                        return
                    }
                    if botPreferences != merged {
                        applyingPreferences = true
                        botPreferences = merged
                        applyingPreferences = false
                    }
                    guard merged.entries != remote.entries, preferenceDebounce == nil else { return }
                    do {
                        _ = try await backend.updateBotPreferences(merged.entries, expectedVersion: remote.version)
                        return
                    } catch APIError.conflict where attempt == 0 {
                        continue  // Re-read the winner and include any edits made during the PUT.
                    }
                }
            } catch APIError.notFound {
                if !Task.isCancelled { preferenceEndpointMissing = true }
            } catch {
                // Local edits are already saved. The next refresh compares them again.
            }
        }
        preferenceSync = task
        await task.value
    }

    /// Loads each screen independently; a failed endpoint keeps its previous data.
    @MainActor
    public func refresh() async {
        refreshGeneration += 1
        let generation = refreshGeneration
        let backend = self.backend, targetGeneration = connectionGeneration
        editsDuringRefresh = [:]
        async let preferences: Void = syncBotPreferences()
        async let status = Self.readStatus(backend)
        async let today = try? backend.today()
        async let todos = try? backend.todos(status: nil)
        async let digests = try? backend.digests()
        async let runs = try? backend.runs()
        async let plugins = try? backend.plugins()
        async let reports = try? backend.healthReports()
        async let settings = try? backend.settings()
        let dayStart = calendar.startOfDay(for: Date())
        let dayEnd = calendar.date(byAdding: .day, value: 1, to: dayStart) ?? dayStart
        async let extractions = try? backend.succeeded(
            job: "extract_todos", since: dayStart, until: dayEnd)
        let loaded = await (status, today, todos, digests, runs, plugins, reports, settings, extractions)
        guard generation == refreshGeneration else { return }
        let edits = editsDuringRefresh ?? [:]
        editsDuringRefresh = nil
        guard !Task.isCancelled else { return }
        authorizationIssue = loaded.0.issue
        if let value = loaded.0.status { self.status = value }
        if var value = loaded.1 {
            if !edits.isEmpty {
                value.todos = value.todos.compactMap { original in
                    guard let saved = edits[original.id] else { return original }
                    return saved.status == .deleted || saved.status == .cancelled ? nil : saved
                }
            }
            self.today = value
            todayUpdatedAt = Date()
            adoptCards(value.cards, plugins: loaded.5 ?? self.plugins)
        }
        if let value = loaded.2 {
            self.todos = edits.isEmpty ? value : value.map { edits[$0.id] ?? $0 }
        }
        if let value = loaded.3 { self.digests = value }
        if let value = loaded.4 { self.runs = value }
        if let value = loaded.5 { self.plugins = value }
        if let value = loaded.6 { self.healthReports = value }
        if let value = loaded.7 { self.settings = value }
        if let value = loaded.8 { self.extractionsToday = value }
        let reached = loaded.0.status != nil || loaded.1 != nil || loaded.2 != nil || loaded.3 != nil
            || loaded.4 != nil || loaded.5 != nil || loaded.6 != nil || loaded.7 != nil
        // One refresh that reaches nothing is often the phone waking up or changing network, so
        // the App says it cannot reach the Mac from the second in a row, which comes 2 s later.
        misses = reached ? 0 : misses + 1
        online = authorizationIssue == nil && (loaded.0.status?.online ?? (reached || misses < 2))
        if reached { lastRefresh = Date() }
        // The chats already opened, read again: a line written elsewhere (the other device, a
        // Bot speaking first about a goal) used to wait until the page was opened again.
        if online, reached {
            for id in chats.keys.sorted() {
                guard let page = try? await backend.chat(botID: id, before: nil) else { continue }
                guard generation == refreshGeneration, !Task.isCancelled else { return }
                showNewest(page, botID: id)
                if BotChat.isReplying(chats[id] ?? []) { pollChat(id) }
            }
        }
        if online, reached {
            guard generation == refreshGeneration, !Task.isCancelled else { return }
            await watchCompletions.retry { id, done in
                guard targetGeneration == connectionGeneration else { throw CancellationError() }
                let updated = try await backend.updateTodo(
                    id: id, TodoPatch(status: done ? .completed : .pending))
                guard updated.status == (done ? .completed : .pending) else { throw APIError.invalidResponse }
                if targetGeneration == connectionGeneration { replace(updated) }
            }
        }
        guard generation == refreshGeneration, !Task.isCancelled else { return }
        await preferences
        guard generation == refreshGeneration, !Task.isCancelled else { return }
        await refreshShareInbox(reached: reached, pluginsLoaded: loaded.5 != nil)
        guard generation == refreshGeneration, !Task.isCancelled else { return }
        reconcileManualEntries()
        settled = true
        let state = assistantState
        if state != lastAssistantState {
            lastAssistantState = state
            stateSince = Date()
        }
        onRefresh?()
    }

    private static func readStatus(_ backend: any Backend) async -> (status: Status?, issue: String?) {
        do {
            return (try await backend.status(), nil)
        } catch let error as APIError where [.missingToken, .unauthorized, .forbidden].contains(error) {
            return (nil, describe(error))
        } catch {
            return (nil, nil)
        }
    }

    /// /today's cards sorted by plugin, when each names its own (backends before that leave the
    /// pages to load their plugins' cards). Only a change is published.
    @MainActor
    func adoptCards(_ list: [Card], plugins: [PluginView]) {
        guard !list.isEmpty, list.allSatisfy({ $0.plugin != nil }) else {
            cardsFromToday = false
            return
        }
        let grouped = Dictionary(grouping: list) { $0.plugin ?? "" }
        var next = cards
        for id in Set(plugins.map(\.id)).union(grouped.keys) {
            next[id] = grouped[id] ?? []
        }
        if next != cards { cards = next }
        if !cardsFromToday { cardsFromToday = true }
    }

    /// The cards of these Bots' plugins, one plugin after another; nothing to do when /today
    /// brought them.
    @MainActor
    public func loadCards(of bots: [Bot]) async {
        guard !cardsFromToday else { return }
        let generation = connectionGeneration
        for id in bots.flatMap(\.pluginIDs) {
            guard generation == connectionGeneration, !Task.isCancelled else { return }
            await loadCards(id)
        }
    }

    @MainActor
    public func loadCards(_ pluginID: String) async {
        let generation = connectionGeneration
        do {
            let loaded = try await backend.cards(pluginID: pluginID).map { card in
                var tagged = card
                tagged.plugin = card.plugin ?? pluginID
                return tagged
            }
            guard generation == connectionGeneration, !Task.isCancelled else { return }
            cards[pluginID] = loaded
        } catch APIError.notFound {
            if generation == connectionGeneration, !Task.isCancelled { cards[pluginID] = [] }
        } catch {
            // Keep the last successful list through an outage or a cancelled page load.
        }
    }

    /// Refreshes every 30 s, every 2 s while a job runs; 2 s after a refresh that reached
    /// nothing, then every 10 s until the Mac answers. Calling it again does nothing.
    @MainActor
    public func start() {
        pollingPaused = false
        guard pollTask == nil else { return }
        #if os(macOS) || os(iOS)
            #if os(macOS)
                let active = NSApplication.didBecomeActiveNotification
            #else
                let active = UIApplication.didBecomeActiveNotification
            #endif
            preferenceForeground = NotificationCenter.default.publisher(for: active).sink { [weak self] _ in
                Task { @MainActor in await self?.syncBotPreferences() }
            }
        #endif
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                let delay = Self.pollDelay(misses: self.misses, jobRunning: self.isJobRunning)
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
            }
        }
    }

    static func pollDelay(misses: Int, jobRunning: Bool) -> UInt64 {
        switch misses {
        case 0: return jobRunning ? 2 : 30
        case 1: return 2
        default: return 10
        }
    }

    @MainActor
    public func stop() {
        pollingPaused = true
        preferenceForeground = nil
        pollTask?.cancel()
        pollTask = nil
        chatPolls.values.forEach { $0.cancel() }
        chatPolls = [:]
    }

    // MARK: Actions

    @MainActor
    func watchSnapshot() -> WatchSnapshot? {
        guard let today else { return nil }
        let snapshot = WatchSnapshot.make(
            today: today, online: online, state: assistantState, now: Date(),
            calendar: calendar, plugins: plugins, updatedAt: todayUpdatedAt)
        // With the Mac out of reach `today` still has what was ticked off: without this the
        // tick would come undone on the watch as soon as the iPhone answered.
        return pendingWatchCompletions.reduce(snapshot) { $0.setting($1.id, done: $1.done) }
    }

    /// Ticks from the watch that have not reached the Mac yet.
    @MainActor
    var pendingWatchCompletions: [(id: String, done: Bool)] { watchCompletions.pending }

    @MainActor
    public func handleWatchRequest(_ request: WatchRequest) async {
        switch request {
        case .refresh:
            await refresh()
        case .setDone(let id, let done):
            watchCompletions.enqueue(id: id, done: done)
            await refresh()
        }
    }

    @MainActor
    private func perform(_ failure: String, _ action: @MainActor () async throws -> Void) async {
        let generation = connectionGeneration
        do {
            try await action()
        } catch {
            guard generation == connectionGeneration else { return }
            message = "\(failure)：\(Self.describe(error))"
        }
    }

    static func describe(_ error: Error) -> String {
        switch error as? APIError {
        case .screenOpen(let detail): return ManualEntryText.openError(.screenOpen(detail))
        case .missingToken: return "还没有填写令牌"
        case .unauthorized: return "令牌不对，或者已经作废、过期"
        case .forbidden: return "这个令牌没有使用 App 的权限，在 Mac 上用 scripts.tokens 创建一个 app 权限的令牌"
        case .notFound: return "找不到这一项"
        case .conflict: return "和当前状态冲突（比如任务已经在运行）"
        case .invalidRequest: return "内容不符合要求"
        case .server(let status): return "后端出错（\(status)）"
        case .transport: return "连不上 Mac"
        case .invalidResponse, .decoding: return "后端返回的数据看不懂"
        case nil: return String(describing: error)
        }
    }

    /// Replaces a todo everywhere it is shown, before the next refresh confirms it.
    @MainActor
    private func replace(_ todo: Todo) {
        editsDuringRefresh?[todo.id] = todo
        if let index = todos.firstIndex(where: { $0.id == todo.id }) {
            todos[index] = todo
        }
        if var today, let index = today.todos.firstIndex(where: { $0.id == todo.id }) {
            if todo.status == .deleted || todo.status == .cancelled {
                today.todos.remove(at: index)
            } else {
                today.todos[index] = todo
            }
            self.today = today
        }
    }

    @MainActor
    public func update(_ todo: Todo, _ patch: TodoPatch) async {
        await perform("没能保存待办") { try await saveEdits(todo, patch) }
    }

    @MainActor
    func saveEdits(_ todo: Todo, _ patch: TodoPatch) async throws {
        guard !patch.isEmpty else { return }
        let target = backend, generation = connectionGeneration
        let updated = try await Self.againOnTransport {
            guard generation == connectionGeneration else { throw CancellationError() }
            return try await target.updateTodo(id: todo.id, patch)
        }
        guard generation == connectionGeneration else { throw CancellationError() }
        replace(updated)
        // A plugin's card may list it too (作业Bot's 「还没交」): read those cards again.
        let listing = cards.filter { _, list in
            list.contains { card in
                guard case .todoList(let todos) = card.content else { return false }
                return todos.contains { $0.id == todo.id }
            }
        }
        for plugin in listing.keys {
            await loadCards(plugin)
        }
    }

    /// A save that failed on the way (the phone had just woken, the network changed) is sent
    /// once more before the App says it cannot reach the Mac: setting the same fields twice
    /// leaves the todo as it was after the first.
    static func againOnTransport<T>(_ call: () async throws -> T) async throws -> T {
        do {
            return try await call()
        } catch let error as APIError {
            guard case .transport = error else { throw error }
            try await Task.sleep(nanoseconds: 1_500_000_000)
            return try await call()
        }
    }

    @MainActor
    public func toggleDone(_ todo: Todo) async {
        let next: TodoStatus = todo.status == .completed ? .pending : .completed
        await update(todo, TodoPatch(status: next))
    }

    @MainActor
    public func delete(_ todo: Todo, reason: TodoDeleteReason? = nil) async {
        await update(todo, .delete(reason: reason))
        await refresh()
    }

    /// "立即运行一次": starts a job and polls quickly until it ends.
    @MainActor
    public func runNow(_ jobID: String = "extract_todos") async {
        await perform("没能开始运行") {
            _ = try await backend.runJob(jobID)
        }
        await refresh()
    }

    @MainActor
    public func setPlugin(_ id: String, enabled: Bool) async {
        await perform(enabled ? "没能启用插件" : "没能停用插件") {
            let generation = connectionGeneration
            let updated = try await backend.updatePlugin(id: id, PluginPatch(enabled: enabled))
            guard generation == connectionGeneration else { return }
            if let index = plugins.firstIndex(where: { $0.id == id }) {
                plugins[index] = updated
            }
        }
        await loadCards(id)
    }

    /// Replaces a plugin's configuration (`PluginConfigEdit` builds it), e.g. its whitelist.
    @MainActor
    public func setPluginConfig(_ id: String, _ config: JSONValue) async {
        await perform("没能保存插件设置") {
            let generation = connectionGeneration
            let updated = try await backend.updatePlugin(id: id, PluginPatch(config: config))
            guard generation == connectionGeneration else { return }
            if let index = plugins.firstIndex(where: { $0.id == id }) {
                plugins[index] = updated
            }
        }
        await loadCards(id)
    }

    /// Loads a Bot's chat; while a reply is pending, keeps checking every 1.5 s until it is
    /// written (or has failed).
    @MainActor
    public func loadChat(_ bot: Bot) async {
        guard bot.kind != .source else { return }
        do {
            let generation = connectionGeneration
            let page = try await backend.chat(botID: bot.id, before: nil)
            guard generation == connectionGeneration, !Task.isCancelled else { return }
            showNewest(page, botID: bot.id)
        } catch {
            return  // keep what is shown; the refresh marks the App offline
        }
        if isReplying(bot) { pollChat(bot.id) }
    }

    @MainActor
    private func pollChat(_ botID: String) {
        guard !pollingPaused, chatPolls[botID] == nil else { return }
        chatPolls[botID] = Task { [weak self] in
            // Agents give up after the backend's timeout; stop asking after 15 minutes.
            for _ in 0..<600 {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                guard let self, !Task.isCancelled else { return }
                if let page = try? await self.backend.chat(botID: botID, before: nil) {
                    guard !Task.isCancelled else { return }
                    self.showNewest(page, botID: botID)
                    if !BotChat.isReplying(page.items) { break }
                }
            }
            guard !Task.isCancelled else { return }
            self?.chatPolls[botID] = nil
            await self?.refresh()  // the Agent may have added todos or records
        }
    }

    /// The newest page replaces what it covers; older lines read earlier stay, and so does
    /// their cursor.
    @MainActor
    private func showNewest(_ page: ChatHistory, botID: String) {
        let fresh = Set(page.items.map(\.id))
        let older = (chats[botID] ?? []).filter { line in
            !fresh.contains(line.id) && page.items.first.map { line.createdAt <= $0.createdAt } ?? false
        }
        // Only when something changed: every refresh reads the open chats again, and an
        // unchanged chat should not redraw the App.
        let merged = older + page.items
        if chats[botID] != merged { chats[botID] = merged }
        if older.isEmpty, chatCursors[botID] != page.nextBefore { chatCursors[botID] = page.nextBefore }
    }

    /// Reads one more page of a Bot's chat, before what is already here.
    @MainActor
    public func loadEarlierChat(_ bot: Bot) async {
        guard let cursor = chatCursors[bot.id] else { return }
        await perform("没读到更早的消息") {
            let generation = connectionGeneration
            let page = try await backend.chat(botID: bot.id, before: cursor)
            guard generation == connectionGeneration, chatCursors[bot.id] == cursor else { return }
            let known = Set((chats[bot.id] ?? []).map(\.id))
            chats[bot.id] = page.items.filter { !known.contains($0.id) } + (chats[bot.id] ?? [])
            chatCursors[bot.id] = page.nextBefore
        }
    }

    /// Sends a line to a Bot; the Agent on the Mac answers it.
    @MainActor
    @discardableResult
    public func send(_ text: String, to bot: Bot) async -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, bot.kind != .source, !isReplying(bot) else { return false }
        let generation = connectionGeneration
        sendingChats[bot.id] = Date()
        defer { if generation == connectionGeneration { sendingChats[bot.id] = nil } }
        do {
            let accepted = try await backend.sendChat(
                botID: bot.id, text: trimmed, botName: bot.name, choice: chatChoice)
            guard generation == connectionGeneration else { return false }
            // A concurrent GET can already include both IDs and a finished reply.
            let known = Set((chats[bot.id] ?? []).map(\.id))
            let missing = [accepted.message, accepted.reply].filter { !known.contains($0.id) }
            if !missing.isEmpty {
                chats[bot.id] = ((chats[bot.id] ?? []) + missing).sorted { $0.createdAt < $1.createdAt }
            }
            if BotChat.isReplying(chats[bot.id] ?? []) { pollChat(bot.id) }
            return true
        } catch {
            guard generation == connectionGeneration else { return false }
            message = "没发出去：" + Self.describe(error)
            return false
        }
    }

    static let seenKey = "assistant.botsSeen"
    static let firstLaunchKey = "assistant.firstLaunch"

    static func loadSeen(_ defaults: UserDefaults = .standard) -> [String: Date] {
        let stored = defaults.dictionary(forKey: seenKey) as? [String: Double] ?? [:]
        return stored.mapValues { Date(timeIntervalSince1970: $0) }
    }

    /// When the App first started on this device (recorded the first time it is asked).
    static func firstLaunch(_ defaults: UserDefaults = .standard) -> Date {
        if let stored = defaults.object(forKey: firstLaunchKey) as? Double {
            return Date(timeIntervalSince1970: stored)
        }
        let now = Date()
        defaults.set(now.timeIntervalSince1970, forKey: firstLaunchKey)
        return now
    }

    public func unread(_ bot: Bot) -> Int {
        BotTimeline.unread(messages(bot), seen: seen[bot.id], firstLaunch: Self.firstLaunch())
    }

    /// The Bot's page is open: what it said so far is read.
    @MainActor
    public func markSeen(_ bot: Bot) {
        seen[bot.id] = Date()
        UserDefaults.standard.set(seen.mapValues(\.timeIntervalSince1970), forKey: Self.seenKey)
    }

    /// Sends a readiness check-in for `day`, then asks the Mac to read it right away so the
    /// health cards show it. True when the backend took it.
    @MainActor
    public func logCheckin(_ checkin: ReadinessCheckin, day: Date) async -> Bool {
        let generation = connectionGeneration
        var sent = false
        await perform("没能保存这次记录") {
            _ = try await backend.uploadHealth(checkin.batch(for: day, now: Date(), calendar: calendar))
            sent = true
        }
        guard sent, generation == connectionGeneration else { return false }
        _ = try? await backend.runJob("plugin:health:refresh")
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            await self?.loadCards("health")
        }
        return true
    }

    @MainActor
    public func saveSettings(_ patch: SettingsPatch) async -> Bool {
        var saved = false
        await perform("没能保存时间表") {
            let generation = connectionGeneration
            let updated = try await backend.updateSettings(patch)
            guard generation == connectionGeneration else { return }
            settings = updated
            saved = true
        }
        return saved
    }

    /// Saves where to connect (and the token, if one was typed) and reloads everything.
    @MainActor
    public func connect(_ connection: ConnectionSettings, token: String?) async {
        #if canImport(Security)
            if let token, !token.isEmpty {
                do {
                    try AppTokenStore().save(token)
                } catch {
                    message = "没能把令牌存进钥匙串：\(error)"
                    return
                }
            }
        #endif
        connection.save()
        await activate(connection, backend: Self.makeBackend(connection))
    }

    @MainActor
    func activate(_ connection: ConnectionSettings, backend: any Backend) async {
        connectionGeneration += 1
        if connection != self.connection {
            status = nil
            today = nil
            todos = []
            digests = []
            runs = []
            plugins = []
            healthReports = []
            settings = nil
            extractionsToday = nil
            lastRefresh = nil
            todayUpdatedAt = nil
            settled = false
            misses = 0
            online = true
        }
        authorizationIssue = nil
        message = nil
        pendingShareCount = 0
        watchCompletions.selectDestination(connection.destination)
        preferenceDebounce?.cancel()
        preferenceDebounce = nil
        preferenceSync?.cancel()
        preferenceSync = nil
        preferenceEndpointMissing = false
        self.connection = connection
        cacheShareConfiguration()
        self.backend = backend
        sendingChats = [:]
        inboxReceipts = []
        dismissedUnsure = UnsureDismissals()
        openingUnsure = [:]
        openFailures = [:]
        manualStateLoaded = false
        cards = [:]
        cardsFromToday = false
        chatPolls.values.forEach { $0.cancel() }
        chatPolls = [:]
        chats = [:]
        chatCursors = [:]
        await refresh()
    }

    /// Whether a token is saved on this device (never shown).
    public var hasToken: Bool {
        #if canImport(Security)
            return (try? AppTokenStore().currentToken()) != nil
        #else
            return false
        #endif
    }
    @Published var sendingChats: [String: Date] = [:]
    @Published var inboxReceipts: [InboxReceipt] = []
    @Published var dismissedUnsure = UnsureDismissals()
    @Published var openingUnsure: [String: Date] = [:]
    @Published var openFailures: [String: String] = [:]
    @Published var pendingShareCount = 0
    var shareQueue: ShareQueue?
    var drainingShares = false
    var manualStateLoaded = false
    var manualDefaults: UserDefaults = .standard

}
