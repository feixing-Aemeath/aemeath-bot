import AppKit
import CoreGraphics
import Foundation

/// capture_chat: switch the app to a whitelisted chat and scroll it, then screenshot the chat
/// area. The only input it sends is a click on the chat's row in the conversation list and
/// scrolling inside the chat area. Before every screenshot it checks the chat's title (OCR of
/// the header), so it never reads a chat it was not asked for.
struct ChatReader {
    let app: App
    let chat: String

    /// The chat area, and the title the header showed for it.
    func capture(page: Int) async throws -> (image: CGImage, title: String) {
        guard let running = app.running else { throw HelperError.appNotRunning }
        running.activate()
        try await pause(0.6)
        var shot = try await WindowShot.take(of: app)
        if page == 0 {
            if try !isOpen(shot) {
                try await open(in: shot)
                shot = try await WindowShot.take(of: app)
                guard try isOpen(shot) else { throw HelperError.chatNotOpen }
            }
            // The newest messages: all the way down.
            Input.scroll(at: center(of: shot), points: -6000)
        } else {
            // Another helper run scrolled the previous page; make sure nothing switched since.
            guard try isOpen(shot) else { throw HelperError.chatNotOpen }
            // One step up, keeping about a third of the previous screen as overlap.
            Input.scroll(at: center(of: shot), points: shot.regions.chat.height * shot.pointsPerPixel * 0.65)
        }
        try await pause(0.9)
        shot = try await WindowShot.take(of: app)
        guard let title = try openTitle(shot) else { throw HelperError.chatNotOpen }
        return (shot.crop(shot.regions.chat), title)
    }

    func isOpen(_ shot: WindowShot) throws -> Bool {
        try openTitle(shot) != nil
    }

    /// The chat's title as read in the header, when it is this chat. One line of the header
    /// must be the whole name, a member count after it aside: 「软件1班家长群」 contains
    /// 「软件1班」 but is another chat. The backend checks the returned title again.
    func openTitle(_ shot: WindowShot) throws -> String? {
        let lines = try Recognizer.blocks(in: shot.crop(shot.regions.header)).map(\.text)
        let key = chat.matchKey
        return (lines + [lines.joined()])
            .map { $0.replacingOccurrences(of: #"\s*[(（]\d{1,5}[)）]\s*$"#, with: "", options: .regularExpression) }
            .first { $0.matchKey == key }
    }

    /// Clicks the chat's name in the conversation list. The list shows a name with its time
    /// at the right, or a long name cut off with "…".
    func open(in shot: WindowShot) async throws {
        let list = shot.regions.list
        let key = chat.matchKey
        let blocks = try Recognizer.blocks(in: shot.crop(list))
        guard let row = blocks.first(where: { Self.names(chat: key, line: $0.text.matchKey) }) else {
            throw HelperError.chatNotFound
        }
        let pixel = CGPoint(
            x: list.minX + CGFloat(row.x + row.w / 2) * list.width,
            y: list.minY + CGFloat(row.midY) * list.height)
        Input.click(at: shot.screenPoint(pixel))
        try await pause(1.0)
    }

    static func names(chat key: String, line: String) -> Bool {
        if line == key { return true }
        // OCR read the name and the time as one line: 「软件工程2班 21:05」.
        if line.hasPrefix(key) {
            let rest = String(line.dropFirst(key.count))
            return rest.range(of: #"^(\d{3,4}|昨天|前天|星期.|周.|\d{1,2}月\d{1,2}日|\d{6,8})$"#, options: .regularExpression) != nil
        }
        // A long name cut off: 「软件工程2班家…」 (the "…" is not part of the key).
        return line.count >= 4 && key.hasPrefix(line)
    }

    func center(of shot: WindowShot) -> CGPoint {
        let chat = shot.regions.chat
        return shot.screenPoint(CGPoint(x: chat.midX, y: chat.midY))
    }

    func pause(_ seconds: Double) async throws {
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }
}

enum Input {
    static let source = CGEventSource(stateID: .combinedSessionState)

    static func click(at point: CGPoint) {
        for type in [CGEventType.mouseMoved, .leftMouseDown, .leftMouseUp] {
            CGEvent(mouseEventSource: source, mouseType: type, mouseCursorPosition: point, mouseButton: .left)?
                .post(tap: .cghidEventTap)
            usleep(60_000)
        }
    }

    /// Positive points scroll towards older messages (up), negative towards newer ones.
    static func scroll(at point: CGPoint, points: CGFloat) {
        CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?
            .post(tap: .cghidEventTap)
        usleep(50_000)
        var remaining = points
        while abs(remaining) >= 1 {
            let step = remaining > 0 ? min(remaining, 40) : max(remaining, -40)
            let event = CGEvent(
                scrollWheelEvent2Source: source, units: .pixel, wheelCount: 1, wheel1: Int32(step), wheel2: 0,
                wheel3: 0)
            event?.location = point
            event?.post(tap: .cghidEventTap)
            remaining -= step
            usleep(12_000)
        }
    }
}
