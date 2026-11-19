import Foundation

// The watch answers three questions at a raise of the wrist: what now, what next, how am I
// today. Which thing answers the first one changes through the day, and both the App and the
// complications must agree on it, so the choice is made here, from the snapshot and the time,
// with no views involved.

/// The watch App's pages, which a complication opens.
public enum WatchPage: String, CaseIterable, Sendable {
    case now, todos, courses, health

    public static let scheme = "aemeath-watch"

    public var url: URL { URL(string: "\(Self.scheme)://\(rawValue)")! }

    public init?(url: URL) {
        guard url.scheme == Self.scheme, let host = url.host, let page = WatchPage(rawValue: host)
        else { return nil }
        self = page
    }
}

/// The Bot a moment belongs to: its sticker and color.
public enum WatchBot: Equatable, Sendable {
    case assistant, courses, health
}

/// A course on the calendar: `Courses.Course` with its day applied.
public struct WatchClass: Equatable, Sendable {
    public var title: String
    public var start: Date
    public var end: Date
    public var isOwnEvent: Bool
    /// False for tomorrow's, which the courses page shows once today's are over.
    public var isToday: Bool
    public var estimated: Bool

    public func isRunning(at date: Date) -> Bool { start <= date && date < end }

    /// Elapsed share of the class at `date`, 0 to 1.
    public func progress(at date: Date) -> Double {
        let length = end.timeIntervalSince(start)
        guard length > 0 else { return 0 }
        return min(max(date.timeIntervalSince(start) / length, 0), 1)
    }
}

/// What the watch's first page leads with at some moment, already in words.
public struct WatchMoment: Equatable, Sendable {
    public enum Kind: String, Equatable, Sendable {
        case inClass, nextClass, deadline, overdue, todo, readiness, tomorrow, clear
    }

    /// The big figure, 「52」 and 「分钟」.
    public struct Figure: Equatable, Sendable {
        public var value: String
        public var unit: String?

        public init(value: String, unit: String? = nil) {
            self.value = value
            self.unit = unit
        }

        public var text: String { value + (unit.map { " \($0)" } ?? "") }
    }

    /// A line of what comes after the lead.
    public struct Line: Equatable, Sendable, Identifiable {
        public enum Icon: String, Sendable {
            case course, deadline, todos, sleep
        }

        public var icon: Icon
        /// "13:57", "明天 08:00"; nil when the line is not about a time.
        public var time: String?
        public var text: String
        public var page: WatchPage

        public var id: String { "\(icon.rawValue)-\(time ?? "")-\(text)" }
    }

    public var kind: Kind
    public var bot: WatchBot
    /// What kind of thing this is: 「正在上课」, 「快截止了」.
    public var kicker: String
    public var title: String
    public var figure: WatchMoment.Figure?
    /// Under the figure: 「12:57 下课」.
    public var detail: String?
    /// Where a todo came from.
    public var note: String?
    /// 0 to 1: the class gone by, the score out of 10.
    public var progress: Double?
    /// Set when the lead is a todo: it can be ticked off right there.
    public var todo: WatchSnapshot.Item?
    /// Late or nearly so: the kicker goes orange.
    public var urgent: Bool
    /// An SF Symbol beside the figure when the figure is the weather.
    public var symbol: String?
    public var lines: [Line]
    /// How much this is worth a place on top of the Smart Stack, 0 to 100.
    public var relevance: Float

    /// The page with the rest of what this is about.
    public var page: WatchPage {
        switch kind {
        case .inClass, .nextClass, .tomorrow: return .courses
        case .deadline, .overdue, .todo: return .todos
        case .readiness: return .health
        case .clear: return .now
        }
    }
}

extension WatchSnapshot {
    /// Before these hours the lead is whatever else is on; from them to `readinessEnds`, 健康's
    /// score, which is what one looks up right after getting up.
    static let readinessStarts = 5
    static let readinessEnds = 10
    /// From this hour with nothing left, tomorrow's first class leads. (Weather's own switch at
    /// 19:00 is `Weather.shown`.)
    static let eveningStarts = 18
    /// A class this close leads over everything but a deadline even closer.
    static let classSoon: TimeInterval = 30 * 60
    static let deadlineUrgent: TimeInterval = 60 * 60
    static let deadlineNear: TimeInterval = 3 * 60 * 60

