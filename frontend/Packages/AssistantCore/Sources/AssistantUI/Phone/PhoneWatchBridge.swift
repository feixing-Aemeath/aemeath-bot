#if os(iOS)
    import AssistantCore
    import Foundation
    import WatchConnectivity

    /// The iPhone side of the watch link. After every refresh it sends the watch a snapshot
    /// (today's todos, how many are left, the assistant's state, today's courses, readiness and
    /// last night's sleep); it answers the watch's
    /// requests to refresh and to complete a todo. The watch itself never contacts the Mac.
    public final class PhoneWatchBridge: NSObject, WCSessionDelegate {
        public static let shared = PhoneWatchBridge()

        private weak var store: AppStore?
        private var session: WCSession? { WCSession.isSupported() ? WCSession.default : nil }

        /// Call once at launch, also when iOS starts the App in the background for a watch
        /// request.
        public func attach(_ store: AppStore) {
            guard let session else { return }
            self.store = store
            store.onRefresh = { [weak self] in self?.push() }
            session.delegate = self
            session.activate()
        }

        @MainActor
        func snapshot() -> WatchSnapshot? {
            guard let store else { return nil }
            return WatchSnapshot.make(
                today: store.today, online: store.online, state: store.assistantState, now: Date(),
                calendar: store.calendar, plugins: store.plugins)
        }

        /// Sends the latest snapshot; only the newest one is delivered if the watch is away.
        @MainActor
        func push() {
            guard let session, session.activationState == .activated, session.isPaired,
                session.isWatchAppInstalled, let message = try? snapshot()?.message()
            else { return }
            try? session.updateApplicationContext(message)
        }

        @MainActor
        func handle(_ request: WatchRequest) async {
            guard let store else { return }
            switch request {
            case .refresh:
                await store.refresh()
            case .setDone(let id, let done):
                let todo =
                    store.today?.todos.first(where: { $0.id == id })
                    ?? store.todos.first(where: { $0.id == id })
                if let todo, (todo.status == .completed) != done {
                    await store.toggleDone(todo)
                }
                await store.refresh()
            }
        }

        // MARK: WCSessionDelegate

        public func session(
            _ session: WCSession, activationDidCompleteWith state: WCSessionActivationState,
            error: Error?
        ) {
            Task { @MainActor in self.push() }
        }

        public func sessionDidBecomeInactive(_ session: WCSession) {}

        public func sessionDidDeactivate(_ session: WCSession) {
            // Switching to another watch: activate again for the new one.
            session.activate()
        }

        /// The watch app was just installed (it comes with the iPhone App) or the watch was
        /// paired: send it today's snapshot right away instead of at the next refresh.
        public func sessionWatchStateDidChange(_ session: WCSession) {
            Task { @MainActor in self.push() }
        }

        /// "Refresh" or "complete" while the watch waits for an answer.
        public func session(
            _ session: WCSession, didReceiveMessage message: [String: Any],
            replyHandler: @escaping ([String: Any]) -> Void
        ) {
            guard let request = WatchRequest(message: message) else {
                replyHandler([:])
                return
            }
            Task { @MainActor in
                await self.handle(request)
                replyHandler((try? self.snapshot()?.message()) ?? [:])
            }
        }

        /// Completions made while the iPhone was out of reach arrive later as user info.
        public func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
            guard let request = WatchRequest(message: userInfo) else { return }
            Task { @MainActor in await self.handle(request) }
        }
    }
#endif
