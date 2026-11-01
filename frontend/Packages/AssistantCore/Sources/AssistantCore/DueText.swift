import Foundation

/// How a todo's deadline reads in the lists: "今天 17:00", "明天", "10月20日",
/// "已逾期 · 10月14日", "无截止时间".
public enum DueText {
    public static func describe(
        _ todo: Todo, now: Date = Date(), calendar: Calendar = DueText.calendar()
    ) -> String {
        guard let due = todo.dueAt else { return "无截止时间" }
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: now) ?? now
        var text: String
        if calendar.isDate(due, inSameDayAs: now) {
            text = "今天"
        } else if calendar.isDate(due, inSameDayAs: tomorrow) {
            text = "明天"
        } else {
            text = format(due, "M月d日", calendar)
        }
        if todo.hasTime {
            text += " " + format(due, "HH:mm", calendar)
        }
        if todo.status == .pending && isOverdue(todo, now: now, calendar: calendar) {
            text = "已逾期 · " + text
        }
        return text
    }

    /// A date-only deadline is overdue from the next day on, a timed one from its minute.
    public static func isOverdue(_ todo: Todo, now: Date, calendar: Calendar) -> Bool {
        guard let due = todo.dueAt else { return false }
        if todo.hasTime { return due < now }
        return calendar.startOfDay(for: due) < calendar.startOfDay(for: now)
    }

    /// Gregorian, in the device's time zone.
    public static func calendar(timeZone: TimeZone = .current) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        calendar.locale = Locale(identifier: "zh_CN")
        return calendar
    }

    public static func format(_ date: Date, _ pattern: String, _ calendar: Calendar) -> String {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = pattern
        return formatter.string(from: date)
    }
}
