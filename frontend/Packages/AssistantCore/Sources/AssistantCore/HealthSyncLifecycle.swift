import Foundation

/// Runs the same incremental sync at launch and when the phone returns to the foreground.
@MainActor
public final class HealthSyncLifecycle {
    private let sync: @MainActor () async -> Void
    private let center: NotificationCenter
    private var observer: NSObjectProtocol?

    public init(
        center: NotificationCenter = .default, foreground: Notification.Name,
        sync: @escaping @MainActor () async -> Void
    ) {
        self.sync = sync
        self.center = center
        observer = center.addObserver(forName: foreground, object: nil, queue: .main) { _ in
            Task { @MainActor in await sync() }
        }
    }

    deinit {
        if let observer { center.removeObserver(observer) }
    }

    public func start() async { await sync() }
}
