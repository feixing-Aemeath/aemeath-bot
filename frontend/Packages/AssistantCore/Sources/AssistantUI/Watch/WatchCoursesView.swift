#if os(watchOS)
    import AssistantCore
    import SwiftUI

    /// 课表: the day as a line. What is over goes dim, what is going on has the Bot on it and the
    /// time left; once the last class is over, tomorrow's (WatchSnapshot.coursePage).
    struct WatchCoursesView: View {
        @EnvironmentObject private var store: WatchStore
        @WatchDimmed private var dimmed

        var look: StickerLook { .watch(.courses, in: store.snapshot) }
        var calendar: Calendar { DueText.calendar() }

        var body: some View {
            // Redrawn every minute, so the course going on moves along and tomorrow's come in.
            TimelineView(.everyMinute) { context in
                let date = store.now(context.date)
                let page = store.snapshot?.coursePage(at: date, calendar: calendar)
                let slots = store.snapshot?.classes(at: date, calendar: calendar) ?? []
                let current = page?.isToday == true ? slots.first { $0.end > date } : nil
                ScrollViewReader { reader in
                    ScrollView {
                        content(page, slots: slots, current: current, date: date)
                            .padding(.top, 5)
                            .padding(.horizontal, WatchScale.margin)
                            .padding(.bottom, 12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .opacity(dimmed ? 0.7 : 1)
                    }
                    .watchScrollFade()
                    .onAppear {
                        // The class that matters on top, what is over above it within reach.
                        if let current, let index = slots.firstIndex(of: current), index > 0 {
                            // A little past .top: the row above should be gone, not fading under the time.
                            reader.scrollTo(index, anchor: UnitPoint(x: 0.5, y: -0.1))
                        }
                    }
                }
                .watchKicker(kicker(page), color: look.ink, look: current == nil ? look : nil)
            }
            .watchBackdrop(look.accent.color)
        }

        func kicker(_ page: WatchSnapshot.CoursePage?) -> String {
            guard let page else { return "课表" }
            return page.isToday ? "今天的课" : "明天的课"
        }

        @ViewBuilder
        func content(_ page: WatchSnapshot.CoursePage?, slots: [WatchClass], current: WatchClass?, date: Date) -> some View {
            VStack(alignment: .leading, spacing: 0) {
                if let page {
                    intro(page)
                    if page.courses.items.isEmpty {
                        Text(page.isToday ? "今天没有课" : "明天没有课")
                            .font(.system(size: WatchScale.of(25), weight: .bold))
                            .padding(.top, 2)
                    }
                    ForEach(Array(slots.enumerated()), id: \.offset) { index, slot in
                        WatchClassRow(
                            slot: slot, date: date, isLast: index == slots.count - 1, isCurrent: slot == current,
                            look: look
                        )
                        .id(index)
                    }
                } else {
                    Text(
                        store.snapshot?.courses == nil
                            ? "Mac 上的课表插件读到学校网站的课表后，今天的课会出现在这里。"
                            : "iPhone 还没送来今天的课表，打开 iPhone 上的 App 就会更新。"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
            }
        }

        @ViewBuilder func intro(_ page: WatchSnapshot.CoursePage) -> some View {
            let note = [
                page.isToday ? nil : (page.hadCoursesToday ? "今天的课上完了" : "今天没有课"),
                page.courses.estimated ? "按上周推测" : nil,
            ].compactMap { $0 }.joined(separator: " · ")
            if !note.isEmpty {
                Text(note)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
                    .padding(.bottom, 6)
            }
        }
    }

    /// A class on the day's rail. The one that matters now gets the room: bigger, how long, and
    /// the Bot on the line that runs to its end.
    struct WatchClassRow: View {
        let slot: WatchClass
        let date: Date
        let isLast: Bool
        let isCurrent: Bool
        let look: StickerLook
        @WatchDimmed private var dimmed

        var over: Bool { slot.isToday && slot.end <= date }
        var running: Bool { slot.isRunning(at: date) }
        var calendar: Calendar { DueText.calendar() }

        var body: some View {
            HStack(alignment: .top, spacing: 8) {
                rail
                VStack(alignment: .leading, spacing: 1) {
                    Text(DueText.format(slot.start, "HH:mm", calendar) + "–" + DueText.format(slot.end, "HH:mm", calendar))
                        .font(.caption2.weight(.medium).monospacedDigit())
                        .foregroundStyle(isCurrent ? look.ink : Color.secondary)
                    Text(slot.title)
                        .font(isCurrent ? .system(size: WatchScale.of(19), weight: .bold) : .footnote.weight(.medium))
                        .lineLimit(3)
                    if isCurrent { progress }
                }
                .padding(.bottom, isLast ? 2 : WatchScale.of(14))
                .opacity(over ? 0.4 : 1)
            }
            .accessibilityElement(children: .combine)
        }

        var progress: some View {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .bottom, spacing: 0) {
                    let target = running ? slot.end : slot.start
                    let left = WatchFigures.until(target, from: date).text
                    Text(running ? "还有 \(left)" : "\(left)后开始")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(look.ink)
                        .padding(.bottom, 4)
                    Spacer(minLength: 0)
                    if !dimmed {
                        MascotView(look: look, state: .idle, interactive: true, liveliness: .subtle)
                            .frame(width: WatchScale.of(44), height: WatchScale.of(44))
                    }
                }
                WatchProgressLine(value: running ? slot.progress(at: date) : nil, tint: look.ink, height: 3)
            }
            .padding(.top, 2)
        }

        var rail: some View {
            VStack(spacing: 0) {
                ZStack {
                    if isCurrent {
                        Circle().fill(look.ink.opacity(0.28)).frame(width: 20, height: 20)
                        Circle().fill(look.ink).frame(width: 10, height: 10)
                    } else if over {
                        Circle().fill(.white.opacity(0.35)).frame(width: 8, height: 8)
                    } else {
                        Circle().stroke(look.ink, lineWidth: 2).frame(width: 9, height: 9)
                    }
                }
                .frame(height: 20)
                if !isLast {
                    Capsule()
                        .fill(over || running ? look.ink.opacity(0.5) : .white.opacity(0.18))
                        .frame(width: 2)
                }
            }
            .frame(width: 20)
        }
    }
#endif