    /// The classes the courses page shows at `date`, on the calendar, in time order.
    public func classes(at date: Date, calendar: Calendar) -> [WatchClass] {
        guard let page = coursePage(at: date, calendar: calendar) else { return [] }
        return Self.placed(page.courses, isToday: page.isToday, at: date, calendar: calendar)
    }

    static func placed(_ courses: Courses, isToday: Bool, at date: Date, calendar: Calendar) -> [WatchClass] {
        let today = calendar.startOfDay(for: date)
        guard let day = isToday ? today : calendar.date(byAdding: .day, value: 1, to: today) else { return [] }
        return courses.items.compactMap { course in
            guard let minutes = course.minutes,
                let start = calendar.date(byAdding: .minute, value: minutes.start, to: day),
                let end = calendar.date(byAdding: .minute, value: minutes.end, to: day)
            else { return nil }
            return WatchClass(
                title: course.title, start: start, end: end, isOwnEvent: course.isOwnEvent, isToday: isToday,
                estimated: courses.estimated)
        }
    }

    /// The class going on, else the next one, today's or (once they are over) tomorrow's.
    public func currentClass(at date: Date, calendar: Calendar) -> WatchClass? {
        classes(at: date, calendar: calendar).first { $0.end > date }
    }

    enum Urgency: Equatable {
        case overdue(Date?)
        case timed(Date)
        /// A date-only deadline that is still ahead.
        case date
        case none

        /// Late first, then by time; a day without a time after those with one.
        var rank: Int {
            switch self {
            case .overdue: return 0
            case .timed: return 1
            case .date: return 2
            case .none: return 3
            }
        }

        var due: Date? {
            switch self {
            case .overdue(let date): return date
            case .timed(let date): return date
            case .date, .none: return nil
            }
        }
    }

    func urgency(of item: Item, at date: Date, calendar: Calendar) -> Urgency {
        guard let due = item.dueAt else { return item.overdue ? .overdue(nil) : .none }
        if item.hasTime == true { return due <= date ? .overdue(due) : .timed(due) }
        return DueText.isOverdue(due: due, hasTime: false, now: date, calendar: calendar) ? .overdue(due) : .date
    }

    /// The todos in the order the watch lists them. A ticked one keeps its place, so that the
    /// row under the finger does not jump away.
    public func orderedTodos(at date: Date, calendar: Calendar) -> [Item] {
        todos.enumerated().sorted { lhs, rhs in
            let a = urgency(of: lhs.element, at: date, calendar: calendar)
            let b = urgency(of: rhs.element, at: date, calendar: calendar)
            if a.rank != b.rank { return a.rank < b.rank }
            if let first = a.due, let second = b.due, first != second { return first < second }
            return lhs.offset < rhs.offset
        }.map(\.element)
    }

    /// The moment's todo deadline in words, afresh when the snapshot says when it is.
    public func dueWords(of item: Item, at date: Date, calendar: Calendar) -> String {
        guard item.dueAt != nil else { return item.due }
        return DueText.describe(
            due: item.dueAt, hasTime: item.hasTime == true, pending: !item.done, now: date, calendar: calendar)
    }

    public func isOverdue(_ item: Item, at date: Date, calendar: Calendar) -> Bool {
        guard !item.done else { return false }
        if case .overdue = urgency(of: item, at: date, calendar: calendar) { return true }
        return false
    }

