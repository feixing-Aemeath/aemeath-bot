#if DEBUG
    import Foundation

    /// Invented states of the watch for the simulator: `-watch.mock <name>` at launch. Debug
    /// builds only. Courses, deadlines and names are made up; there is no finance in any of them.
    public enum WatchMock: String, CaseIterable, Sendable {
        /// A class going on, todos waiting.
        case inClass = "class"
        /// The next class in a quarter of an hour.
        case soon
        /// A deadline within the hour.
        case deadline
        /// Something already late, nothing else near.
        case overdue
        /// Just got up: readiness leads.
        case morning
        /// Today's classes are over: tomorrow's show, with tomorrow's weather.
        case classesOver = "classes-over"
        /// Every todo ticked off on a day without classes.
        case allDone = "all-done"
        /// No classes and no todos.
        case free
        /// Only a todo without a time.
        case todo
        /// The iPhone could not reach the Mac.
        case macOffline = "mac-offline"
        /// The iPhone out of reach: ticks wait for it.
        case iphoneAway = "iphone-away"
        /// A snapshot two days old.
        case stale
        /// Long titles, to see what the small watch does with them.
        case long
        /// Readiness and sleep that could not be worked out.
        case noReadiness = "no-readiness"
        /// A class going on around the real time, for the complications on a simulator's face,
        /// which go by the real clock.
        case liveClass = "live-class"
        /// Nothing from the iPhone yet.
        case noData = "no-data"

        /// `1` is the usual state, as in `-watch.mock 1`.
        public init?(argument: String) {
            if argument == "1" {
                self = .inClass
            } else {
                self.init(rawValue: argument)
            }
        }

        /// The time of day this state happens at. The simulator's clock cannot be set, so the
        /// App adds the difference to its own idea of now (the status bar keeps the real time).
        public var clock: (hour: Int, minute: Int) {
            switch self {
            case .inClass, .macOffline, .iphoneAway, .stale, .liveClass, .noReadiness: return (10, 42)
            case .soon: return (13, 45)
            case .deadline: return (16, 22)
            case .overdue: return (12, 40)
            case .long: return (11, 8)
            case .morning: return (7, 12)
            case .classesOver: return (20, 16)
            case .allDone: return (15, 30)
            case .free: return (14, 30)
            case .todo: return (15, 30)
            case .noData: return (10, 42)
            }
        }

        /// The state's own "now", on the day of `date`.
        public func now(on date: Date, calendar: Calendar) -> Date {
            guard self != .liveClass else { return date }
            return calendar.date(bySettingHour: clock.hour, minute: clock.minute, second: 0, of: date) ?? date
        }

        public func snapshot(now: Date, calendar: Calendar) -> WatchSnapshot? {
            let day = calendar.startOfDay(for: now)
            func at(_ hour: Int, _ minute: Int = 0, days: Int = 0) -> Date {
                let shifted = calendar.date(byAdding: .day, value: days, to: day) ?? day
                return calendar.date(bySettingHour: hour, minute: minute, second: 0, of: shifted) ?? shifted
            }
            func todo(
                _ id: String, _ title: String, due: Date?, hasTime: Bool, source: String = "QQ · 计算机2班 · 班长",
                done: Bool = false
            ) -> WatchSnapshot.Item {
                WatchSnapshot.Item(
                    id: id, title: title,
                    due: DueText.describe(due: due, hasTime: hasTime, pending: !done, now: now, calendar: calendar),
                    overdue: !done && due.map { DueText.isOverdue(due: $0, hasTime: hasTime, now: now, calendar: calendar) } == true,
                    source: source, done: done, dueAt: due, hasTime: due == nil ? nil : hasTime)
            }
            let moning = PluginMascot(sticker: "moning", accent: "#D65F63")
            let lily = PluginMascot(sticker: "lily_veil", accent: "#8068B2")
            func courses(_ title: String, _ items: [(String, String, String?)]) -> WatchSnapshot.Courses {
                WatchSnapshot.Courses(
                    title: title, items: items.map { .init(title: $0.0, time: $0.1, detail: $0.2) }, mascot: moning)
            }
            let tuesday = courses(
                "今天的课",
                [
                    ("大学英语（1）", "08:00–09:35", nil), ("高等数学I（A）", "09:55–12:20", nil), ("数据结构", "14:00–15:35", nil),
                    ("社团例会", "19:00–20:00", "自己的日程"),
                ])
            let wednesday = courses(
                "明天的课 · 推测", [("大学生职业发展与就业指导（A）", "08:00–09:35", nil), ("线性代数", "10:00–11:35", nil)])
            let health = WatchSnapshot.Health(
                readiness: .init(
                    value: "8.2", unit: "/ 10", label: "夜间 HRV 7.9 · 静息心率 8.3 · 睡眠 9.0 · 训练负荷 7.0", delta: "昨天 7.6"),
                sleep: .init(value: "7小时12分", label: "23:20 入睡 · 06:50 醒来", delta: "比近 30 天中位数多 18分"), mascot: lily)
            let weather = WatchSnapshot.Weather(
                place: "上海", today: .init(temperatures: "9–21", unit: "°C", label: "多云 · 降水概率 10%", symbol: "cloud.sun.fill"),
                advice: ["不用带伞", "外套或卫衣"],
                tomorrow: .init(temperatures: "12–18", unit: "°C", label: "小雨 · 降水概率 70% · 要带伞", symbol: "cloud.drizzle.fill"))
            let todos = [
                todo("homework", "交数据结构第三次作业", due: at(17), hasTime: true, source: "QQ · 计算机2班 · 李老师"),
                todo("info", "学生信息核对", due: at(22), hasTime: true, source: "飞书 · 班委群"),
                todo("dorm", "补交寝室卫生评比材料", due: at(12, days: -2), hasTime: true, source: "QQ · 年级通知"),
                todo("gym", "预约体测", due: day, hasTime: false, source: "QQ · 体育部"),
            ]
            let generated = now.addingTimeInterval(-4 * 60)

            func snapshot(
                todos: [WatchSnapshot.Item], courses today: WatchSnapshot.Courses? = tuesday,
                online: Bool = true, state: MascotState = .idle, generated stamp: Date? = nil
            ) -> WatchSnapshot {
                WatchSnapshot(
                    generatedAt: stamp ?? generated, online: online, state: state, summary: "", todos: todos,
                    courses: today, tomorrowCourses: wednesday, health: health, weather: weather)
            }

            switch self {
            case .inClass, .iphoneAway:
                return snapshot(todos: Array(todos.prefix(2)) + [todos[3]])
            case .soon:
                return snapshot(todos: [todos[1], todos[3]])
            case .deadline:
                return snapshot(todos: [todo("homework", "交数据结构第三次作业", due: at(17), hasTime: true, source: "QQ · 计算机2班 · 李老师"), todos[1]])
            case .overdue:
                return snapshot(todos: [todos[2], todos[3]])
            case .morning:
                return snapshot(todos: todos)
            case .classesOver:
                return snapshot(todos: [])
            case .allDone:
                return snapshot(
                    todos: todos.map { todo($0.id, $0.title, due: $0.dueAt, hasTime: $0.hasTime == true, source: $0.source, done: true) },
                    courses: courses("今天的课", []))
            case .free:
                return snapshot(todos: [], courses: courses("今天的课", []))
            case .todo:
                return snapshot(
                    todos: [todo("gym", "预约体测，选周四下午的场次", due: day, hasTime: false, source: "QQ · 体育部")],
                    courses: courses("今天的课", []))
            case .macOffline:
                return snapshot(
                    todos: todos, online: false, state: .offline, generated: now.addingTimeInterval(-52 * 60))
            case .stale:
                return snapshot(
                    todos: todos, online: true, state: .idle,
                    generated: calendar.date(byAdding: .day, value: -2, to: now))
            case .long:
                return snapshot(
                    todos: [
                        todo("long1", "在学工系统完成 2026 年秋季学期学生信息核对并把确认截图发给辅导员老师", due: at(12), hasTime: true),
                        todo("long2", "把团员证和团费收据一起交给团支书，顺便确认下周团日活动的时间", due: day, hasTime: false),
                        todo("long3", "预约体测", due: at(21), hasTime: true),
                    ],
                    courses: courses(
                        "今天的课",
                        [
                            ("习近平新时代中国特色社会主义思想概论", "09:55–12:20", nil),
                            ("大学生职业发展与就业指导（A）", "14:00–15:35", nil),
                        ]))
            case .noReadiness:
                var unknown = snapshot(todos: todos)
                unknown.health = .init(
                    readiness: .init(value: "数据不足", label: "至少要 3 项有数据 · 夜间 HRV 昨晚没有 HRV"),
                    sleep: .init(value: "没有记录", label: "手表没戴，或者数据还没从 iPhone 上传"), mascot: lily)
                return unknown
            case .liveClass:
                let span = { (from: Int, to: Int) -> String in
                    DueText.format(now.addingTimeInterval(Double(from) * 60), "HH:mm", calendar) + "–"
                        + DueText.format(now.addingTimeInterval(Double(to) * 60), "HH:mm", calendar)
                }
                return snapshot(
                    todos: [todos[0], todos[1]],
                    courses: courses("今天的课", [("高等数学I（A）", span(-30, 65), nil), ("数据结构", span(90, 185), nil)]))
            case .noData:
                return nil
            }
        }
    }
#endif
