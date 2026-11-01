import Foundation

// The watch never talks to the backend (watchOS has no Tailscale, so away from home it could
// not reach the Mac). The iPhone sends it a small snapshot over WatchConnectivity; the watch
// asks the iPhone to refresh or to complete a todo. Everything here is plain data so both
// sides and the tests share it.

extension MascotState: Codable {}

/// What the watch shows, a page each: today's todos, how many are left, the assistant's state
/// and the weather (天气); today's and tomorrow's courses (课表); readiness and last night's
/// sleep (健康Bot).
public struct WatchSnapshot: Codable, Equatable, Sendable {
    public struct Item: Codable, Equatable, Identifiable, Sendable {
        public var id: String
        public var title: String
        /// Already in words ("今天 17:00", "已逾期 · 10月14日").
        public var due: String
        public var overdue: Bool
        /// "QQ · 软件工程2班 · 李老师".
        public var source: String
        public var done: Bool

        public init(
            id: String, title: String, due: String, overdue: Bool, source: String, done: Bool
        ) {
            self.id = id
            self.title = title
            self.due = due
            self.overdue = overdue
            self.source = source
            self.done = done
        }
    }

    /// 课表's 「今天的课」 or 「明天的课」 card.
    public struct Courses: Codable, Equatable, Sendable {
        public struct Course: Codable, Equatable, Sendable {
            public var title: String
            /// "08:00–09:35".
            public var time: String

            public init(title: String, time: String) {
                self.title = title
                self.time = time
            }

            /// Start and end in minutes since midnight, from "08:00–09:35".
            public var minutes: (start: Int, end: Int)? {
                let numbers = time.split { !$0.isNumber }.compactMap { Int($0) }
                guard numbers.count == 4 else { return nil }
                return (numbers[0] * 60 + numbers[1], numbers[2] * 60 + numbers[3])
            }
        }

        /// 「今天的课」, or 「今天的课 · 推测」 while the school page has not shown this week; the
        /// same for 「明天的课」.
        public var title: String
        public var items: [Course]
        public var mascot: PluginMascot?

        public init(title: String, items: [Course], mascot: PluginMascot?) {
            self.title = title
            self.items = items
            self.mascot = mascot
        }

        public var estimated: Bool { title.contains("推测") }

        /// At `minute` (since midnight): the course going on, else the next one; nil after the
        /// last.
        public func current(atMinute minute: Int) -> Int? {
            items.firstIndex { ($0.minutes?.end ?? Int.max) > minute }
        }
    }

    /// 健康Bot's 「今日准备度」 and 「昨晚睡眠」 cards.
    public struct Health: Codable, Equatable, Sendable {
        public struct Stat: Codable, Equatable, Sendable {
            public var value: String
            public var unit: String?
            public var label: String?
            public var delta: String?

            public init(value: String, unit: String? = nil, label: String? = nil, delta: String? = nil) {
                self.value = value
                self.unit = unit
                self.label = label
                self.delta = delta
            }
        }

        public var readiness: Stat?
        public var sleep: Stat?
        public var mascot: PluginMascot?

        public init(readiness: Stat?, sleep: Stat?, mascot: PluginMascot?) {
            self.readiness = readiness
            self.sleep = sleep
            self.mascot = mascot
        }
    }

    /// 天气's 「<地方>今天」, 「出门提醒」 and 「明天」 cards, on 助理Bot's page.
    public struct Weather: Codable, Equatable, Sendable {
        public struct Day: Codable, Equatable, Sendable {
            /// "11–23".
            public var temperatures: String
            public var unit: String?
            /// "晴 · 降水概率 0%".
            public var label: String?
            /// An SF Symbol, e.g. "sun.max.fill".
            public var symbol: String?

            public init(temperatures: String, unit: String?, label: String?, symbol: String?) {
                self.temperatures = temperatures
                self.unit = unit
                self.label = label
                self.symbol = symbol
            }
        }

        /// "上海".
        public var place: String
        public var today: Day?
        /// 「出门提醒」 for today: "不用带伞", "外套或卫衣".
        public var advice: [String]
        public var tomorrow: Day?

        public init(place: String, today: Day?, advice: [String], tomorrow: Day?) {
            self.place = place
            self.today = today
            self.advice = advice
            self.tomorrow = tomorrow
        }

