import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit

/// A screenshot of the chat app's main window and the regions found in it, in pixels with the
/// origin at the top left:
///
///     ┌────┬──────────┬───────────────────────┐
///     │nav │ 搜索      │ chat header (title)   │
///     │    │ list     ├───────────────────────┤
///     │    │          │ chat (messages)       │
///     │    │          ├───────────────────────┤
///     │    │          │ input box             │
///     └────┴──────────┴───────────────────────┘
///
/// The columns and the two horizontal lines are found from the window's own dividers: a
/// column (row) where the colour changes along most of the window's height (width). Content
/// such as text or avatars never does that. The fractions in `Regions.fallback` are used when
/// a divider is not found.
struct WindowShot {
    let windowID: CGWindowID
    let image: CGImage
    /// The window in global screen points (the coordinates input events use).
    let frame: CGRect
    let regions: Regions

    /// `locked`: the regions page 0 of this read found (see RegionLock), used when they fit.
    static func take(of app: App, locked: RegionLock? = nil) async throws -> WindowShot {
        guard app.running != nil else { throw HelperError.appNotRunning }
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        } catch {
            throw HelperError.screenRecordingDenied
        }
        // The main window: the largest normal window of the app (not menus, tooltips or the
        // login window's small panels).
        let windows = content.windows.filter {
            $0.owningApplication?.bundleIdentifier == app.bundleID && $0.windowLayer == 0
                && $0.frame.width >= 500 && $0.frame.height >= 400
        }
        guard let window = windows.max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height })
        else { throw HelperError.windowNotFound }
        let image = try await capture(window)
        let regions = RegionLock.regions(locked: locked, width: image.width, height: image.height) {
            Regions.detect(Pixels(image))
        }
        return WindowShot(windowID: window.windowID, image: image, frame: window.frame, regions: regions)
    }

    /// The window alone, at its pixel size, also where other windows cover it.
    static func capture(_ window: SCWindow) async throws -> CGImage {
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let configuration = SCStreamConfiguration()
        let scale = CGFloat(filter.pointPixelScale)
        configuration.width = Int(filter.contentRect.width * scale)
        configuration.height = Int(filter.contentRect.height * scale)
        configuration.showsCursor = false
        configuration.ignoreShadowsSingleWindow = true
        do {
            return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
        } catch {
            throw HelperError.captureFailed
        }
    }

    /// Guard global input with the captured window's identity, frame and hit-test. A
    /// covered, moved or background window must not redirect our click/scroll elsewhere.
    func checkInput(app: App, at point: CGPoint) throws {
        try Permissions.checkInput()
        if let session = CGSessionCopyCurrentDictionary() as? [String: Any] {
            if session["kCGSessionLoginDoneKey"] as? Bool == false { throw HelperError.loginWindow }
            if session["kCGSSessionOnConsoleKey"] as? Bool == false { throw HelperError.sessionInactive }
            if session["CGSSessionScreenIsLocked"] as? Bool == true { throw HelperError.locked }
        }
        guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier == app.bundleID,
              let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                                       kCGNullWindowID) as? [[String: Any]],
              Self.inputTarget(windows: windows, at: point, windowID: windowID, frame: frame,
                               passThrough: Self.dockProcesses())
        else { throw HelperError.windowObscured }
    }

    /// The Dock keeps a transparent window over the whole screen, above every app's window
    /// (layer 20), and lets clicks through. Counted as cover it "obscured" every point of
    /// every window: seen on the Mac this runs on, where it is the second window from the
    /// front at 3200x1800.
    static func dockProcesses() -> Set<pid_t> {
        Set(NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.dock").map(\.processIdentifier))
    }

    /// Whether a click at `point` lands in the captured window: the frontmost window there
    /// must be that one, still where it was. `passThrough` processes' windows above the normal
    /// layer are skipped (the Dock's overlay); a normal window of any process still covers.
    static func inputTarget(windows: [[String: Any]], at point: CGPoint,
                            windowID: CGWindowID, frame: CGRect, passThrough: Set<pid_t> = []) -> Bool {
        guard let hit = windows.first(where: { window in
            guard let bounds = window[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
                  (window[kCGWindowAlpha as String] as? Double ?? 1) > 0, rect.contains(point)
            else { return false }
            if let owner = (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
               passThrough.contains(owner), (window[kCGWindowLayer as String] as? Int ?? 0) != 0 {
                return false
            }
            return true
        }), hit[kCGWindowNumber as String] as? UInt32 == windowID,
              let bounds = hit[kCGWindowBounds as String] as? [String: Any],
              CGRect(dictionaryRepresentation: bounds as CFDictionary) == frame
        else { return false }
        return true
    }

    func crop(_ rect: CGRect) -> CGImage {
        image.cropping(to: rect.integral) ?? image
    }

    /// A pixel of the screenshot as a point on screen.
    func screenPoint(_ pixel: CGPoint) -> CGPoint {
        let scale = CGFloat(image.width) / frame.width
        return CGPoint(x: frame.minX + pixel.x / scale, y: frame.minY + pixel.y / scale)
    }

    var pointsPerPixel: CGFloat { frame.width / CGFloat(image.width) }

    func describe() -> [String: Any] {
        func fractions(_ rect: CGRect) -> [String: Double] {
            let w = Double(image.width), h = Double(image.height)
            return [
                "x": Double(rect.minX) / w, "y": Double(rect.minY) / h,
                "w": Double(rect.width) / w, "h": Double(rect.height) / h,
            ]
        }
        let pixels = Pixels(image)
        let height = pixels.height, width = pixels.width
        let columns = pixels.verticalDividers(rows: Int(Double(height) * 0.15)..<Int(Double(height) * 0.95))
        let lines = pixels.horizontalDividers(columns: Int(regions.chat.minX) + 4..<Int(regions.chat.maxX) - 4)
        return [
            "window": ["width": Double(frame.width), "height": Double(frame.height)],
            "found": regions.found,
            "columns": columns.map { Double($0) / Double(width) },
            "lines": lines.map { Double($0) / Double(height) },
            "list": fractions(regions.list), "header": fractions(regions.header),
            "chat": fractions(regions.chat),
        ]
    }
}

struct Regions: Equatable {
    var list: CGRect
    var header: CGRect
    var chat: CGRect
    /// Which dividers were found rather than taken from the fallback fractions.
    var found: [String]

    static let fallback = (nav: 0.06, list: 0.32, header: 0.09, input: 0.74)

    static func detect(_ pixels: Pixels) -> Regions {
        let width = pixels.width, height = pixels.height
        var found: [String] = []
        func fraction(_ value: Int, of total: Int) -> Double { Double(value) / Double(total) }
        // The window's own edges are not dividers.
        let columns = pixels.verticalDividers(rows: Int(Double(height) * 0.15)..<Int(Double(height) * 0.95))
            .filter { fraction($0, of: width) > 0.015 && fraction($0, of: width) < 0.985 }
        // The navigation bar is narrow; the conversation list ends well before the middle.
        let nav = columns.first { fraction($0, of: width) < 0.16 }
        let listEnd = columns.first {
            fraction($0, of: width) > 0.15 && fraction($0, of: width) < 0.65
                && $0 > (nav ?? 0) + Int(0.08 * Double(width))
        }
        if nav != nil { found.append("nav") }
        if listEnd != nil { found.append("list") }
        let left = nav ?? Int(fallback.nav * Double(width))
        let right = listEnd ?? Int(fallback.list * Double(width))
        // A panel on the right of the chat (a group's member list) ends the chat area: its
        // names would otherwise read as messages of the user's own.
        let panel = columns.first { $0 > right + Int(0.25 * Double(width)) }
        if panel != nil { found.append("panel") }
        let end = panel ?? width

        let lines = pixels.horizontalDividers(columns: (right + 4)..<(end - 4))
            .filter { fraction($0, of: height) > 0.015 && fraction($0, of: height) < 0.985 }
        // Under the window's title bar comes the chat's own header with its title; the last
        // line near the top ends both.
        let header = lines.last { fraction($0, of: height) > 0.03 && fraction($0, of: height) < 0.25 }
        let input = lines.last { fraction($0, of: height) > 0.45 }
        if header != nil { found.append("header") }
        if input != nil { found.append("input") }
        let top = header ?? Int(fallback.header * Double(height))
        let bottom = input ?? Int(fallback.input * Double(height))

        return Regions(
            list: CGRect(x: left + 1, y: 0, width: right - left - 1, height: height),
            header: CGRect(x: right + 1, y: 0, width: end - right - 1, height: top),
            chat: CGRect(x: right + 1, y: top + 1, width: end - right - 1, height: bottom - top - 1),
            found: found)
    }
}

/// The screenshot in 8-bit gray, rows from the top.
struct Pixels {
    let width: Int
    let height: Int
    let data: [UInt8]

    init(_ image: CGImage) {
        let width = image.width, height = image.height
        var data = [UInt8](repeating: 0, count: width * height)
        data.withUnsafeMutableBytes { buffer in
            let context = CGContext(
                data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
                bitmapInfo: CGImageAlphaInfo.none.rawValue)
            context?.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        }
        self.width = width
        self.height = height
        self.data = data
    }

    subscript(x: Int, y: Int) -> Int { Int(data[y * width + x]) }

    /// Columns where the colour changes from the column before along at least 60 % of `rows`,
    /// with neighbouring columns merged (an anti-aliased line spans two).
    func verticalDividers(rows: Range<Int>) -> [Int] {
        guard width > 2, !rows.isEmpty else { return [] }
        let sampled = Array(stride(from: rows.lowerBound, to: rows.upperBound, by: 2))
        var hits: [Int] = []
        for x in 1..<width {
            let changed = sampled.filter { abs(self[x, $0] - self[x - 1, $0]) >= 2 }.count
            if Double(changed) >= 0.6 * Double(sampled.count) { hits.append(x) }
        }
        return merged(hits)
    }

    func horizontalDividers(columns: Range<Int>) -> [Int] {
        guard height > 2, !columns.isEmpty else { return [] }
        let sampled = Array(stride(from: columns.lowerBound, to: columns.upperBound, by: 2))
        var hits: [Int] = []
        for y in 1..<height {
            let changed = sampled.filter { abs(self[$0, y] - self[$0, y - 1]) >= 2 }.count
            if Double(changed) >= 0.6 * Double(sampled.count) { hits.append(y) }
        }
        return merged(hits)
    }

    private func merged(_ hits: [Int]) -> [Int] {
        var result: [Int] = []
        for value in hits where result.last.map({ value - $0 > 3 }) ?? true {
            result.append(value)
        }
        return result
    }

    /// Include every pixel and the dimensions: sparse text can fall between sample points.
    var fingerprint: String {
        var hash: UInt64 = 1_469_598_103_934_665_603
        for pixel in data {
            hash = (hash ^ UInt64(pixel)) &* 1_099_511_628_211
        }
        return "\(width)x\(height):\(String(hash, radix: 16))"
    }
}
