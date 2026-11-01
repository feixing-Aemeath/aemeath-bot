import AppKit
import Foundation

/// The read-only screen helper of qq_screen, wechat_screen and timetable (docs/screen-reading.md).
///
/// One JSON request on stdin, one JSON reply on stdout, one request per process:
///
///     {"action": "preview", "app": "qq", "out_dir": "/…"}
///     {"action": "capture_chat", "app": "qq", "chat": "…", "page": 0, "out_dir": "/…"}
///     {"action": "capture_announcement", "app": "qq", "chat": "…", "out_dir": "/…"}
///     {"action": "capture_page", "app": "chrome", "title": "…", "out_dir": "/…"}
///     {"action": "recognize", "path": "/…/x.png", "out_dir": "/…"}
///     {"action": "layout", "app": "qq"}     (calibration only: the regions it found)
///     {"action": "authorize"}                (setup only: ask for the two permissions)
///     {"action": "permissions"}              (whether they are granted, without asking)
///
/// It screenshots the app's window, finds the conversation list and the chat area from the
/// window's own column dividers, and runs Apple Vision. Only capture_chat touches the app: it
/// activates it, clicks the whitelisted chat in the conversation list and scrolls the chat.
/// It never types, sends, adds friends or likes anything. capture_page only screenshots the
/// browser window showing a page the user keeps open. Screenshots stay in `out_dir`.
@main
struct ScreenHelper {
    static func main() async {
        Disclaim.runAsResponsibleProcess()
        _ = NSApplication.shared  // connects to the window server; ScreenCaptureKit needs it
        do {
            let input = FileHandle.standardInput.readDataToEndOfFile()
            let request = try JSONDecoder().decode(Request.self, from: input)
            let reply = try await handle(request)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: reply))
        } catch {
            let code = (error as? HelperError)?.code ?? "screen_helper_failed"
            FileHandle.standardOutput.write(Data("{\"error\":\"\(code)\"}".utf8))
            FileHandle.standardError.write(Data("\(code)\n".utf8))
            exit(1)
        }
    }

    static func handle(_ request: Request) async throws -> [String: Any] {
        let output = Output(directory: request.out_dir)
        switch request.action {
        case "recognize":
            guard let path = request.path, output.contains(path) else { throw HelperError.invalidRequest }
            let blocks = try Recognizer.blocks(in: URL(fileURLWithPath: path))
            return ["blocks": blocks.map(\.json)]
        case "preview":
            let app = try App.chat(request.app)
            try Permissions.checkScreen()
            let shot = try await WindowShot.take(of: app)
            let path = try output.save(shot.crop(shot.regions.list), name: "\(app.id)-preview")
            return output.screenshot(path: path, chat: nil, atTop: false)
        case "capture_chat":
            let app = try App.chat(request.app)
            guard let chat = request.chat, !chat.isEmpty, let page = request.page, page >= 0 else {
                throw HelperError.invalidRequest
            }
            try Permissions.checkScreen()
            try Permissions.checkInput()
            let (image, title) = try await ChatReader(app: app, chat: chat).capture(page: page)
            let path = try output.save(image, name: "\(app.id)-chat")
            let atTop = page > 0 && output.sameAsLast(image, key: "\(app.id)-\(chat)")
            output.remember(image, key: "\(app.id)-\(chat)")
            // The title as read, not the name asked for, so the backend can check it too.
            return output.screenshot(path: path, chat: title, atTop: atTop)
        case "capture_announcement":
            // Opening the announcement panel is not calibrated yet; no announcement is read.
            _ = try App.chat(request.app)
            return ["screenshot": NSNull()]
        case "capture_page":
            let app = try App(request.app)
            guard app.isBrowser, let title = request.title, !title.isEmpty else {
                throw HelperError.invalidRequest
            }
            try Permissions.checkScreen()
            let image = try await PageShot.take(of: app, title: title)
            let path = try output.save(image, name: "\(app.id)-page")
            return output.screenshot(path: path, chat: nil, atTop: false)
        case "authorize":
            // Setup only: ask macOS for the two permissions, for this helper itself.
            let screen = CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess()
            let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
            return ["screen_recording": screen, "accessibility": AXIsProcessTrustedWithOptions(options)]
        case "permissions":
            return ["screen_recording": CGPreflightScreenCaptureAccess(), "accessibility": AXIsProcessTrusted()]
        case "layout":
            let app = try App.chat(request.app)
            try Permissions.checkScreen()
            let shot = try await WindowShot.take(of: app)
            var reply = shot.describe()
            // Calibration: save the regions too, to check them with `recognize`.
            if request.out_dir != nil {
                reply["files"] = [
                    "list": try output.save(shot.crop(shot.regions.list), name: "\(app.id)-layout-list"),
                    "header": try output.save(shot.crop(shot.regions.header), name: "\(app.id)-layout-header"),
                    "chat": try output.save(shot.crop(shot.regions.chat), name: "\(app.id)-layout-chat"),
                ]
            }
            return reply
        default:
            throw HelperError.invalidRequest
        }
    }
}

