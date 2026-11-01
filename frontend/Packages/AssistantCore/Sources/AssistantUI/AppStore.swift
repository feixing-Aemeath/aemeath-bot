import AssistantCore
import Foundation

/// Where the App connects. Stored in UserDefaults; the token stays in the Keychain.
public struct ConnectionSettings: Equatable, Sendable {
    public var baseURL: String
    public var useMockData: Bool

    public static let defaultURL = "http://127.0.0.1:8765"

    public init(baseURL: String = ConnectionSettings.defaultURL, useMockData: Bool = true) {
        self.baseURL = baseURL
        self.useMockData = useMockData
    }

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
    @Published public private(set) var today: Today?
    @Published public private(set) var todos: [Todo] = []
    @Published public private(set) var digests: [Digest] = []
    @Published public private(set) var runs: [Run] = []
    @Published public private(set) var plugins: [PluginView] = []
    @Published public private(set) var cards: [String: [Card]] = [:]
    @Published public private(set) var healthReports: [HealthReport] = []
    @Published public private(set) var settings: RuntimeSettings?
    @Published public private(set) var connection: ConnectionSettings
    @Published public private(set) var lastRefresh: Date?
    /// Whether the first refresh has come back, loaded or not: until then a Bot that was open
    /// last is unknown rather than gone.
    @Published public private(set) var settled = false
    /// The last failed action, in words, until dismissed.
    @Published public var message: String?
    /// When the assistant's current state began (for the "done" bounce).
    @Published public private(set) var stateSince = Date()
    /// Each Bot's chat from the backend, oldest first, by Bot id.
    @Published public private(set) var chats: [String: [ChatMessage]] = [:]
    /// When each Bot's page was last open, for the unread marks; kept in UserDefaults.
    @Published public private(set) var seen: [String: Date] = AppStore.loadSeen()
    /// Which Agent and model answer what is typed to a Bot; kept in UserDefaults.
    @Published public var chatChoice = ChatChoice(stored: UserDefaults.standard.string(forKey: AppStore.choiceKey)) {
        didSet { UserDefaults.standard.set(chatChoice.stored, forKey: Self.choiceKey) }
    }

    static let choiceKey = "assistant.chatChoice"

    /// The choices for `chatChoice`: 自动 and the models of the Agents the Mac has.
    public var chatOptions: [ChatOption] { ChatOption.build(status: status) }
    /// Called after every refresh, e.g. to send the watch a new snapshot.
    public var onRefresh: (@MainActor () -> Void)?

    private(set) var backend: any Backend
    private var pollTask: Task<Void, Never>?
    private var chatPolls: [String: Task<Void, Never>] = [:]
    private var lastAssistantState: MascotState?
    public let calendar: Calendar

    public init(
        backend: any Backend, connection: ConnectionSettings = ConnectionSettings(),
        calendar: Calendar = DueText.calendar()
    ) {
        self.backend = backend
        self.connection = connection
        self.calendar = calendar
        _ = Self.firstLaunch()
    }

    /// The App's store: mock data or the Mac, as configured in Settings.
    public static func standard() -> AppStore {
        #if DEBUG && canImport(Security)
            // Development builds: `open --env ASSISTANT_IMPORT_TOKEN=… App.app` hands the App a
            // device token, which it saves in its own Keychain item. An item that another tool
            // (such as `security`) created makes an unsigned build ask for the login password.
            if let token = ProcessInfo.processInfo.environment["ASSISTANT_IMPORT_TOKEN"], !token.isEmpty {
                try? KeychainTokenStore().save(token)
            }
        #endif
        let connection = ConnectionSettings.load()
        return AppStore(backend: makeBackend(connection), connection: connection)
    }

    static func makeBackend(_ connection: ConnectionSettings) -> any Backend {
        #if canImport(Security)
            if !connection.useMockData, let url = URL(string: connection.baseURL) {
                return LiveBackend(client: APIClient(baseURL: url, tokens: KeychainTokenStore()))
            }
        #endif
        return MockBackend()
    }

    // MARK: Derived state

    public var assistantState: MascotState {
        MascotState.forAssistant(status: online ? status : nil, runs: runs, now: Date())
    }

