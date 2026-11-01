#if os(watchOS)
    import AssistantCore
    import SwiftUI

    /// The watch App: a page each, swiped up and down or turned with the Digital Crown —
    /// 助理Bot's todos, today's courses (课表, then tomorrow's), 健康Bot's readiness and last
    /// night's sleep.
    /// All of it comes from the iPhone (WatchSync); a page whose plugin sent nothing says so.
    public struct WatchRootView: View {
        @EnvironmentObject private var store: WatchStore
        @State private var page = Self.firstPage

        enum Page: String {
            case todos, courses, health
        }

        public init() {}

        /// The todos; in a development build `-page courses` (or `health`) at launch opens that
        /// page, for screenshots in the simulator.
        static var firstPage: Page {
            #if DEBUG
                if let name = UserDefaults.standard.string(forKey: "page"), let page = Page(rawValue: name) {
                    return page
                }
            #endif
            return .todos
        }

        public var body: some View {
            TabView(selection: $page) {
                NavigationStack { WatchTodayView() }.tag(Page.todos)
                NavigationStack { WatchCoursesView() }.tag(Page.courses)
                NavigationStack { WatchHealthView() }.tag(Page.health)
            }
            .tabViewStyle(.verticalPage)
            .task { store.start() }
        }
    }

    extension View {
        /// A page of the watch App: a wash of its Bot's color behind it, and no big title over
        /// the Bot's header (on the 49 mm watch the two took half the first screen).
        func watchPage(_ look: StickerLook) -> some View {
            containerBackground(look.accent.color.opacity(0.4).gradient, for: .tabView)
        }
    }

    extension StickerLook {
        /// The Bot's color for small text and marks on the watch's black screen.
        var ink: Color { accent.onBlack.color }
    }

    /// The top of a page: the Bot's face in a circle of its color, its name and a line.
    struct WatchBotHeader: View {
        let look: StickerLook
        let name: String
        let detail: String
        var state: MascotState = .idle

        var body: some View {
            HStack(spacing: 8) {
                MascotView(look: look, state: state, liveliness: .subtle)
                    .frame(width: 40, height: 40)
                    .background(Circle().fill(look.accent.color.opacity(0.22)))
                    .clipShape(Circle())
                VStack(alignment: .leading, spacing: 0) {
                    Text(name).font(.headline).lineLimit(1)
                    Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            .listRowBackground(Color.clear)
        }
    }

    extension StickerLook {
        /// The Bot's name: its character's for a sticker (characters.json), else `fallback`.
        func name(or fallback: String) -> String {
            sticker.flatMap { StickerCatalog.main.characters[$0] } ?? fallback
        }
    }

    /// 课表: today's courses, the one going on or next marked, past ones dimmed; once the last
    /// one is over (or on a day without any), tomorrow's.
    struct WatchCoursesView: View {
        @EnvironmentObject private var store: WatchStore

        var body: some View {
            let look = StickerLook(mascot: (store.snapshot?.courses ?? store.snapshot?.tomorrowCourses)?.mascot)
            // Redrawn every minute, so the course going on moves along and tomorrow's come in.
            TimelineView(.everyMinute) { context in
                let page = store.snapshot?.coursePage(at: context.date, calendar: DueText.calendar())
                let minute = minutes(context.date)
                List {
                    WatchBotHeader(look: look, name: look.name(or: "课表"), detail: detail(page))
                    if let page {
                        if !page.isToday {
                            Text("明天的课" + (page.courses.estimated ? " · 按上周推测" : ""))
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(look.ink)
                                .listRowBackground(Color.clear)
                        }
                        if page.courses.items.isEmpty {
                            Text(page.isToday ? "今天没有课" : "明天没有课").font(.footnote)
                        }
                        let current = page.isToday ? page.courses.current(atMinute: minute) : nil
                        ForEach(Array(page.courses.items.enumerated()), id: \.offset) { index, course in
                            row(
                                course, accent: look.ink,
                                mark: page.isToday ? mark(index, course, current, minute) : nil
                            )
                            .opacity(page.isToday && (current.map { index < $0 } ?? true) ? 0.45 : 1)
                        }
                    } else {
                        Text(
                            store.snapshot?.courses == nil
                                ? "Mac 上的课表插件读到学校网站的课表后，今天的课会出现在这里。"
                                : "iPhone 还没送来今天的课表，打开 iPhone 上的 App 就会更新。"
                        )
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    }
                }
            }
            .watchPage(look)
        }

        func detail(_ page: WatchSnapshot.CoursePage?) -> String {
            guard let page else { return "还没读到课表" }
            if page.isToday { return page.courses.estimated ? "按上周的课表推测" : "从学校网站读到" }
            return page.hadCoursesToday ? "今天的课上完了" : "今天没有课"
        }

        func minutes(_ date: Date) -> Int {
            let parts = DueText.calendar().dateComponents([.hour, .minute], from: date)
            return (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        }

        func mark(_ index: Int, _ course: WatchSnapshot.Courses.Course, _ current: Int?, _ minute: Int)
            -> String?
        {
            guard index == current else { return nil }
            return (course.minutes?.start ?? Int.max) <= minute ? "正在上" : "下一节"
        }

        func row(_ course: WatchSnapshot.Courses.Course, accent: Color, mark: String?) -> some View {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(course.time).font(.caption2.monospacedDigit()).foregroundStyle(accent)
                    if let mark {
                        Text(mark)
                            .font(.caption2.weight(.semibold))
                            .padding(.horizontal, 5)
                            .background(Capsule().fill(accent.opacity(0.3)))
                    }
                }
                Text(course.title).font(.footnote).lineLimit(3)
            }
        }
    }

    /// 健康Bot: today's readiness on a ring out of 10, and last night's sleep.
    struct WatchHealthView: View {
        @EnvironmentObject private var store: WatchStore

        var body: some View {
            let health = store.snapshot?.health
            let look = StickerLook(mascot: health?.mascot)
            List {
                WatchBotHeader(
                    look: look, name: look.name(or: "健康"),
                    detail: health == nil ? "还没有健康数据" : "今天的身体状态")
                if let readiness = health?.readiness {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 10) {
                            ring(readiness, accent: look.ink)
                            VStack(alignment: .leading, spacing: 1) {
                                Text("今日准备度").font(.footnote.weight(.semibold))
                                if let delta = readiness.delta {
                                    Text(delta).font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                        }
                        if let label = readiness.label {
                            Text(label).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
                if let sleep = health?.sleep {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("昨晚睡眠").font(.footnote.weight(.semibold))
                        Text([sleep.value, sleep.unit].compactMap { $0 }.joined(separator: " "))
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(look.ink)
                        if let label = sleep.label {
                            Text(label).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
                if health == nil {
                    Text("iPhone 上传健康数据、Mac 算出准备度后会出现在这里。")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .watchPage(look)
        }

        /// A ring out of 10 when the readiness is a number; the words otherwise (数据不足).
        @ViewBuilder func ring(_ readiness: WatchSnapshot.Health.Stat, accent: Color) -> some View {
            if let value = Double(readiness.value) {
                Gauge(value: min(max(value, 0), 10), in: 0...10) {
                    EmptyView()
                } currentValueLabel: {
                    Text(readiness.value).font(.title3.weight(.bold))
                }
                .gaugeStyle(.accessoryCircularCapacity)
                .tint(accent)
            } else {
                Text(readiness.value).font(.headline)
            }
        }
    }
#endif