struct Request: Decodable {
    let action: String
    let app: String?
    let chat: String?
    let page: Int?
    let path: String?
    let title: String?
    let out_dir: String?
}

enum HelperError: Error {
    case invalidRequest, unknownApp, appNotRunning, windowNotFound, screenRecordingDenied
    case accessibilityDenied, captureFailed, chatNotFound, chatNotOpen, pageNotOpen

    var code: String {
        switch self {
        case .invalidRequest: return "screen_invalid_request"
        case .unknownApp: return "screen_unknown_app"
        case .appNotRunning: return "screen_app_not_running"
        case .windowNotFound: return "screen_window_not_found"
        case .screenRecordingDenied: return "screen_recording_denied"
        case .accessibilityDenied: return "screen_accessibility_denied"
        case .captureFailed: return "screen_capture_failed"
        case .chatNotFound: return "screen_chat_not_in_list"
        case .chatNotOpen: return "screen_chat_not_open"
        case .pageNotOpen: return "screen_page_not_open"
        }
    }
}

/// The apps the helper knows: the chat apps, and the browser that shows pages the user keeps
/// open (only ever captured, never clicked).
struct App {
    let id: String
    let bundleID: String
    let isBrowser: Bool

    init(_ id: String?) throws {
        switch id {
        case "qq": self.id = "qq"; bundleID = "com.tencent.qq"; isBrowser = false
        case "wechat": self.id = "wechat"; bundleID = "com.tencent.xinWeChat"; isBrowser = false
        case "chrome": self.id = "chrome"; bundleID = "com.google.Chrome"; isBrowser = true
        default: throw HelperError.unknownApp
        }
    }

    /// A chat app, for the actions that read (and may click) chats.
    static func chat(_ id: String?) throws -> App {
        let app = try App(id)
        guard !app.isBrowser else { throw HelperError.unknownApp }
        return app
    }

    var running: NSRunningApplication? {
        NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first
    }
}

enum Permissions {
    static func checkScreen() throws {
        guard CGPreflightScreenCaptureAccess() else { throw HelperError.screenRecordingDenied }
    }

    /// Clicking and scrolling post input events, which needs Accessibility.
    static func checkInput() throws {
        guard AXIsProcessTrusted() else { throw HelperError.accessibilityDenied }
    }
}

/// Where screenshots go: `out_dir` from the request (the plugin's data folder), else the
/// helper's cache. Files older than a week are removed.
struct Output {
    let directory: URL

    init(directory path: String?) {
        if let path, path.hasPrefix("/") {
            directory = URL(fileURLWithPath: path, isDirectory: true)
        } else {
            directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("local.assistant.screen-helper", isDirectory: true)
        }
    }

    /// Only screenshots this helper wrote may be recognized.
    func contains(_ path: String) -> Bool {
        let file = URL(fileURLWithPath: path).standardizedFileURL
        return file.pathExtension == "png"
            && file.deletingLastPathComponent().path == directory.standardizedFileURL.path
    }

    func save(_ image: CGImage, name: String) throws -> String {
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        prune()
        let stamp = Int(Date().timeIntervalSince1970 * 1000)
        let url = directory.appendingPathComponent("\(name)-\(stamp).png")
        guard let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)
        else { throw HelperError.captureFailed }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw HelperError.captureFailed }
        return url.path
    }

    func prune(olderThan age: TimeInterval = 7 * 24 * 3600) {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        for file in files where file.pathExtension == "png" {
            let modified = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            if let modified, Date().timeIntervalSince(modified) > age {
                try? FileManager.default.removeItem(at: file)
            }
        }
    }

    func screenshot(path: String, chat: String?, atTop: Bool) -> [String: Any] {
        let format = ISO8601DateFormatter()
        format.timeZone = .current
        return [
            "path": path, "captured_at": format.string(from: Date()), "chat": chat ?? NSNull(),
            "at_top": atTop,
        ]
    }

    // Scrolling up when the chat is already at its first message changes nothing.
    private func marker(_ key: String) -> URL {
        let safe = key.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? String($0) : "_" }.joined()
        return directory.appendingPathComponent(".last-\(safe)")
    }

    func sameAsLast(_ image: CGImage, key: String) -> Bool {
        guard let previous = try? String(contentsOf: marker(key), encoding: .utf8) else { return false }
        return previous == Pixels(image).fingerprint
    }

    func remember(_ image: CGImage, key: String) {
        try? Pixels(image).fingerprint.write(to: marker(key), atomically: true, encoding: .utf8)
    }
}
