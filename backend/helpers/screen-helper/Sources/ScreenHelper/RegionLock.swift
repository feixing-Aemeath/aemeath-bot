import CoreGraphics
import Foundation

/// The regions page 0 of one chat read found, reused for the pages after it.
///
/// Regions are found from the window's dividers, and chat content can look like one. On
/// 2026-11-13 a class group's second page found an extra "member panel" edge at about three
/// quarters of the window: that page's crop lost its right side (867 instead of 1043 pixels
/// wide), no line matched the page before it, and the read stopped with a history gap after
/// two screens. One read scrolls a single window that keeps its shape, so the first page's
/// regions hold for the rest of it. A window of another pixel size, or a lock older than a
/// read can take, is not trusted and the regions are found again.
struct RegionLock: Equatable {
    let width: Int
    let height: Int
    let regions: Regions
    let savedAt: Date

    /// A read of 20 pages takes a few minutes; the backend's whole round stops at 9.
    static let maxAge: TimeInterval = 15 * 60

    func fits(width: Int, height: Int, now: Date = Date()) -> Bool {
        let age = now.timeIntervalSince(savedAt)
        return width == self.width && height == self.height && age >= 0 && age < Self.maxAge
    }

    var json: [String: Any] {
        func rect(_ r: CGRect) -> [Double] { [r.minX, r.minY, r.width, r.height].map(Double.init) }
        return [
            "width": width, "height": height, "saved_at": savedAt.timeIntervalSince1970,
            "list": rect(regions.list), "header": rect(regions.header), "chat": rect(regions.chat),
            "found": regions.found,
        ]
    }

    init(width: Int, height: Int, regions: Regions, savedAt: Date = Date()) {
        self.width = width
        self.height = height
        self.regions = regions
        self.savedAt = savedAt
    }

    init?(json: [String: Any]) {
        func rect(_ key: String) -> CGRect? {
            guard let v = json[key] as? [Double], v.count == 4 else { return nil }
            return CGRect(x: v[0], y: v[1], width: v[2], height: v[3])
        }
        guard let width = json["width"] as? Int, let height = json["height"] as? Int,
              let saved = json["saved_at"] as? Double,
              let list = rect("list"), let header = rect("header"), let chat = rect("chat")
        else { return nil }
        self.init(
            width: width, height: height,
            regions: Regions(list: list, header: header, chat: chat, found: json["found"] as? [String] ?? []),
            savedAt: Date(timeIntervalSince1970: saved))
    }

    /// The locked regions when they fit this screenshot, else the ones found in it.
    static func regions(locked: RegionLock?, width: Int, height: Int, now: Date = Date(),
                        detect: () -> Regions) -> Regions {
        if let locked, locked.fits(width: width, height: height, now: now) { return locked.regions }
        return detect()
    }
}

/// One small file per app and chat, next to the screenshots.
struct RegionStore {
    let file: URL

    func load() -> RegionLock? {
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return RegionLock(json: json)
    }

    func save(_ lock: RegionLock) {
        try? FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        guard let data = try? JSONSerialization.data(withJSONObject: lock.json) else { return }
        try? data.write(to: file, options: .atomic)
    }
}