    /// What leads, in this order: a deadline within the hour; the class going on; the next
    /// class within half an hour; in the morning, today's readiness; a deadline within three
    /// hours; something already late; any other deadline today; a todo without a time; the next
    /// class later today; from the evening, tomorrow's first class; else a clear day.
    /// A todo beats a class hours away because it can be done now, and the class is still
    /// shown on the line under it.
    public func moment(at date: Date, calendar: Calendar) -> WatchMoment {
        let page = coursePage(at: date, calendar: calendar)
        let placed = classes(at: date, calendar: calendar)
        let today = placed.filter(\.isToday)
        let running = today.first { $0.isRunning(at: date) }
        let upcoming = today.first { $0.start > date }
        let open = orderedTodos(at: date, calendar: calendar).filter { !$0.done }
        let timed: [(item: Item, due: Date)] = open.compactMap { item in
            if case .timed(let due) = urgency(of: item, at: date, calendar: calendar) { return (item, due) }
            return nil
        }
        let late = open.first { urgency(of: $0, at: date, calendar: calendar).rank == 0 }
        let plain = open.first { urgency(of: $0, at: date, calendar: calendar).rank >= 2 }
        let builder = MomentBuilder(
            snapshot: self, date: date, calendar: calendar, page: page, today: today,
            tomorrow: placed.first { !$0.isToday }, running: running, upcoming: upcoming, open: open, timed: timed)

        if let first = timed.first, first.due.timeIntervalSince(date) <= Self.deadlineUrgent {
            return builder.deadline(first.item, due: first.due, urgent: true)
        }
        if let running { return builder.inClass(running) }
        if let upcoming, upcoming.start.timeIntervalSince(date) <= Self.classSoon { return builder.nextClass(upcoming) }
        if let readiness = builder.morningReadiness() { return readiness }
        if let first = timed.first, first.due.timeIntervalSince(date) <= Self.deadlineNear {
            return builder.deadline(first.item, due: first.due, urgent: false)
        }
        if let late { return builder.overdue(late) }
        if let first = timed.first { return builder.deadline(first.item, due: first.due, urgent: false) }
        if let plain { return builder.todo(plain) }
        if let upcoming { return builder.nextClass(upcoming) }
        if let tomorrow = builder.tomorrow, calendar.component(.hour, from: date) >= Self.eveningStarts {
            return builder.tomorrowFirst(tomorrow)
        }
        return builder.clear()
    }

    /// When what the complications show can change, soonest first, in the `hours` after `date`:
    /// every edge of the ranking in `moment`, of the page of courses and of the weather.
    public func changes(after date: Date, hours: Int, calendar: Calendar) -> [Date] {
        var points: [Date] = []
        let today = calendar.startOfDay(for: date)
        for offset in -1...2 {
            guard let day = calendar.date(byAdding: .day, value: offset, to: today) else { continue }
            points.append(day)
            for hour in [Self.readinessStarts, Self.readinessEnds, Self.eveningStarts, 19] {
                if let point = calendar.date(bySettingHour: hour, minute: 0, second: 0, of: day) { points.append(point) }
            }
            for (courses, shift) in [(courses, 0), (tomorrowCourses, 1)] {
                guard let courses, let classDay = calendar.date(byAdding: .day, value: shift, to: day) else { continue }
                for course in courses.items {
                    guard let minutes = course.minutes else { continue }
                    for minute in [minutes.start - Int(Self.classSoon / 60), minutes.start, minutes.end] {
                        if let point = calendar.date(byAdding: .minute, value: minute, to: classDay) { points.append(point) }
                    }
                }
            }
        }
        for item in todos where !item.done && item.hasTime == true {
            guard let due = item.dueAt else { continue }
            points += [due.addingTimeInterval(-Self.deadlineNear), due.addingTimeInterval(-Self.deadlineUrgent), due]
        }
        let limit = date.addingTimeInterval(TimeInterval(hours) * 3600)
        return Array(Set(points)).filter { $0 > date && $0 <= limit }.sorted()
    }
}

/// Puts the words on each kind of lead; one per `moment` call.
private struct MomentBuilder {
    typealias Item = WatchSnapshot.Item
    typealias Line = WatchMoment.Line

    let snapshot: WatchSnapshot
    let date: Date
    let calendar: Calendar
    let page: WatchSnapshot.CoursePage?
    let today: [WatchClass]
    let tomorrow: WatchClass?
    let running: WatchClass?
    let upcoming: WatchClass?
    let open: [Item]
    let timed: [(item: Item, due: Date)]

    func clock(_ time: Date) -> String { DueText.format(time, "HH:mm", calendar) }

    func inClass(_ slot: WatchClass) -> WatchMoment {
        WatchMoment(
            kind: .inClass, bot: .courses, kicker: slot.isOwnEvent ? "正在进行" : "正在上课", title: slot.title,
            figure: WatchFigures.until(slot.end, from: date),
            detail: clock(slot.end) + (slot.isOwnEvent ? " 结束" : " 下课"), note: nil,
            progress: slot.progress(at: date), todo: nil, urgent: false, symbol: nil,
            lines: lines(class: slot, todo: nil), relevance: 90)
    }

    func nextClass(_ slot: WatchClass) -> WatchMoment {
        let soon = slot.start.timeIntervalSince(date) <= WatchSnapshot.classSoon
        return WatchMoment(
            kind: .nextClass, bot: .courses, kicker: slot.isOwnEvent ? "接下来" : "下一节课", title: slot.title,
            figure: WatchFigures.until(slot.start, from: date),
            detail: clock(slot.start) + (slot.isOwnEvent ? " 开始" : " 上课"), note: nil,
            progress: soon ? 1 - slot.start.timeIntervalSince(date) / WatchSnapshot.classSoon : nil, todo: nil,
            urgent: false, symbol: nil, lines: lines(class: slot, todo: nil), relevance: soon ? 75 : 35)
    }

