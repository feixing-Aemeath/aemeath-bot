import Foundation

/// Upload progress for one backend; HealthKit anchors are stored as opaque archived data.
public struct HealthSyncState {
    private let defaults: UserDefaults
    private let prefix: String

    public init(backend: String, defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let address = backend.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        prefix = "health.backend.\(Data(address.utf8).base64EncodedString())."
    }

    public func anchor(for kind: String) -> Data? { defaults.data(forKey: prefix + "anchor." + kind) }

    public func saveAnchor(_ data: Data, for kind: String) {
        defaults.set(data, forKey: prefix + "anchor." + kind)
    }

    public var lastSync: Date? {
        let stamp = defaults.double(forKey: prefix + "lastSync")
        return stamp > 0 ? Date(timeIntervalSince1970: stamp) : nil
    }

    public var lastCount: Int { defaults.integer(forKey: prefix + "lastCount") }

    public func finished(at date: Date, count: Int) {
        defaults.set(date.timeIntervalSince1970, forKey: prefix + "lastSync")
        defaults.set(count, forKey: prefix + "lastCount")
    }
}
