import Foundation

/// When the App was last in front (it started, came back, went to the background), so that
/// coming back to the iPhone after more than an hour opens 仪表盘 instead of the chat that was
/// open.
public enum AwayClock {
    static let key = "assistant.lastSeen"
    public static let limit: TimeInterval = 3600

    public static func mark(_ defaults: UserDefaults = .standard, now: Date = Date()) {
        defaults.set(now.timeIntervalSince1970, forKey: key)
    }

    /// Away for more than `limit`, or never seen before (a first start).
    public static func wasAway(_ defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
        guard let stored = defaults.object(forKey: key) as? Double else { return true }
        return now.timeIntervalSince1970 - stored > limit
    }

    /// At start: `page` becomes the page kept under `pageKey` when the App was away, then the
    /// clock starts again (SwiftUI may make a view more than once, and only the first counts).
    public static func openIfAway(
        _ page: String, key pageKey: String, _ defaults: UserDefaults = .standard, now: Date = Date()
    ) {
        if wasAway(defaults, now: now) { defaults.set(page, forKey: pageKey) }
        mark(defaults, now: now)
    }
}
