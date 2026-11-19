import Foundation

/// Both devices reuse WatchSnapshot's parser and moment ranking. This separate storage key
/// avoids mixing a phone snapshot with a watch snapshot on a shared development installation.
public enum PhoneWidgetSnapshot {
    public static let key = "phone.widget.snapshot"
    public static func make(
        today: Today, updatedAt: Date?, online: Bool, plugins: [PluginView], now: Date, calendar: Calendar
    ) -> WatchSnapshot {
        WatchSnapshot.make(
            today: today, online: online, state: .idle, now: now, calendar: calendar, plugins: plugins,
            updatedAt: today.date == DueText.format(now, "yyyy-MM-dd", calendar) ? updatedAt : nil)
    }
    public static func save(_ snapshot: WatchSnapshot, to defaults: UserDefaults) {
        if let data = try? JSONEncoder.assistant().encode(snapshot) { defaults.set(data, forKey: key) }
    }
    public static func load(from defaults: UserDefaults) -> WatchSnapshot? {
        defaults.data(forKey: key).flatMap { try? JSONDecoder.assistant().decode(WatchSnapshot.self, from: $0) }
    }
}

public struct PhoneWidgetContent: Equatable, Sendable {
    public var title: String
    public var detail: String
    public var remaining: Int?
    public var todos: [WatchSnapshot.Item]
    public var stale: Bool
    public var destination: PhoneDestination

    public init(snapshot: WatchSnapshot?, now: Date, calendar: Calendar) {
        guard let snapshot else {
            title = "打开 App，看看今天"
            detail = "还没收到数据"
            remaining = nil
            todos = []
            stale = true
            destination = .dashboard
            return
        }
        // Even a snapshot only minutes old can belong to yesterday. Never label yesterday's
        // pending count as today's, nor reuse its classes after a whole day has elapsed.
        stale =
            !calendar.isDate(snapshot.generatedAt, inSameDayAs: now)
            || now.timeIntervalSince(snapshot.generatedAt) >= 86400
        if stale {
            title = "打开 App 更新今天的安排"
            detail = "数据来自 " + DueText.format(snapshot.generatedAt, "M月d日 HH:mm", calendar)
            remaining = nil
            todos = []
            destination = .dashboard
        } else {
            let moment = snapshot.moment(at: now, calendar: calendar)
            title = moment.title
            detail = [moment.kicker, moment.detail].compactMap { $0 }.joined(separator: " · ")
            let tomorrow = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: now)) ?? now
            let open = snapshot.orderedTodos(at: now, calendar: calendar).filter {
                !$0.done && ($0.dueAt.map { $0 < tomorrow } ?? true)
            }
            todos = Array(open.prefix(3)).map {
                var item = $0
                item.overdue = snapshot.isOverdue(item, at: now, calendar: calendar)
                return item
            }
            remaining = open.count
            switch moment.bot {
            case .assistant: destination = .dashboard
            case .health: destination = .bot("health")
            case .courses: destination = .bot("timetable")
            }
        }
    }
}