        /// The day the watch shows at `date`: today's with its advice, tomorrow's from 19:00
        /// (what to wear in the morning), and after midnight, before a new snapshot, the
        /// forecast that was tomorrow's as today's.
        public func shown(at date: Date, since generatedAt: Date, calendar: Calendar) -> Shown? {
            let days = calendar.dateComponents(
                [.day], from: calendar.startOfDay(for: generatedAt), to: calendar.startOfDay(for: date)
            ).day
            switch days {
            case 0:
                if calendar.component(.hour, from: date) >= 19, let tomorrow {
                    return Shown(day: tomorrow, isToday: false, advice: [])
                }
                return today.map { Shown(day: $0, isToday: true, advice: advice) }
            case 1:
                return tomorrow.map { Shown(day: $0, isToday: true, advice: []) }
            default:
                return nil
            }
        }

        public struct Shown: Equatable, Sendable {
            public var day: Day
            public var isToday: Bool
            public var advice: [String]
        }
    }

    public var generatedAt: Date
    /// Whether the iPhone reached the Mac when it made this snapshot.
    public var online: Bool
    public var state: MascotState
    public var summary: String
    public var todos: [Item]
    /// Nil while the plugin is off or has not shown these yet (and in snapshots from before
    /// they existed).
    public var courses: Courses?
    public var tomorrowCourses: Courses?
    public var health: Health?
    public var weather: Weather?

    public init(
        generatedAt: Date, online: Bool, state: MascotState, summary: String, todos: [Item],
        courses: Courses? = nil, tomorrowCourses: Courses? = nil, health: Health? = nil,
        weather: Weather? = nil
    ) {
        self.generatedAt = generatedAt
        self.online = online
        self.state = state
        self.summary = summary
        self.todos = todos
        self.courses = courses
        self.tomorrowCourses = tomorrowCourses
        self.health = health
        self.weather = weather
    }

    /// The courses page at some moment: whose day they are.
    public struct CoursePage: Equatable, Sendable {
        public var courses: Courses
        /// False when these are tomorrow's, shown once today's are over.
        public var isToday: Bool
        /// Today had courses (and they are over), as opposed to a day without any.
        public var hadCoursesToday: Bool
    }

    /// Today's courses until the last one ends, then tomorrow's. After midnight, before the
    /// iPhone sends a new snapshot, the courses that were tomorrow's are today's. Nil when the
    /// snapshot does not know that day.
    public func coursePage(at date: Date, calendar: Calendar) -> CoursePage? {
        let days = calendar.dateComponents(
            [.day], from: calendar.startOfDay(for: generatedAt), to: calendar.startOfDay(for: date)
        ).day
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        let minute = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        switch days {
        case 0:
            let had = !(courses?.items.isEmpty ?? true)
            if let courses, courses.current(atMinute: minute) != nil {
                return CoursePage(courses: courses, isToday: true, hadCoursesToday: true)
            }
            if let tomorrowCourses {
                return CoursePage(courses: tomorrowCourses, isToday: false, hadCoursesToday: had)
            }
            return courses.map { CoursePage(courses: $0, isToday: true, hadCoursesToday: had) }
        case 1:
            return tomorrowCourses.map {
                CoursePage(courses: $0, isToday: true, hadCoursesToday: !$0.items.isEmpty)
            }
        default:
            return nil
        }
    }

    public var remaining: Int { todos.filter { !$0.done }.count }

    /// Built on the iPhone from the latest `Today`, whose cards are every running plugin's;
    /// `plugins` give the pages their characters.
    public static func make(
        today: Today?, online: Bool, state: MascotState, now: Date, calendar: Calendar,
        plugins: [PluginView] = []
    ) -> WatchSnapshot {
        let items = (today?.todos ?? []).filter { $0.status != .deleted }.map { todo in
            Item(
                id: todo.id, title: todo.title,
                due: DueText.describe(todo, now: now, calendar: calendar),
                overdue: todo.status == .pending
                    && DueText.isOverdue(todo, now: now, calendar: calendar),
                source: TodoSourceText.describe(todo), done: todo.status == .completed)
        }
        let cards = today?.cards ?? []
        func mascot(_ id: String) -> PluginMascot? { plugins.first { $0.id == id }?.mascot }
        return WatchSnapshot(
            generatedAt: now, online: online, state: state,
            summary: today?.summary ?? "", todos: items,
            courses: courses(in: cards, titled: "今天的课", mascot: mascot("timetable")),
            tomorrowCourses: courses(in: cards, titled: "明天的课", mascot: mascot("timetable")),
            health: health(in: cards, mascot: mascot("health")),
            weather: weather(in: cards))
    }

    /// A text card 「今天的课」 means a day without courses.
    static func courses(in cards: [Card], titled title: String, mascot: PluginMascot?) -> Courses? {
        guard let card = cards.first(where: { $0.title.hasPrefix(title) }) else { return nil }
        var items: [Courses.Course] = []
        if case .list(let list) = card.content {
            items = list.map { Courses.Course(title: $0.title, time: $0.subtitle ?? "") }
        }
        return Courses(title: card.title, items: items, mascot: mascot)
    }