    func deadline(_ item: Item, due: Date, urgent: Bool) -> WatchMoment {
        WatchMoment(
            kind: .deadline, bot: .assistant,
            kicker: urgent ? "快截止了" : calendar.isDate(due, inSameDayAs: date) ? "今天截止" : "之后截止", title: item.title,
            figure: WatchFigures.until(due, from: date), detail: dueDetail(due, hasTime: true), note: source(item),
            progress: closeness(to: due), todo: item, urgent: urgent, symbol: nil, lines: lines(class: nil, todo: item),
            relevance: urgent ? 100 : 60)
    }

    func overdue(_ item: Item) -> WatchMoment {
        let due = snapshot.urgency(of: item, at: date, calendar: calendar).due
        return WatchMoment(
            kind: .overdue, bot: .assistant, kicker: "已逾期", title: item.title,
            figure: due.map { WatchFigures.since($0, hasTime: item.hasTime == true, to: date, calendar: calendar) },
            detail: due.map { dueDetail($0, hasTime: item.hasTime == true && calendar.isDate($0, inSameDayAs: date)) },
            note: source(item), progress: 1,
            todo: item, urgent: true, symbol: nil, lines: lines(class: nil, todo: item), relevance: 50)
    }

    func todo(_ item: Item) -> WatchMoment {
        WatchMoment(
            kind: .todo, bot: .assistant, kicker: item.dueAt == nil ? "还没做" : "今天要做", title: item.title,
            figure: nil, detail: nil, note: source(item), progress: nil, todo: item, urgent: false, symbol: nil,
            lines: lines(class: nil, todo: item), relevance: 40)
    }

    func morningReadiness() -> WatchMoment? {
        let hour = calendar.component(.hour, from: date)
        guard (WatchSnapshot.readinessStarts..<WatchSnapshot.readinessEnds).contains(hour),
            calendar.isDate(snapshot.generatedAt, inSameDayAs: date),
            let readiness = snapshot.health?.readiness, let score = readiness.score
        else { return nil }
        var extra: [Line] = []
        if let sleep = snapshot.health?.sleep, sleep.score == nil, sleep.value != "没有记录" {
            extra.append(Line(icon: .sleep, time: nil, text: "睡了 \(sleep.value)", page: .health))
        }
        return WatchMoment(
            kind: .readiness, bot: .health, kicker: "今日准备度", title: readiness.band ?? readiness.value,
            figure: WatchMoment.Figure(value: readiness.value, unit: readiness.unit), detail: readiness.change,
            note: nil, progress: min(max(score / 10, 0), 1), todo: nil, urgent: false, symbol: nil,
            lines: Array((extra + lines(class: nil, todo: nil)).prefix(WatchFigures.maxLines)), relevance: 65)
    }

    func tomorrowFirst(_ slot: WatchClass) -> WatchMoment {
        WatchMoment(
            kind: .tomorrow, bot: .courses, kicker: slot.isOwnEvent ? "明天先是" : "明天第一节", title: slot.title,
            figure: WatchMoment.Figure(value: clock(slot.start)),
            detail: slot.estimated ? "按上周课表推测" : nil, note: nil, progress: nil, todo: nil, urgent: false, symbol: nil,
            lines: lines(class: slot, todo: nil), relevance: 20)
    }

    func clear() -> WatchMoment {
        let shown = snapshot.weather?.shown(at: date, since: snapshot.generatedAt, calendar: calendar)
        let finished = page?.hadCoursesToday == true && (page?.isToday == false || today.allSatisfy { $0.end <= date })
        let title: String
        if !snapshot.todos.isEmpty {
            title = "都做完了"
        } else if finished {
            title = "今天的课上完了"
        } else if page == nil, !calendar.isDate(snapshot.generatedAt, inSameDayAs: date) {
            // A snapshot from days ago knows nothing about today, so 「没有安排」 would be a guess.
            title = "还没收到今天的安排"
        } else {
            title = "今天没有安排"
        }
        return WatchMoment(
            kind: .clear, bot: .assistant, kicker: DueText.format(date, "M月d日 EEE", calendar), title: title,
            figure: shown.map { WatchMoment.Figure(value: ($0.isToday ? "" : "明天 ") + $0.day.temperatures + "°") },
            detail: shown?.day.label, note: nil, progress: nil, todo: nil, urgent: false, symbol: shown?.day.symbol,
            lines: lines(class: nil, todo: nil), relevance: 5)
    }

