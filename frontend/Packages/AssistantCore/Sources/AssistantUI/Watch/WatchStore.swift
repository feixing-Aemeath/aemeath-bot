#if os(watchOS)
    import AssistantCore
    import Foundation
    import WatchConnectivity
    import WidgetKit

    /// The watch's data: the latest snapshot from the iPhone. Refreshing and completing go
    /// through the iPhone, which talks to the Mac; the watch has no token and no backend
    /// address.
    public final class WatchStore: NSObject, ObservableObject, WCSessionDelegate {
        @Published public private(set) var snapshot: WatchSnapshot?
        @Published public private(set) var reachable = false
        @Published public private(set) var waiting = false
        @Published public var message: String?

        private let defaults: UserDefaults
        private static let snapshotKey = "watch.snapshot"

        public override init() {
            defaults = ComplicationData.defaults()
            super.init()
            // Show the last snapshot right away, even before the iPhone answers.
            if let data = defaults.data(forKey: Self.snapshotKey) {
                snapshot = try? JSONDecoder.assistant().decode(WatchSnapshot.self, from: data)
            }
        }

        private var session: WCSession? { WCSession.isSupported() ? WCSession.default : nil }

        public func start() {
            guard let session, session.delegate == nil else { return }
            session.delegate = self
            session.activate()
        }

        @MainActor
        func apply(_ snapshot: WatchSnapshot) {
            self.snapshot = snapshot
            if let data = try? JSONEncoder.assistant().encode(snapshot) {
                defaults.set(data, forKey: Self.snapshotKey)
            }
            ComplicationData(remaining: snapshot.remaining, updated: snapshot.generatedAt)
                .save(to: defaults)
            WidgetCenter.shared.reloadAllTimelines()
        }

        /// Asks the iPhone to refresh from the Mac.
        @MainActor
        public func refresh() {
            guard let session, session.isReachable else {
                message = "iPhone 不在附近，显示的是上次同步的内容"
                return
            }
            waiting = true
            session.sendMessage(
                WatchRequest.refresh.message,
                replyHandler: { reply in
                    Task { @MainActor in
                        self.waiting = false
                        if let snapshot = WatchSnapshot(message: reply) { self.apply(snapshot) }
                    }
                },
                errorHandler: { _ in
                    Task { @MainActor in
                        self.waiting = false
                        self.message = "没能连上 iPhone"
                    }
                })
        }

        /// Ticks a todo off at once; the iPhone completes it now, or later if out of reach.
        @MainActor
        public func toggle(_ item: WatchSnapshot.Item) {
            guard let snapshot, let session else { return }
            let done = !item.done
            apply(snapshot.setting(item.id, done: done))
            let request = WatchRequest.setDone(id: item.id, done: done)
            if session.isReachable {
                session.sendMessage(
                    request.message,
                    replyHandler: { reply in
                        Task { @MainActor in
                            if let snapshot = WatchSnapshot(message: reply) { self.apply(snapshot) }
                        }
                    },
                    errorHandler: { _ in
                        // Queued delivery instead; the iPhone applies it when it gets it.
                        _ = session.transferUserInfo(request.message)
                    })
            } else {
                _ = session.transferUserInfo(request.message)
            }
        }

        // MARK: WCSessionDelegate

        public func session(
            _ session: WCSession, activationDidCompleteWith state: WCSessionActivationState,
            error: Error?
        ) {
            let context = session.receivedApplicationContext
            let reachable = session.isReachable
            Task { @MainActor in
                self.reachable = reachable
                if let snapshot = WatchSnapshot(message: context) { self.apply(snapshot) }
            }
        }

        public func session(
            _ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]
        ) {
            guard let snapshot = WatchSnapshot(message: applicationContext) else { return }
            Task { @MainActor in self.apply(snapshot) }
        }

        public func sessionReachabilityDidChange(_ session: WCSession) {
            let reachable = session.isReachable
            Task { @MainActor in self.reachable = reachable }
        }
    }
#endif