    public var isJobRunning: Bool { runs.contains { $0.status == .running } }

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
    public var bots: [Bot] { BotList.build(plugins: plugins, catalog: .main) }

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
        return plugin(bot.id).map(statusLine) ?? AgentText.headline(assistantState)
    }

    /// The plugins that joined a Bot, e.g. 知识库Bot's GoodNotes, 课堂照片 and 视频.
    public func inputs(of bot: Bot) -> [PluginView] { bot.joined.compactMap(plugin) }

    /// The Agent is writing this Bot's answer.
    public func isReplying(_ bot: Bot) -> Bool { BotChat.isReplying(chats[bot.id] ?? []) }

    /// What the Bot said and the chat with it, oldest first.
    public func messages(_ bot: Bot) -> [BotMessage] {
        BotTimeline.messages(
            for: bot, today: today, digests: digests, todos: todos, runs: runs,
            reports: healthReports, chat: BotChat.messages(chats[bot.id] ?? []), now: Date(),
            calendar: calendar)
    }

    // MARK: Loading

    /// Loads every screen's data; failures leave the previous data and mark the App offline.
    @MainActor
    public func refresh() async {
        let backend = self.backend
        do {
            async let status = backend.status()
            async let today = backend.today()
            async let todos = backend.todos(status: nil)
            async let digests = backend.digests()
            async let runs = backend.runs()
            async let plugins = backend.plugins()
            async let reports = backend.healthReports()
            async let settings = backend.settings()
            let loaded = try await (status, today, todos, digests, runs, plugins, reports, settings)
            self.status = loaded.0
            self.today = loaded.1
            self.todos = loaded.2
            self.digests = loaded.3
            self.runs = loaded.4
            self.plugins = loaded.5
            self.healthReports = loaded.6
            self.settings = loaded.7
            self.online = loaded.0.online
            self.lastRefresh = Date()
        } catch {
            online = false
        }
        settled = true
        let state = assistantState
        if state != lastAssistantState {
            lastAssistantState = state
            stateSince = Date()
        }
        onRefresh?()
    }

    @MainActor
    public func loadCards(_ pluginID: String) async {
        do {
            cards[pluginID] = try await backend.cards(pluginID: pluginID)
        } catch {
            // A page reloads its cards after every refresh, cancelling a load still on its way:
            // keep what it shows instead of emptying it until the next refresh.
            guard !Task.isCancelled else { return }
            cards[pluginID] = []
        }
    }

    /// Refreshes every 30 s, every 2 s while a job runs. Calling it again does nothing.
    @MainActor
    public func start() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                let delay: UInt64 = self.isJobRunning ? 2 : 30
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
            }
        }
    }

    @MainActor
    public func stop() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: Actions

    @MainActor
    private func perform(_ failure: String, _ action: @MainActor () async throws -> Void) async {
        do {
            try await action()
        } catch {
            message = "\(failure)：\(Self.describe(error))"
        }
    }

    static func describe(_ error: Error) -> String {
        switch error as? APIError {
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
        if let index = todos.firstIndex(where: { $0.id == todo.id }) {
            todos[index] = todo
        }
        if var today, let index = today.todos.firstIndex(where: { $0.id == todo.id }) {
            if todo.status == .deleted {
                today.todos.remove(at: index)
            } else {
                today.todos[index] = todo
            }
            self.today = today
        }
    }

    @MainActor
    public func update(_ todo: Todo, _ patch: TodoPatch) async {
        guard !patch.isEmpty else { return }
        await perform("没能保存待办") {
            let updated = try await backend.updateTodo(id: todo.id, patch)
            replace(updated)
        }
    }

    @MainActor
    public func toggleDone(_ todo: Todo) async {
        let next: TodoStatus = todo.status == .completed ? .pending : .completed
        await update(todo, TodoPatch(status: next))
    }

    @MainActor
    public func delete(_ todo: Todo) async {
        await update(todo, .delete())
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
            let updated = try await backend.updatePlugin(id: id, PluginPatch(enabled: enabled))
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
            let updated = try await backend.updatePlugin(id: id, PluginPatch(config: config))
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
            chats[bot.id] = try await backend.chat(botID: bot.id)
        } catch {
            return  // keep what is shown; the refresh marks the App offline
        }
        if isReplying(bot) { pollChat(bot) }
    }

    @MainActor
    private func pollChat(_ bot: Bot) {
        guard chatPolls[bot.id] == nil else { return }
        chatPolls[bot.id] = Task { [weak self] in
            // Agents give up after the backend's timeout; stop asking after 15 minutes.
            for _ in 0..<600 {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                guard let self, !Task.isCancelled else { return }
                if let chat = try? await self.backend.chat(botID: bot.id) {
                    self.chats[bot.id] = chat
                    if !BotChat.isReplying(chat) { break }
                }
            }
            self?.chatPolls[bot.id] = nil
            await self?.refresh()  // the Agent may have added todos or records
        }
    }

    /// Sends a line to a Bot; the Agent on the Mac answers it.
    @MainActor
    public func send(_ text: String, to bot: Bot) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, bot.kind != .source else { return }
        await perform("没发出去") {
            let accepted = try await backend.sendChat(
                botID: bot.id, text: trimmed, botName: bot.name, choice: chatChoice)
            chats[bot.id, default: []].append(contentsOf: [accepted.message, accepted.reply])
            pollChat(bot)
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
        var sent = false
        await perform("没能保存这次记录") {
            _ = try await backend.uploadHealth(checkin.batch(for: day, now: Date(), calendar: calendar))
            sent = true
        }
        guard sent else { return false }
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
            settings = try await backend.updateSettings(patch)
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
                    try KeychainTokenStore().save(token)
                } catch {
                    message = "没能把令牌存进钥匙串：\(error)"
                    return
                }
            }
        #endif
        connection.save()
        self.connection = connection
        backend = Self.makeBackend(connection)
        cards = [:]
        chatPolls.values.forEach { $0.cancel() }
        chatPolls = [:]
        chats = [:]
        await refresh()
    }

    /// Whether a token is saved on this device (never shown).
    public var hasToken: Bool {
        #if canImport(Security)
            return (try? KeychainTokenStore().currentToken()) != nil
        #else
            return false
        #endif
    }
}
