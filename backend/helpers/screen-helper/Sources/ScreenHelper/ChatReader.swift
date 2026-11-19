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
    /// Where page 0 keeps its regions for the pages after it; nil finds them on every page.
    var regionStore: RegionStore? = nil

    /// The chat area, and the title the header showed for it.
    func capture(page: Int) async throws -> (image: CGImage, title: String) {
        guard let running = app.running else { throw HelperError.appNotRunning }
        running.activate()
        try await pause(0.6)
        let locked = page > 0 ? regionStore?.load() : nil
        var shot = try await WindowShot.take(of: app, locked: locked)
        if page == 0 {
            // Check the list even when the title already matches: two visible chats can have
            // the same title, which is not enough evidence to choose either of them.
            var listMoved = false
            // The preview action reads the list as it stands and expects its top, where the
            // newest chats are. A list left scrolled after looking for a row would hide them
            // from every later preview, so it goes back up whether the row was found or not.
            func restoreList() async {
                guard listMoved else { return }
                let point = shot.screenPoint(CGPoint(x: shot.regions.list.midX, y: shot.regions.list.height * 0.55))
                guard (try? shot.checkInput(app: app, at: point)) != nil else { return }
                Input.scroll(at: point, points: 60000)
                try? await pause(0.6)
                listMoved = false
            }
            let row: Block
            do {
                row = try await Self.findRow(chat: chat, read: {
                    shot = try await WindowShot.take(of: app)
                    return try Recognizer.blocks(in: shot.crop(shot.regions.list))
                }, scroll: { direction in
                    let point = shot.screenPoint(CGPoint(x: shot.regions.list.midX, y: shot.regions.list.height * 0.55))
                    try shot.checkInput(app: app, at: point)
                    listMoved = true
                    Input.scroll(at: point, points: direction * shot.regions.list.height * shot.pointsPerPixel * 0.65)
                    try await pause(0.6)
                })
            } catch {
                await restoreList()
                throw error
            }
            if try !isOpen(shot) {
                do {
                    try await open(in: shot, row: row)
                } catch {
                    await restoreList()
                    throw error
                }
                await restoreList()
                shot = try await WindowShot.take(of: app)
                guard try isOpen(shot) else { throw HelperError.chatNotOpen }
            } else if listMoved {
                await restoreList()
                shot = try await WindowShot.take(of: app)
            }
            // The newest messages: all the way down.
            try shot.checkInput(app: app, at: center(of: shot))
            Input.scroll(at: center(of: shot), points: -6000)
        } else {
            // Another helper run scrolled the previous page; make sure nothing switched since.
            guard try isOpen(shot) else { throw HelperError.chatNotOpen }
            // One step up, keeping about a third of the previous screen as overlap.
            try shot.checkInput(app: app, at: center(of: shot))
            Input.scroll(at: center(of: shot), points: shot.regions.chat.height * shot.pointsPerPixel * 0.65)
        }
        try await pause(0.9)
        shot = try await WindowShot.take(of: app, locked: locked)
        guard let title = try openTitle(shot) else { throw HelperError.chatNotOpen }
        if page == 0 {
            // The regions of the newest screen hold for the older ones of this read.
            regionStore?.save(RegionLock(width: shot.image.width, height: shot.image.height, regions: shot.regions))
        }
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
        return Self.title(chat: chat, lines: lines)
    }

    static func title(chat: String, lines: [String]) -> String? {
        let candidates = lines + [lines.joined()]
        let key = chat.matchKey
        // A name can itself end in digits in brackets; try the complete name first.
        if let exact = candidates.first(where: { names(chat: key, line: $0) && $0.matchKey == key }) {
            return exact
        }
        return candidates.map {
            $0.replacingOccurrences(of: #"\s*[(（]\d{1,5}[)）]\s*$"#, with: "", options: .regularExpression)
        }.first { names(chat: key, line: $0) && $0.matchKey == key }
    }

    /// Clicks the chat's name in the conversation list. The list shows a name with its time
    /// at the right. Truncated or ambiguous names are never clicked.
    func open(in shot: WindowShot, row: Block) async throws {
        let list = shot.regions.list
        let pixel = CGPoint(
            x: list.minX + CGFloat(row.x + row.w / 2) * list.width,
            y: list.minY + CGFloat(row.midY) * list.height)
        try shot.checkInput(app: app, at: shot.screenPoint(pixel))
        Input.click(at: shot.screenPoint(pixel))
        try await pause(1.0)
    }

    /// Bounded list-only navigation. It never uses the search box or emits keyboard input.
    /// Injected operations let tests exercise invisible rows without touching an app.
    static func findRow(chat: String, read: () async throws -> [Block],
                        scroll: (Double) async throws -> Void) async throws -> Block {
        var blocks = try await read()
        if let row = try candidate(chat: chat, blocks: blocks) { return row }
        // Look above the current viewport, then below. Repeated OCR geometry means this
        // direction has stopped moving. The bound also handles animated/noisy list rows.
        for (direction, limit) in [(1.0, 12), (-1.0, 24)] {
            for _ in 0..<limit {
                let previous = listSignature(blocks)
                try await scroll(direction)
                blocks = try await read()
                if let row = try candidate(chat: chat, blocks: blocks) { return row }
                if listSignature(blocks) == previous { break }
            }
        }
        throw HelperError.chatNotFound
    }

    static func candidate(chat: String, blocks: [Block]) throws -> Block? {
        do { return try row(chat: chat, blocks: blocks) }
        catch HelperError.chatNotFound { return nil }
    }

    static func listSignature(_ blocks: [Block]) -> String {
        blocks.map { "\($0.text.matchKey):\(Int($0.y * 1000))" }.joined(separator: "|")
    }

    static func row(chat: String, blocks: [Block]) throws -> Block {
        // Vision sometimes splits a name into several blocks. Merge only a single
        // horizontal row with a right-hand time, never text in the preview underneath it.
        var groups: [[Block]] = []
        for block in blocks.sorted(by: { $0.y < $1.y }) {
            if let index = groups.firstIndex(where: { row in
                row.contains { abs($0.midY - block.midY) <= min($0.h, block.h) / 2 }
            }) { groups[index].append(block) } else { groups.append([block]) }
        }
        let key = chat.matchKey
        let rows: [Block] = groups.compactMap { group in
            let row = group.sorted { $0.x < $1.x }
            guard let first = row.first, let last = row.last, first.x < 0.8 else { return nil }
            let text = row.map(\.text).joined(separator: " ")
            guard last.right > 0.85 else { return nil }
            let separateTime = last.text.trimmingCharacters(in: .whitespacesAndNewlines)
                .range(of: "^(?:" + listTime + "|" + strayClock + ")$", options: .regularExpression) != nil
            let name = separateTime ? row.dropLast().map(\.text).joined() : text
            guard names(chat: key, line: name), separateTime || text.matchKey != key else { return nil }
            let right = separateTime ? (row.dropLast().map(\.right).max() ?? first.right) : last.right
            return Block(text: name, x: first.x, y: first.y, w: right - first.x,
                         h: first.h, confidence: first.confidence)
        }
        guard rows.count == 1 else {
            throw rows.isEmpty ? HelperError.chatNotFound : HelperError.chatAmbiguous
        }
        return rows[0]
    }

    static let listTime = #"(?:\d{1,2}[:：]\d{2}|(?:昨天|前天|(?:星期|周)[一二三四五六日天]|\d{1,2}月\d{1,2}日|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{1,2}[/.-]\d{1,2})(?:\s*\d{1,2}[:：]\d{2})?)"#

    /// A clock with one digit too many: Vision adds a stray 「1」 after some list times
    /// (「昨天20:411」 for 「昨天20:41」, 2026-11-14), which made the whole row unfindable.
    static let strayClock = #"(?:(?:昨天|前天|(?:星期|周)[一二三四五六日天]|\d{1,2}月\d{1,2}日|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{1,2}[/.-]\d{1,2})?\s*\d{1,2}[:：]\d{2})\d"#

    static func names(chat key: String, line: String) -> Bool {
        // Keep the raw punctuation until after the ellipsis and clock checks. Normalizing
        // first loses the ellipsis and makes another name ending in 2105 look like 21:05.
        let name = line.replacingOccurrences(
            // OCR can drop the space before a clock; only a clock is that unmistakable.
            of: #"(?:\s+"# + listTime + #"|\s*\d{1,2}[:：]\d{2})\s*$"#, with: "", options: .regularExpression
        ).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty, !["…", "...", "⋯", ".."].contains(where: { name.hasSuffix($0) }) else {
            return false
        }
        return name.matchKey == key
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
