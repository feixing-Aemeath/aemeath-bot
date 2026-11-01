import Darwin
import EventKit
import Foundation

struct Input: Decodable {
    let client_id: String
    let id: String?
    let title: String
    let notes: String
    let due_at: String?
    let has_time: Bool
    let completed: Bool
    let alarms: [Int]?
    let calendar: String?
    let end_at: String?
    let timezone: String?
}

struct Output: Encodable { let id: String? }
enum SyncError: Error { case invalidInput, denied, calendarUnavailable, invalidDate }

@main struct AppleSync {
    static let store = EKEventStore()

    static func access(_ entity: EKEntityType) async throws {
        let granted: Bool
        if #available(macOS 14.0, *) {
            granted = entity == .reminder
                ? try await store.requestFullAccessToReminders()
                : try await store.requestFullAccessToEvents()
        } else {
            granted = try await withCheckedThrowingContinuation { continuation in
                store.requestAccess(to: entity) { allowed, error in
                    if let error { continuation.resume(throwing: error) }
                    else { continuation.resume(returning: allowed) }
                }
            }
        }
        if !granted { throw SyncError.denied }
    }

    static func defaultSource(_ entity: EKEntityType) -> EKSource? {
        entity == .reminder
            ? store.defaultCalendarForNewReminders()?.source
            : store.defaultCalendarForNewEvents?.source
    }

    /// The named dedicated list/calendar in the account new items go to by default: iCloud once
    /// the Mac is signed in, so they reach the iPhone. A list left "On My Mac" from before
    /// signing in is not reused then; a new one is made in iCloud.
    static func calendar(_ entity: EKEntityType, name: String = "AI 待办") throws -> EKCalendar {
        let source = defaultSource(entity) ?? store.sources.first(where: { $0.sourceType == .local })
        guard let source else { throw SyncError.calendarUnavailable }
        let available = lists(entity, name: name)
        if let existing = available.first(where: { $0.source.sourceIdentifier == source.sourceIdentifier }) {
            return existing
        }
        let created = EKCalendar(for: entity, eventStore: store)
        created.title = name
        created.source = source
        try store.saveCalendar(created, commit: true)
        return created
    }

    /// `apple-sync status`: read-only, asks for nothing. Whether access was granted, the
    /// account new items go to, and which accounts hold an "AI 待办" list.
    static func status() -> [String: Any] {
        func describe(_ entity: EKEntityType) -> [String: Any] {
            let state = EKEventStore.authorizationStatus(for: entity)
            let granted: Bool
            if #available(macOS 14.0, *) { granted = state == .fullAccess } else { granted = state == .authorized }
            var result: [String: Any] = ["access": granted ? "granted" : state == .notDetermined ? "not_asked" : "denied"]
            guard granted else { return result }
            if let source = defaultSource(entity) {
                result["default_account"] = ["title": source.title, "type": kind(source.sourceType)]
            }
            result["ai_lists"] = store.calendars(for: entity).filter { $0.title == "AI 待办" }
                .map { ["account": $0.source.title, "type": kind($0.source.sourceType)] }
            return result
        }
        return ["reminders": describe(.reminder), "calendar": describe(.event)]
    }

    static func kind(_ type: EKSourceType) -> String {
        switch type {
        case .local: return "local"
        case .calDAV: return "caldav"  // iCloud is a CalDAV account
        case .exchange: return "exchange"
        case .mobileMe: return "mobileme"
        case .subscribed: return "subscribed"
        case .birthdays: return "birthdays"
        @unknown default: return "other"
        }
    }

    static func date(_ text: String?) throws -> Date? {
        guard let text else { return nil }
        let format = ISO8601DateFormatter()
        format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let value = format.date(from: text) { return value }
        format.formatOptions = [.withInternetDateTime]
        guard let value = format.date(from: text) else { throw SyncError.invalidDate }
        return value
    }

    /// Every dedicated list with this name, in every account. An item made while another account was the
    /// default stays in that account's list, so items are looked up in all of them: deleting
    /// removes it there too, and saving moves it to today's list instead of leaving it behind.
    static func lists(_ entity: EKEntityType, name: String = "AI 待办") -> [EKCalendar] {
        store.calendars(for: entity).filter { $0.title == name }
    }

    static func localCalendar(_ input: Input) throws -> Calendar {
        var calendar = Calendar.current
        if let name = input.timezone {
            guard let zone = TimeZone(identifier: name) else { throw SyncError.invalidInput }
            calendar.timeZone = zone
        }
        return calendar
    }

    static func reminder(_ input: Input, delete: Bool) async throws -> String? {
        try await access(.reminder)
        let marker = URL(string: "assistant-todo://" + input.client_id)!
        let all = lists(.reminder)
        var found: [EKReminder] = []
        if !all.isEmpty {
            let predicate = store.predicateForReminders(in: all)
            found = await withCheckedContinuation { continuation in
                store.fetchReminders(matching: predicate) { continuation.resume(returning: $0 ?? []) }
            }
        }
        // Marker lookup makes creation idempotent even after a crash before returning an ID.
        let matches = found.filter { $0.calendarItemIdentifier == input.id || $0.url == marker }
        if delete {
            for item in matches { try store.remove(item, commit: true) }
            return nil
        }
        let list = try calendar(.reminder)
        let local = try localCalendar(input)
        let due = try date(input.due_at)
        // Updated in place when it is in today's list; otherwise made again there (EventKit
        // does not move items between accounts) and removed from the other account below.
        let item = matches.first { $0.calendar.calendarIdentifier == list.calendarIdentifier }
            ?? EKReminder(eventStore: store)
        item.calendar = list
        item.title = input.title
        item.notes = input.notes
        item.url = marker
        item.isCompleted = input.completed
        if let due {
            var fields: Set<Calendar.Component> = [.year, .month, .day]
            if input.has_time { fields.formUnion([.hour, .minute, .second]) }
            var components = local.dateComponents(fields, from: due)
            components.timeZone = local.timeZone
            item.dueDateComponents = components
        } else {
            item.dueDateComponents = nil
        }
        if let alarms = input.alarms {
            item.alarms = []
            if let due, !input.completed {
                let base = input.has_time ? due : local.startOfDay(for: due)
                for minutes in alarms {
                    item.addAlarm(EKAlarm(absoluteDate: base.addingTimeInterval(-Double(minutes) * 60)))
                }
            }
        }
        try store.save(item, commit: true)
        for other in matches where other !== item { try store.remove(other, commit: true) }
        return item.calendarItemIdentifier
    }

    static func event(_ input: Input, delete: Bool) throws -> String? {
        let name = input.calendar ?? "AI 待办"
        let marker = URL(string: "assistant-todo://" + input.client_id)!
        let due = try date(input.due_at)
        let all = lists(.event, name: name)
        var matches: [EKEvent] = []
        if let id = input.id, let item = store.calendarItem(withIdentifier: id) as? EKEvent,
           all.contains(where: { $0.calendarIdentifier == item.calendar.calendarIdentifier }) {
            matches.append(item)
        }
        // Search bounded yearly windows (EventKit limits long predicates) around the due date.
        let center = due ?? Date()
        for year in -1...1 where !all.isEmpty {
            let start = Calendar.current.date(byAdding: .year, value: year, to: center)!
            let end = Calendar.current.date(byAdding: .year, value: 1, to: start)!
            let predicate = store.predicateForEvents(withStart: start, end: end, calendars: all)
            for item in store.events(matching: predicate) where item.url == marker {
                if !matches.contains(where: { $0.calendarItemIdentifier == item.calendarItemIdentifier }) {
                    matches.append(item)
                }
            }
        }
        if delete {
            for item in matches { try store.remove(item, span: .thisEvent, commit: true) }
            return nil
        }
        guard let due else { throw SyncError.invalidDate }
        let end = try date(input.end_at) ?? due.addingTimeInterval(30 * 60)
        guard end > due else { throw SyncError.invalidDate }
        let calendar = try calendar(.event, name: name)
        // As for reminders: an event in another account's list is made again in today's.
        let item = matches.first { $0.calendar.calendarIdentifier == calendar.calendarIdentifier }
            ?? EKEvent(eventStore: store)
        item.calendar = calendar
        item.title = input.title
        item.notes = input.notes
        item.url = marker
        item.startDate = due
        item.endDate = end
        item.isAllDay = false
        if input.timezone != nil { item.timeZone = try localCalendar(input).timeZone }
        if let alarms = input.alarms {
            item.alarms = []
            if !input.completed {
                for minutes in alarms {
                    item.addAlarm(EKAlarm(relativeOffset: -Double(minutes) * 60))
                }
            }
        }
        try store.save(item, span: .thisEvent, commit: true)
        for other in matches where other !== item { try store.remove(other, span: .thisEvent, commit: true) }
        return item.calendarItemIdentifier
    }

    /// Privacy permissions belong to the "responsible" process: when the backend's Python
    /// starts this tool, that is Python, which has no usage descriptions, so Reminders and
    /// Calendar would be refused without asking. Start a copy of ourselves that disclaims
    /// the parent, so the prompt names this tool and the grant is kept for it, and pass its
    /// exit code on. stdin and stdout are inherited.
    static func runDisclaimed() {
        let marker = "ASSISTANT_DISCLAIMED"
        guard ProcessInfo.processInfo.environment[marker] == nil,
              let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim"),
              let path = Bundle.main.executablePath
        else { return }
        typealias Disclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32
        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        guard unsafeBitCast(symbol, to: Disclaim.self)(&attributes, 1) == 0 else { return }
        let arguments: [UnsafeMutablePointer<CChar>?] = CommandLine.arguments.map { strdup($0) } + [nil]
        let pairs = ProcessInfo.processInfo.environment.map { "\($0)=\($1)" } + ["\(marker)=1"]
        let environment: [UnsafeMutablePointer<CChar>?] = pairs.map { strdup($0) } + [nil]
        defer { (arguments + environment).forEach { free($0) } }
        var child: pid_t = 0
        guard posix_spawn(&child, path, nil, &attributes, arguments, environment) == 0 else { return }
        var status: Int32 = 0
        while waitpid(child, &status, 0) == -1 && errno == EINTR {}
        // WIFEXITED / WEXITSTATUS are macros Swift does not import.
        exit((status & 0x7f) == 0 ? (status >> 8) & 0xff : 1)
    }

    static func main() async {
        runDisclaimed()
        if CommandLine.arguments.dropFirst() == ["status"] {
            let data = try? JSONSerialization.data(withJSONObject: status(), options: [.sortedKeys])
            FileHandle.standardOutput.write(data ?? Data("{}".utf8))
            return
        }
        do {
            guard CommandLine.arguments.count == 3 else { throw SyncError.invalidInput }
            let resource = CommandLine.arguments[1], action = CommandLine.arguments[2]
            guard ["reminders", "calendar"].contains(resource), ["upsert", "delete"].contains(action) else {
                throw SyncError.invalidInput
            }
            let input = try JSONDecoder().decode(Input.self, from: FileHandle.standardInput.readDataToEndOfFile())
            guard UUID(uuidString: input.client_id) != nil else { throw SyncError.invalidInput }
            let name = input.calendar ?? "AI 待办"
            guard (resource == "calendar" && ["AI 待办", "AI 课表"].contains(name))
                || (resource == "reminders" && name == "AI 待办") else {
                throw SyncError.invalidInput
            }
            let identifier: String?
            if resource == "reminders" {
                identifier = try await reminder(input, delete: action == "delete")
            } else {
                try await access(.event)
                identifier = try event(input, delete: action == "delete")
            }
            let data = try JSONEncoder().encode(Output(id: identifier))
            // JSONEncoder omits nil by default; protocol requires an explicit null.
            FileHandle.standardOutput.write(identifier == nil ? Data("{\"id\":null}\n".utf8) : data)
        } catch {
            FileHandle.standardError.write(Data("apple_sync_failed\n".utf8))
            exit(1)
        }
    }
}