    /// Tomorrow's first class, also while the page of courses is still on today's.
    var tomorrowSlot: WatchClass? {
        if let tomorrow { return tomorrow }
        guard let courses = snapshot.tomorrowCourses, calendar.isDate(snapshot.generatedAt, inSameDayAs: date) else {
            return nil
        }
        return WatchSnapshot.placed(courses, isToday: false, at: date, calendar: calendar).first
    }

    /// What comes after the lead, in time order; what has no time only to fill the room.
    func lines(class leadClass: WatchClass?, todo leadTodo: Item?) -> [Line] {
        var result: [Line] = []
        if let running, running != leadClass {
            result.append(Line(icon: .course, time: "到 " + clock(running.end), text: running.title, page: .courses))
        }
        var events: [(at: Date, line: Line)] = today.filter { $0.start > date && $0 != leadClass }.map {
            ($0.start, Line(icon: .course, time: clock($0.start), text: $0.title, page: .courses))
        }
        events += timed.filter { $0.item.id != leadTodo?.id }.map {
            ($0.due, Line(icon: .deadline, time: clock($0.due), text: $0.item.title, page: .todos))
        }
        result += events.sorted { $0.at < $1.at }.map(\.line)
        if result.count < WatchFigures.maxLines {
            let others = open.filter { item in item.id != leadTodo?.id && !timed.contains { $0.item.id == item.id } }
            if !others.isEmpty {
                result.append(Line(icon: .todos, time: nil, text: "还有 \(others.count) 件待办", page: .todos))
            }
        }
        let todayIsOver = today.allSatisfy { $0.end <= date }
        if result.count < WatchFigures.maxLines, todayIsOver, let slot = tomorrowSlot, slot != leadClass {
            result.append(Line(icon: .course, time: "明天 " + clock(slot.start), text: slot.title, page: .courses))
        }
        return Array(result.prefix(WatchFigures.maxLines))
    }

    /// How much of the run-up to a deadline is gone: the last three hours when it is that
    /// close, else the day so far.
    func closeness(to due: Date) -> Double {
        let left = due.timeIntervalSince(date)
        let window = left <= WatchSnapshot.deadlineNear ? WatchSnapshot.deadlineNear : due.timeIntervalSince(calendar.startOfDay(for: date))
        guard window > 0 else { return 1 }
        return min(max(1 - left / window, 0), 1)
    }

    /// 「17:00 截止」, or the day when it is not today's.
    func dueDetail(_ due: Date, hasTime: Bool) -> String {
        var text = calendar.isDate(due, inSameDayAs: date) ? "今天" : DueText.format(due, "M月d日", calendar)
        if hasTime {
            let time = clock(due)
            text = calendar.isDate(due, inSameDayAs: date) ? time : text + " " + time
        }
        return text + " 截止"
    }

    /// 「软件工程2班」: the chat it came from, or the App when that is all that is known.
    func source(_ item: Item) -> String? {
        guard !item.source.isEmpty else { return nil }
        let parts = item.source.components(separatedBy: " · ")
        return parts.count > 1 ? parts[1] : parts[0]
    }
}

/// Times as the watch says them.
public enum WatchFigures {
    /// How many lines of what comes next a moment carries.
    static let maxLines = 2

    /// 「52 分钟」 up to 100 minutes, then 「2.5 小时」, then days. Rounded up for minutes, so
    /// that 「1 分钟」 stays until it is really over, and down to the half hour for hours.
    public static func until(_ end: Date, from date: Date) -> WatchMoment.Figure {
        let seconds = end.timeIntervalSince(date)
        guard seconds > 0 else { return WatchMoment.Figure(value: "0", unit: "分钟") }
        if seconds < 60 { return WatchMoment.Figure(value: "<1", unit: "分钟") }
        let minutes = Int((seconds / 60).rounded(.up))
        if minutes < 100 { return WatchMoment.Figure(value: "\(minutes)", unit: "分钟") }
        let hours = (Double(minutes) / 60 * 2).rounded(.down) / 2
        if hours >= 48 { return WatchMoment.Figure(value: "\(Int(hours / 24))", unit: "天") }
        return WatchMoment.Figure(value: hours.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(hours))" : "\(hours)", unit: "小时")
    }

