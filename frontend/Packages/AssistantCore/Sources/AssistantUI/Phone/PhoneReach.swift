#if os(iOS)
    import AssistantCore
    import BackgroundTasks
    import SwiftUI
    import UIKit
    import UserNotifications
    import WidgetKit

    /// Platform glue only: policy, identifiers and presentation are shared, testable core code.
    @MainActor
    public final class PhoneReach: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
        public static let shared = PhoneReach()
        @Published public private(set) var preferences = ReachPreferences.load()
        @Published public private(set) var authorization: UNAuthorizationStatus = .notDetermined
        @Published public private(set) var problem: String?
        private weak var store: AppStore?
        private let center = UNUserNotificationCenter.current()
        private var work: Task<Bool, Never>?
        private var lastFetch = Date.distantPast
        private var foregroundObserver: NSObjectProtocol?
        private var backgroundObserver: NSObjectProtocol?
        private var replying = Set<String>()
        private var pendingRoute: URL?
        private var categories = Set<UNNotificationCategory>()
        public static let routeNotification = Notification.Name("phone.reach.route")
        private var refreshID: String { (Bundle.main.bundleIdentifier ?? "assistant") + ".refresh" }

        public func attach(_ store: AppStore) {
            self.store = store
            center.delegate = self
            let previous = store.onRefresh
            store.onRefresh = { [weak self] in
                previous?()
                self?.storeRefreshed()
            }
            BGTaskScheduler.shared.register(forTaskWithIdentifier: refreshID, using: nil) { task in
                Task { @MainActor in PhoneReach.shared.run(task) }
            }
            foregroundObserver = NotificationCenter.default.addObserver(
                forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main
            ) { _ in
                Task { @MainActor in
                    await PhoneReach.shared.checkAuthorization()
                    _ = await PhoneReach.shared.refresh()
                    if let route = PhoneReach.shared.pendingRoute {
                        PhoneReach.shared.pendingRoute = nil
                        PhoneReach.shared.open(route)
                    }
                }
            }
            backgroundObserver = NotificationCenter.default.addObserver(
                forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main
            ) { _ in
                Task { @MainActor in PhoneReach.shared.schedule() }
            }
            Task {
                await checkAuthorization()
                schedule()
            }
        }

        public func checkAuthorization() async {
            authorization = await center.notificationSettings().authorizationStatus
        }

        public func requestAccess() async {
            do {
                _ = try await center.requestAuthorization(options: [.alert, .badge, .sound])
                problem = nil
            } catch { problem = "没能打开通知授权，请稍后重试。" }
            await checkAuthorization()
        }

        public func set(_ kind: ReachKind, enabled: Bool) async {
            preferences[kind] = enabled
            preferences.save()
            if enabled, authorization == .notDetermined { await requestAccess() }
            if !enabled { await remove(kind) }
            _ = await refresh()
        }

        public func setTime(_ date: Date) async {
            preferences.hour = Calendar.current.component(.hour, from: date)
            preferences.minute = Calendar.current.component(.minute, from: date)
            preferences.save()
            await remove(.departure)
            _ = await refresh()
        }

        private func remove(_ kind: ReachKind) async {
            let pending = await center.pendingNotificationRequests().filter {
                $0.content.userInfo["kind"] as? String == kind.rawValue
            }
            center.removePendingNotificationRequests(withIdentifiers: pending.map(\.identifier))
            let delivered = await center.deliveredNotifications().filter {
                $0.request.content.userInfo["kind"] as? String == kind.rawValue
            }
            center.removeDeliveredNotifications(withIdentifiers: delivered.map { $0.request.identifier })
        }

        private func storeRefreshed() {
            guard let store else { return }
            // On a cold launch the URL can arrive before the plugin list. Resolve joined
            // plugins once the store has learned which Bot owns them.
            if store.settled, let route = pendingRoute {
                pendingRoute = nil
                open(route)
            }
            saveWidget(today: store.today, updated: store.todayUpdatedAt, online: store.online, plugins: store.plugins)
            // Poll all Bots, including pages never opened. Do not extend the screen's refresh or
            // start overlapping requests when its running-job poll fires every two seconds. Every
            // five minutes is enough while the App is open: this asks every Bot for its chat, a
            // dozen requests, and what it finds on screen already is not announced anyway.
            guard Date().timeIntervalSince(lastFetch) >= 300 else { return }
            Task { _ = await refresh() }
        }

        private func saveWidget(today: Today?, updated: Date?, online: Bool, plugins: [PluginView]) {
            guard let today else { return }  // Preserve the last successful snapshot during outages.
            let snapshot = PhoneWidgetSnapshot.make(
                today: today, updatedAt: updated, online: online, plugins: plugins, now: Date(), calendar: .current)
            let defaults = ComplicationData.defaults()
            if PhoneWidgetSnapshot.load(from: defaults) != snapshot {
                PhoneWidgetSnapshot.save(snapshot, to: defaults)
                WidgetCenter.shared.reloadTimelines(ofKind: "phone-now")
            }
        }

        /// One short refresh path for foreground, BGAppRefreshTask and HealthKit delivery.
        /// Cancellation propagates through URLSession; nothing polls or retries in the background.
        @discardableResult
        public func refresh() async -> Bool {
            if let work {
                return await withTaskCancellationHandler {
                    await work.value
                } onCancel: {
                    work.cancel()
                }
            }
            guard let store else { return false }
            let backend = store.backend
            let connection = store.connection
            guard !connection.useMockData else {
                saveWidget(
                    today: store.today, updated: store.todayUpdatedAt, online: store.online, plugins: store.plugins)
                await clearNotifications()
                return true
            }
            lastFetch = Date()
            let task = Task { @MainActor [weak self] () -> Bool in
                guard let self else { return false }
                do {
                    let input = try await ReachRefresh.load(backend: backend)
                    try Task.checkCancellation()
                    guard store.connection == connection else { return false }
                    saveWidget(today: input.input.today, updated: Date(), online: true, plugins: input.plugins)
                    await publish(input.input, scope: ReachPolicy.identity(connection.baseURL))
                    guard store.connection == connection, !Task.isCancelled else { return false }
                    // The same queue as the visible App; the 20 s task budget cancels requests
                    // still in flight. A failed/cancelled row remains available for next time.
                    await store.refreshShareInbox(reached: true, pluginsLoaded: false)
                    return !Task.isCancelled
                } catch { return false }
            }
            work = task
            let timer = Task { @MainActor in
                try? await Task.sleep(for: .seconds(20))
                if !Task.isCancelled { task.cancel() }
            }
            let result = await withTaskCancellationHandler {
                await task.value
            } onCancel: {
                task.cancel()
            }
            timer.cancel()
            work = nil
            return result
        }

        private func clearNotifications() async {
            let pending = await center.pendingNotificationRequests().filter { $0.identifier.hasPrefix("reach-") }
            center.removePendingNotificationRequests(withIdentifiers: pending.map(\.identifier))
            let delivered = await center.deliveredNotifications().filter { $0.request.identifier.hasPrefix("reach-") }
            center.removeDeliveredNotifications(withIdentifiers: delivered.map { $0.request.identifier })
        }

        private func publish(_ input: ReachInput, scope: String) async {
            await checkAuthorization()
            guard authorization == .authorized || authorization == .provisional || authorization == .ephemeral else {
                return
            }
            let defaults = UserDefaults.standard
            let key = "phone.reach.ledger." + scope
            var ledger = ReachLedger.load(defaults, key: key)
            let notices = ReachPolicy.notices(
                input, preferences: preferences, ledger: ledger, now: Date(), calendar: .current,
                botPreferences: store?.botPreferences ?? BotPreferences())
            // Remove answered or superseded questions and obsolete departure requests. Keep
            // categories for delivered notifications, whose buttons must survive a later refresh.
            let validQuestions = Set(
                input.chats.compactMap { id, lines in
                    BotChat.replyQuestion(lines).map { "question-" + ReachPolicy.identity(id + ":" + $0.id) }
                })
            let delivered = await center.deliveredNotifications()
            let pending = await center.pendingNotificationRequests()
            let requests = pending + delivered.map(\.request)
            let obsolete = requests.filter { request in
                guard request.identifier.hasPrefix("reach-"), let kind = request.content.userInfo["kind"] as? String,
                    let noticeID = request.content.userInfo["noticeID"] as? String
                else { return false }
                if request.content.userInfo["scope"] as? String != scope { return true }
                if kind == ReachKind.question.rawValue,
                    input.chats[request.content.userInfo["bot"] as? String ?? ""] != nil
                {
                    return !validQuestions.contains(noticeID) || !preferences.question
                }
                if kind == ReachKind.departure.rawValue {
                    // A newer forecast replaces a day's request under the same identifier, so
                    // nothing set for another day is ever in the way: a refresh after midnight
                    // used to remove this morning's reminder because only tomorrow's was "valid".
                    return !preferences.departure
                }
                return ReachKind(rawValue: kind).map { !preferences[$0] } ?? false
            }.map(\.identifier)
            center.removePendingNotificationRequests(withIdentifiers: obsolete)
            center.removeDeliveredNotifications(withIdentifiers: obsolete)
            categories = Set(
                (await center.notificationCategories()).filter { category in
                    requests.contains {
                        $0.content.categoryIdentifier == category.identifier && !obsolete.contains($0.identifier)
                    }
                })
            let appActive = UIApplication.shared.applicationState == .active
            let openPage = UserDefaults.standard.string(forKey: "assistant.phone.bot")
            for notice in notices {
                guard !Task.isCancelled, preferences[notice.kind] else { break }
                if ReachPolicy.staysSilent(notice, appActive: appActive, openPage: openPage) {
                    for id in [notice.id] + notice.covers { ledger.record(id, at: Date()) }
                    ledger.save(defaults, key: key)
                    continue
                }
                let content = UNMutableNotificationContent()
                content.title = notice.title
                content.body = notice.body
                content.sound = .default
                content.userInfo = [
                    "kind": notice.kind.rawValue, "noticeID": notice.id, "url": notice.destination.url.absoluteString,
                    "scope": scope,
                ]
                if case .bot(let id) = notice.destination { content.userInfo["bot"] = id }
                if let questionID = notice.questionID {
                    content.userInfo["question"] = questionID
                    content.userInfo["replies"] = notice.replies
                    let categoryID = "reach-" + scope + "-" + notice.id
                    categories.insert(
                        UNNotificationCategory(
                            identifier: categoryID,
                            actions: notice.replies.enumerated().map {
                                UNNotificationAction(identifier: "reply-\($0.offset)", title: $0.element, options: [])
                            }, intentIdentifiers: [], options: []))
                    content.categoryIdentifier = categoryID
                }
                center.setNotificationCategories(categories)
                let trigger = notice.fireAt.map {
                    UNCalendarNotificationTrigger(
                        dateMatching: Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: $0),
                        repeats: false)
                }
                do {
                    try await center.add(
                        UNNotificationRequest(
                            identifier: "reach-" + scope + "-" + notice.id, content: content, trigger: trigger))
                    if notice.fireAt == nil {
                        for id in [notice.id] + notice.covers { ledger.record(id, at: Date()) }
                        ledger.save(defaults, key: key)
                    }
                } catch { problem = "有一条通知没能排好，下次刷新时会重试。" }
            }
        }

        public func schedule() {
            let request = BGAppRefreshTaskRequest(identifier: refreshID)
            request.earliestBeginDate = Date(timeIntervalSinceNow: 30 * 60)
            try? BGTaskScheduler.shared.submit(request)
        }

        private func run(_ task: BGTask) {
            schedule()
            let completion = RefreshCompletion(task)
            let operation = Task { @MainActor in
                let success = await refresh()
                completion.finish(success)
            }
            task.expirationHandler = {
                operation.cancel()
                Task { @MainActor in completion.finish(false) }
            }
        }

        public func open(_ url: URL) {
            guard let destination = PhoneDestination(url: url) else { return }
            let id: String
            switch destination {
            case .dashboard: id = "dashboard"
            case .bot(let botID):
                if store?.settled != true { pendingRoute = url }
                // A joined plugin (e.g. timetable under homework) opens its actual Bot.
                id = store?.bots.first { $0.id == botID || $0.pluginIDs.contains(botID) }?.id ?? botID
            }
            AwayClock.mark()
            UserDefaults.standard.set(id, forKey: "assistant.phone.bot")
            UserDefaults.standard.set(id, forKey: PadPresentation.botStorageKey)
            NotificationCenter.default.post(name: Self.routeNotification, object: id)
        }

        nonisolated public func userNotificationCenter(
            _ center: UNUserNotificationCenter, willPresent notification: UNNotification,
            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
        ) {
            completionHandler([.banner, .sound, .list])
        }

        nonisolated public func userNotificationCenter(
            _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
            withCompletionHandler completionHandler: @escaping () -> Void
        ) {
            Task { @MainActor in
                defer { completionHandler() }
                let info = response.notification.request.content.userInfo
                if response.actionIdentifier == UNNotificationDefaultActionIdentifier,
                    let raw = info["url"] as? String, let url = URL(string: raw)
                {
                    if UIApplication.shared.applicationState != .active { self.pendingRoute = url }
                    self.open(url)
                } else if response.actionIdentifier.hasPrefix("reply-") {
                    await self.sendReply(response, info: info)
                }
            }
        }

        private func sendReply(_ response: UNNotificationResponse, info: [AnyHashable: Any]) async {
            guard let store, !store.connection.useMockData,
                let scope = info["scope"] as? String, scope == ReachPolicy.identity(store.connection.baseURL),
                let bot = info["bot"] as? String, let question = info["question"] as? String,
                let replies = info["replies"] as? [String],
                let index = Int(response.actionIdentifier.dropFirst(6)), replies.indices.contains(index)
            else { return }
            let key = "phone.reach.actions." + scope
            var ledger = ReachLedger.load(.standard, key: key)
            let actionKey = ReachPolicy.identity(bot + ":" + question)
            guard !replying.contains(actionKey), !ledger.contains(actionKey) else { return }
            replying.insert(actionKey)
            defer { replying.remove(actionKey) }
            let backend = store.backend
            let operation = Task { @MainActor in
                do {
                    let chat = try await backend.chat(botID: bot, before: nil).items
                    guard
                        let reply = ReachPolicy.reply(
                            replies[index], questionID: question, chat: chat, now: Date(), calendar: .current)
                    else { return }
                    try Task.checkCancellation()
                    _ = try await backend.sendChat(
                        botID: bot, text: reply, botName: store.bots.first { $0.id == bot }?.name,
                        choice: store.chatChoice)
                    ledger.record(actionKey, at: Date())
                    ledger.save(.standard, key: key)
                    center.removeDeliveredNotifications(withIdentifiers: [response.notification.request.identifier])
                    problem = nil
                } catch { problem = "回复没有确认送达，请打开聊天查看后再试。" }
            }
            let timer = Task {
                try? await Task.sleep(for: .seconds(15))
                if !Task.isCancelled { operation.cancel() }
            }
            await operation.value
            timer.cancel()
        }
    }

    /// Exactly once even when expiration races the final network response.
    @MainActor private final class RefreshCompletion {
        private var task: BGTask?
        init(_ task: BGTask) { self.task = task }
        func finish(_ success: Bool) {
            guard let task else { return }
            self.task = nil
            task.expirationHandler = nil
            task.setTaskCompleted(success: success)
        }
    }
#endif
