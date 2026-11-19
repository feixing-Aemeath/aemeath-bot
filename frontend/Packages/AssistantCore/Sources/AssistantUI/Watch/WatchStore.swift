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
        /// What was just ticked off from the first page, while it can still be taken back.
        @Published public private(set) var justDone: WatchSnapshot.Item?

        private let defaults: UserDefaults
        private var justDoneTimer: Task<Void, Never>?
        /// Added to the system clock; only a debug launch with `-watch.mock` sets it.
        private var clockOffset: TimeInterval = 0
        private var mocked = false
        /// Old enough to ask the iPhone again when the App comes up.
        private static let staleAfter: TimeInterval = 10 * 60

        public override init() {
            defaults = ComplicationData.defaults()
            super.init()
            #if DEBUG
                if let name = UserDefaults.standard.string(forKey: "watch.mock"),
                    let mock = WatchMock(argument: name)
                {
                    mocked = true
                    reachable = mock != .iphoneAway
                    let now = mock.now(on: Date(), calendar: DueText.calendar())
                    clockOffset = now.timeIntervalSince(Date())
                    snapshot = mock.snapshot(now: now, calendar: DueText.calendar())
                    if UserDefaults.standard.bool(forKey: "watch.justDone") { justDone = snapshot?.todos.first }
                    // So that complications on a simulator's face have the same invented day.
                    snapshot?.save(to: defaults)
                    WidgetCenter.shared.reloadAllTimelines()
                    return
                }
            #endif
            // Show the last snapshot right away, even before the iPhone answers.
            snapshot = WatchSnapshot.load(from: defaults)
        }

        private var session: WCSession? { WCSession.isSupported() ? WCSession.default : nil }

        public func start() {
            guard !mocked, let session, session.delegate == nil else { return }
            session.delegate = self
            session.activate()
        }

        /// The time the pages work from: the clock, except in a debug launch with a mock state.
        public func now(_ date: Date = Date()) -> Date { date.addingTimeInterval(clockOffset) }

        @MainActor
        func apply(_ incoming: WatchSnapshot) {
            let snapshot = self.snapshot.map { incoming.keepingDone(from: $0, calendar: DueText.calendar()) } ?? incoming
            self.snapshot = snapshot
            guard !mocked else { return }
            snapshot.save(to: defaults)
            ComplicationData(remaining: snapshot.remaining, updated: snapshot.generatedAt)
                .save(to: defaults)
            WidgetCenter.shared.reloadAllTimelines()
        }

        /// Asks the iPhone to refresh from the Mac.
        @MainActor
        public func refresh(quietly: Bool = false) {
            #if DEBUG
                if mocked {
                    waiting = true
                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: 800_000_000)
                        self.waiting = false
                    }
                    return
                }
            #endif
            guard let session, session.isReachable else {
                if !quietly { message = "iPhone 不在附近，显示的是上次同步的内容" }
                return
            }
            message = nil
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
                        if !quietly { self.message = "没能连上 iPhone" }
                    }
                })
        }

        /// Asks the iPhone for news when the wrist comes up on a snapshot from a while ago, so
        /// that the pages are current without anybody asking. Quiet when the iPhone is away:
        /// the pages say how old they are.
        @MainActor
        public func refreshIfStale() {
            guard !mocked, !waiting, let session, session.isReachable else { return }
            if let snapshot, Date().timeIntervalSince(snapshot.generatedAt) < Self.staleAfter { return }
            refresh(quietly: true)
        }

        /// Ticks a todo off at once; the iPhone completes it now, or later if out of reach.
        @MainActor
        public func toggle(_ item: WatchSnapshot.Item) {
            setDone(item.id, done: !item.done)
        }

        @MainActor
        private func setDone(_ id: String, done: Bool) {
            if let snapshot { apply(snapshot.setting(id, done: done)) }
            guard !mocked, let session else { return }
            let request = WatchRequest.setDone(id: id, done: done)
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

        /// Ticks off the first page's todo, which that page shows as done for a moment, with an
        /// undo, before going on to whatever comes next.
        @MainActor
        public func completeFromFirstPage(_ item: WatchSnapshot.Item) {
            guard !item.done else { return }
            toggle(item)
            justDone = item
            justDoneTimer?.cancel()
            justDoneTimer = Task { @MainActor in
                try? await Task.sleep(nanoseconds: 3_500_000_000)
                guard !Task.isCancelled else { return }
                self.justDone = nil
            }
        }

        /// Takes the first page's tick back.
        @MainActor
        public func undoJustDone() {
            guard let item = justDone else { return }
            justDoneTimer?.cancel()
            justDone = nil
            setDone(item.id, done: false)
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
                self.refreshIfStale()
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