    /// How late something is: minutes and hours for today's, days from the day after a deadline
    /// without a time.
    public static func since(_ due: Date, hasTime: Bool, to date: Date, calendar: Calendar) -> WatchMoment.Figure {
        let seconds = max(date.timeIntervalSince(due), 0)
        if hasTime && seconds < 3600 { return WatchMoment.Figure(value: "\(max(Int(seconds / 60), 1))", unit: "分钟") }
        if hasTime && seconds < 86400 { return WatchMoment.Figure(value: "\(Int(seconds / 3600))", unit: "小时") }
        let days =
            calendar.dateComponents([.day], from: calendar.startOfDay(for: due), to: calendar.startOfDay(for: date)).day ?? 0
        return WatchMoment.Figure(value: "\(max(days, 1))", unit: "天")
    }
}

extension WatchSnapshot.Health.Stat {
    /// The value when it is a number; 「数据不足」 and 「7小时12分」 are not.
    public var score: Double? { Double(value) }

    /// The score in a word.
    public var band: String? {
        guard let score else { return nil }
        if score >= 8 { return "状态很好" }
        if score >= 6 { return "状态不错" }
        if score >= 4 { return "状态一般" }
        return "状态偏低"
    }

    /// 「比昨天高 0.6」 from 「昨天 7.6」; the delta as it is when it does not say that.
    public var change: String? {
        guard let delta else { return nil }
        guard let score, delta.hasPrefix("昨天"), let yesterday = Double(delta.dropFirst(2).trimmingCharacters(in: .whitespaces))
        else { return delta }
        let difference = ((score - yesterday) * 10).rounded() / 10
        if difference == 0 { return "和昨天一样" }
        let text = String(format: "%g", abs(difference))
        return difference > 0 ? "比昨天高 \(text)" : "比昨天低 \(text)"
    }

    /// One score of what the readiness is made of.
    public struct Part: Equatable, Sendable {
        public var name: String
        public var score: Double
    }

    /// The scores in the readiness's label, 「夜间 HRV 7.9 · 静息心率 8.3 …」. Parts without a
    /// score (「昨晚没有 HRV」) and what the user logged themselves are left out: this is for the
    /// short bars beside the ring.
    public var parts: [Part] {
        (label ?? "").components(separatedBy: " · ").compactMap { piece in
            guard let cut = piece.lastIndex(of: " "), let score = Double(piece[piece.index(after: cut)...]) else { return nil }
            let name = String(piece[..<cut])
            guard !name.hasPrefix("你记的"), name != "体感" else { return nil }
            return Part(name: name.replacingOccurrences(of: "夜间 ", with: ""), score: score)
        }
    }

    /// Bedtime and getting up, from 「23:20 入睡 · 06:50 醒来」 or 「主睡眠 …（23:40–06:10）」.
    public var sleepSpan: (start: String, end: String)? {
        guard let label else { return nil }
        func capture(_ pattern: String) -> [String]? {
            guard let expression = try? NSRegularExpression(pattern: pattern),
                let match = expression.firstMatch(in: label, range: NSRange(label.startIndex..., in: label))
            else { return nil }
            return (1..<match.numberOfRanges).compactMap { Range(match.range(at: $0), in: label).map { String(label[$0]) } }
        }
        if let parts = capture(#"(\d{1,2}:\d{2}) 入睡.*?(\d{1,2}:\d{2}) 醒来"#), parts.count == 2 {
            return (parts[0], parts[1])
        }
        if let parts = capture(#"（(\d{1,2}:\d{2})–(\d{1,2}:\d{2})）"#), parts.count == 2 {
            return (parts[0], parts[1])
        }
        return nil
    }
}

extension WatchSnapshot.Weather.Shown {
    /// The day in a line: 「9–21° · 不用带伞」; in the evening 「明天 12–18° · 小雨 · 要带伞」.
    public var line: String {
        var parts = [(isToday ? "" : "明天 ") + day.temperatures + "°"]
        if !advice.isEmpty {
            parts += advice.prefix(1)
        } else if let label = day.label {
            let pieces = label.components(separatedBy: " · ")
            parts += pieces.prefix(1)
            parts += pieces.dropFirst().filter { $0.contains("伞") }
        }
        return parts.joined(separator: " · ")
    }
}
