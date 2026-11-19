#if os(watchOS)
    import AssistantCore
    import SwiftUI

    /// 待办: everything to tick off, the late ones first. One tap on a row; a double tap ticks
    /// the first one that is still open.
    struct WatchTodosView: View {
        @EnvironmentObject private var store: WatchStore
        let isCurrent: Bool
        private let look = StickerCatalog.main.assistant
        @State private var ticks = 0
        @State private var unticks = 0
        /// 助理 hops when one is ticked off.
        @State private var cue: MascotCue?
        @WatchDimmed private var dimmed

        var ink: Color { look.ink }
        var calendar: Calendar { DueText.calendar() }

        var body: some View {
            TimelineView(.everyMinute) { context in
                ScrollView {
                    content(at: store.now(context.date))
                        .padding(.top, 5)
                        .padding(.horizontal, WatchScale.margin)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .opacity(dimmed ? 0.7 : 1)
                }
                .watchScrollFade()
            }
            .watchKicker("待办", color: ink)
            .watchBackdrop(look.accent.color)
            .sensoryFeedback(.success, trigger: ticks)
            .sensoryFeedback(.impact(weight: .light), trigger: unticks)
        }

        func content(at date: Date) -> some View {
            VStack(alignment: .leading, spacing: 0) {
                if let snapshot = store.snapshot {
                    let items = snapshot.orderedTodos(at: date, calendar: calendar)
                    if snapshot.remaining == 0 {
                        WatchTodosClear(hadAny: !items.isEmpty, look: look)
                    } else {
                        header(snapshot, items: items, date: date)
                    }
                    let firstOpen = items.first { !$0.done }?.id
                    ForEach(items) { item in
                        row(item, snapshot: snapshot, date: date, isFirstOpen: item.id == firstOpen)
                        WatchHairline()
                    }
                }
                footer(date)
            }
        }

        func header(_ snapshot: WatchSnapshot, items: [WatchSnapshot.Item], date: Date) -> some View {
            let late = items.filter { snapshot.isOverdue($0, at: date, calendar: calendar) }.count
            return VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .bottom, spacing: 0) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text("\(snapshot.remaining)")
                            .font(.system(size: WatchScale.of(44), weight: .semibold, design: .rounded))
                            .contentTransition(.numericText(countsDown: true))
                            .animation(.snappy, value: snapshot.remaining)
                        VStack(alignment: .leading, spacing: 0) {
                            if late > 0 {
                                Text("\(late) 件逾期")
                                    .font(.system(size: WatchScale.of(12), weight: .semibold))
                                    .foregroundStyle(.orange)
                            }
                            Text("件还没做")
                                .font(.system(size: WatchScale.of(14), weight: .semibold))
                                .foregroundStyle(ink)
                        }
                    }
                    .padding(.bottom, 2)
                    .layoutPriority(1)
                    Spacer(minLength: 0)
                    if !dimmed {
                        MascotView(
                            look: look, state: store.snapshot?.state ?? .offline, cue: cue, interactive: true,
                            liveliness: isCurrent ? .full : .subtle
                        )
                        .frame(width: WatchScale.of(56), height: WatchScale.of(56))
                    }
                }
                WatchSegments(total: items.count, done: items.filter(\.done).count, tint: ink)
            }
            .padding(.bottom, 2)
        }

        @ViewBuilder func row(_ item: WatchSnapshot.Item, snapshot: WatchSnapshot, date: Date, isFirstOpen: Bool) -> some View {
            let button = Button {
                if item.done {
                    unticks += 1
                } else {
                    ticks += 1
                    cue = MascotCue(.poke)
                }
                withAnimation(.snappy) { store.toggle(item) }
            } label: {
                WatchTodoRow(
                    item: item, due: snapshot.dueWords(of: item, at: date, calendar: calendar),
                    overdue: snapshot.isOverdue(item, at: date, calendar: calendar), ink: ink)
            }
            .buttonStyle(WatchPressStyle())
            .accessibilityValue(item.done ? "已完成" : "未完成")
            if #available(watchOS 11.0, *) {
                button.handGestureShortcut(.primaryAction, isEnabled: isCurrent && isFirstOpen)
            } else {
                button
            }
        }

        func footer(_ date: Date) -> some View {
            VStack(alignment: .leading, spacing: 2) {
                WatchRefreshButton(tint: ink)
                Text(updated(date))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                if !store.reachable {
                    Text("iPhone 不在附近，勾选的会等连上后同步")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if let message = store.message {
                    Text(message)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.top, 10)
            .padding(.bottom, 14)
        }

        func updated(_ date: Date) -> String {
            guard let snapshot = store.snapshot else { return "还没有从 iPhone 收到数据" }
            let when = RelativeTime.describe(snapshot.generatedAt, now: date, calendar: calendar)
            return snapshot.online ? "更新于 \(when)" : "更新于 \(when) · iPhone 连不上 Mac"
        }
    }

    /// Nothing left: the Bot on a full line where the list was.
    struct WatchTodosClear: View {
        let hadAny: Bool
        let look: StickerLook

        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .bottom, spacing: 0) {
                    Text(hadAny ? "都做完了" : "今天没有待办")
                        .font(.system(size: WatchScale.of(25), weight: .bold))
                        .padding(.bottom, 6)
                        .minimumScaleFactor(0.8)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    MascotView(look: look, state: hadAny ? .done : .idle, liveliness: .subtle)
                        .frame(width: WatchScale.of(64), height: WatchScale.of(64))
                }
                WatchProgressLine(value: hadAny ? 1 : nil, tint: look.ink)
            }
            .padding(.bottom, 2)
        }
    }

    struct WatchTodoRow: View {
        let item: WatchSnapshot.Item
        let due: String
        let overdue: Bool
        let ink: Color

        var body: some View {
            HStack(alignment: .top, spacing: 10) {
                WatchCheck(done: item.done, tint: overdue ? .orange : ink)
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.title)
                        .font(.footnote.weight(.medium))
                        .strikethrough(item.done, color: .white.opacity(0.5))
                        .foregroundStyle(item.done ? Color.secondary : Color.primary)
                        .lineLimit(3)
                        .privacySensitive()
                    Text(due)
                        .font(.caption2)
                        .foregroundStyle(overdue ? Color.orange : Color.secondary)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
    }

    /// The round check of a todo: a ring that fills.
    struct WatchCheck: View {
        let done: Bool
        let tint: Color

        var body: some View {
            ZStack {
                Circle().stroke(tint, lineWidth: 2).opacity(done ? 0 : 1)
                if done {
                    Circle().fill(tint)
                    Image(systemName: "checkmark")
                        .font(.system(size: WatchScale.of(13), weight: .heavy))
                        .foregroundStyle(.black)
                        .transition(.scale(scale: 0.4).combined(with: .opacity))
                }
            }
            .frame(width: WatchScale.of(25), height: WatchScale.of(25))
            .animation(.bouncy(duration: 0.3), value: done)
        }
    }

    /// 「让 iPhone 刷新」: a plain line among lines, with the spinner while the iPhone answers.
    struct WatchRefreshButton: View {
        @EnvironmentObject private var store: WatchStore
        let tint: Color

        var body: some View {
            Button {
                store.refresh()
            } label: {
                HStack(spacing: 5) {
                    if store.waiting {
                        ProgressView().controlSize(.mini).frame(width: 16, height: 16)
                    } else {
                        Image(systemName: "arrow.clockwise").font(.footnote.weight(.semibold))
                    }
                    Text("让 iPhone 刷新").font(.footnote.weight(.semibold))
                }
                .foregroundStyle(tint)
            }
            .buttonStyle(.plain)
            .disabled(store.waiting)
        }
    }
#endif