    static func health(in cards: [Card], mascot: PluginMascot?) -> Health? {
        func stat(_ title: String) -> Health.Stat? {
            guard let card = cards.first(where: { $0.type == .stat && $0.title == title }),
                case .stat(let stat) = card.content
            else { return nil }
            return Health.Stat(value: stat.value, unit: stat.unit, label: stat.label, delta: stat.delta)
        }
        let readiness = stat("今日准备度"), sleep = stat("昨晚睡眠")
        guard readiness != nil || sleep != nil else { return nil }
        return Health(readiness: readiness, sleep: sleep, mascot: mascot)
    }

    /// The forecasts are the stat cards with a weather symbol: 「上海今天」 and 「明天」.
    static func weather(in cards: [Card]) -> Weather? {
        func day(_ card: Card?) -> Weather.Day? {
            guard let card, case .stat(let stat) = card.content else { return nil }
            return Weather.Day(temperatures: stat.value, unit: stat.unit, label: stat.label, symbol: card.symbol)
        }
        let forecasts = cards.filter { $0.type == .stat && $0.symbol != nil }
        let today = forecasts.first { $0.title.hasSuffix("今天") }
        let tomorrow = forecasts.first { $0.title == "明天" }
        guard today != nil || tomorrow != nil else { return nil }
        var advice: [String] = []
        if let card = cards.first(where: { $0.title == "出门提醒" }), case .list(let items) = card.content {
            advice = items.map(\.title)
        }
        return Weather(
            place: today.map { String($0.title.dropLast(2)) } ?? "", today: day(today), advice: advice,
            tomorrow: day(tomorrow))
    }

    /// The watch ticks a todo off before the iPhone confirms it.
    public func setting(_ id: String, done: Bool) -> WatchSnapshot {
        var copy = self
        if let index = copy.todos.firstIndex(where: { $0.id == id }) {
            copy.todos[index].done = done
            if done { copy.todos[index].overdue = false }
        }
        return copy
    }

    // WatchConnectivity dictionaries carry the snapshot as JSON data.
    public static let key = "snapshot"

    public func message() throws -> [String: Any] {
        [Self.key: try JSONEncoder.assistant().encode(self)]
    }

    public init?(message: [String: Any]) {
        guard let data = message[Self.key] as? Data,
            let snapshot = try? JSONDecoder.assistant().decode(WatchSnapshot.self, from: data)
        else { return nil }
        self = snapshot
    }
}

/// What the watch asks the iPhone.
public enum WatchRequest: Equatable, Sendable {
    /// Refresh from the Mac and send a new snapshot.
    case refresh
    /// Complete (or reopen) a todo.
    case setDone(id: String, done: Bool)

    public var message: [String: Any] {
        switch self {
        case .refresh: return ["request": "refresh"]
        case .setDone(let id, let done): return ["request": "set_done", "id": id, "done": done]
        }
    }

    public init?(message: [String: Any]) {
        guard let kind = message["request"] as? String else { return nil }
        switch kind {
        case "refresh":
            self = .refresh
        case "set_done":
            guard let id = message["id"] as? String, let done = message["done"] as? Bool else {
                return nil
            }
            self = .setDone(id: id, done: done)
        default:
            return nil
        }
    }
}

/// The numbers the watch face complication shows, shared through the App Group between the
/// watch app and its widget extension.
public struct ComplicationData: Equatable, Sendable {
    public var remaining: Int
    public var updated: Date

    public init(remaining: Int, updated: Date) {
        self.remaining = remaining
        self.updated = updated
    }

    static let remainingKey = "complication.remaining"
    static let updatedKey = "complication.updated"

    /// The App Group named in the target's Info.plist (`AssistantAppGroup`).
    public static func defaults(bundle: Bundle = .main) -> UserDefaults {
        if let group = bundle.object(forInfoDictionaryKey: "AssistantAppGroup") as? String,
            let shared = UserDefaults(suiteName: group)
        {
            return shared
        }
        return .standard
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(remaining, forKey: Self.remainingKey)
        defaults.set(updated.timeIntervalSince1970, forKey: Self.updatedKey)
    }

    /// Nil until the watch received its first snapshot.
    public static func load(from defaults: UserDefaults) -> ComplicationData? {
        guard defaults.object(forKey: updatedKey) != nil else { return nil }
        return ComplicationData(
            remaining: defaults.integer(forKey: remainingKey),
            updated: Date(timeIntervalSince1970: defaults.double(forKey: updatedKey)))
    }
}
