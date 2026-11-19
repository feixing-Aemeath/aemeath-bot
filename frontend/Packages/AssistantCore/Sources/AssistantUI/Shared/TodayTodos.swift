#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// The assistant's first card: today's todos. Each todo shows where it came from (which
    /// chat, who sent it), its deadline and whether it reached Reminders; it can be completed,
    /// edited and deleted. On 仪表盘 what she could not settle (`unsure`) is under them.
    struct TodayTodosCard: View {
        @EnvironmentObject private var store: AppStore
        @State private var adding = false
        /// Whether the todos that are not today's show under them; remembered on this device.
        @AppStorage("assistant.todos.later") private var showsLater = false
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            let todos = store.today?.todos ?? []
            let later = DashboardEdit.later(store.todos, today: todos)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline) {
                    CardTitle("今日待办", icon: "checklist")
                    Spacer(minLength: 8)
                    if store.remainingToday > 0 {
                        Text("还有 \(store.remainingToday) 件").font(CardFont.note).foregroundStyle(.secondary)
                    }
                    Button { adding = true } label: {
                        Image(systemName: "plus").frame(minWidth: 32, minHeight: 32).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain).accessibilityLabel("新建待办或贴一段消息").help("添加待办或消息")
                }
                if store.today == nil {
                    if store.online { TodoSkeleton() }
                    else { Text("连不上 Mac，还没读到今天的待办。").font(CardFont.body).foregroundStyle(.secondary) }
                } else if todos.isEmpty {
                    Text("今天没有要做的事。").font(CardFont.body).foregroundStyle(.secondary).padding(.vertical, 6)
                } else { ActionableTodos(todos: todos) }
                if !store.visibleUnsure.isEmpty { UnsureRow().padding(.top, 6) }
                // Open todos with a later deadline or none: one line until opened.
                if !later.isEmpty {
                    Button {
                        withAnimation(reduceMotion ? nil : .snappy) { showsLater.toggle() }
                    } label: {
                        HStack(spacing: 5) {
                            Text(showsLater ? "之后的 \(later.count) 件" : "之后还有 \(later.count) 件")
                            Image(systemName: showsLater ? "chevron.up" : "chevron.down")
                                .font(.caption2.weight(.semibold))
                            Spacer(minLength: 0)
                        }
                        .font(CardFont.note.weight(.medium))
                        .foregroundStyle(.secondary)
                        .padding(.top, 8)
                        .padding(.bottom, showsLater ? 0 : 2)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(showsLater ? "收起之后的待办" : "之后还有 \(later.count) 件待办，展开")
                    if showsLater { ActionableTodos(todos: later) }
                }
            }
            .padding(CardSurface.padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
            // A todo just written may not be today's: open the list it went to.
            .sheet(isPresented: $adding) { ManualEntryComposer(created: { showsLater = true }) }
        }
    }

    /// One todo: a circle that ticks it, the title, then one line with the deadline (red when
    /// overdue) and where it came from; a small icon only when it has not reached Reminders.
    /// Edit and delete: a tap or a long press on the iPhone, buttons on hover on the Mac.
    struct TodoLine: View {
        let todo: Todo
        let toggle: () -> Void
        let edit: () -> Void
        let delete: () -> Void
        @State private var hovering = false

        /// Where the title starts: past the circle and the gap after it.
        static let textInset: CGFloat = 36

        var done: Bool { todo.status == .completed }

        var overdue: Bool {
            todo.status == .pending
                && DueText.isOverdue(todo, now: Date(), calendar: DueText.calendar())
        }

        static var titleFont: Font {
            #if os(iOS)
                .callout.weight(.medium)
            #else
                .body.weight(.medium)
            #endif
        }

        var body: some View {
            HStack(alignment: .top, spacing: 10) {
                Button(action: toggle) {
                    Image(systemName: done ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 20, weight: .regular))
                        .foregroundStyle(done ? Color.green : overdue ? Color.red.opacity(0.75) : Color.secondary.opacity(0.7))
                        .frame(width: 26, height: 22)
                        // A finger-sized target around the small circle.
                        .padding(8)
                        .contentShape(Rectangle())
                        .padding(-8)
                }
                .buttonStyle(.plain)
                .help(done ? "标记为未完成" : "完成")
                .accessibilityLabel(done ? "标记为未完成" : "完成")

                VStack(alignment: .leading, spacing: 3) {
                    Text(todo.title)
                        .font(Self.titleFont)
                        .strikethrough(done)
                        .foregroundStyle(done ? Color.secondary : Color.primary)
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        TodoMeta(todo: todo, overdue: overdue)
                            .help(todo.sourceExcerpt)
                        SyncMark(state: SyncState(todo))
                    }
                    if !todo.notes.isEmpty {
                        Text(todo.notes).font(CardFont.note).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture(perform: edit)
                .accessibilityAction(named: "编辑待办", edit)
                Spacer(minLength: 0)
                #if os(macOS)
                    HStack(spacing: 4) {
                        Button(action: edit) { Image(systemName: "pencil") }
                            .help("编辑").accessibilityLabel("编辑待办")
                        Button(action: delete) { Image(systemName: "trash") }
                            .help("删除").accessibilityLabel("删除待办")
                    }
                    .buttonStyle(.borderless)
                    .opacity(hovering ? 1 : 0)
                #endif
            }
            .padding(.vertical, 10)
            .contentShape(Rectangle())
            .onHover { hovering = $0 }
            .contextMenu {
                Button(done ? "标记为未完成" : "完成", action: toggle)
                Button("编辑…", action: edit)
                Divider()
                Button("删除", role: .destructive, action: delete)
            }
        }
    }

    /// Three gray rows in the shape of todos while today's are on their way.
    struct TodoSkeleton: View {
        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(0..<3, id: \.self) { index in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "circle").font(.system(size: 20)).frame(width: 26, height: 22)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(index == 1 ? "交一份要提交的材料" : "一条待办的标题在这里").font(TodoLine.titleFont)
                            Text("今天 17:00 · 来自一个群").font(CardFont.note)
                        }
                    }
                    .padding(.vertical, 10)
                }
            }
            .redacted(reason: .placeholder)
            .foregroundStyle(.secondary)
            .accessibilityLabel("正在读取今天的待办")
        }
    }

    /// 「已逾期 · 10月14日 · 宿舍楼通知群」: the deadline, red when it has passed, then the source.
    struct TodoMeta: View {
        let todo: Todo
        let overdue: Bool

        var body: some View {
            (Text(DueText.describe(todo)).foregroundStyle(overdue ? Color.red : Color.secondary)
                + Text(" · " + TodoSourceText.describe(todo)).foregroundStyle(Color.secondary))
                .font(CardFont.note)
                .lineLimit(2)
        }
    }

    /// Nothing once a todo is in Reminders, the normal case; a small icon while it waits, fails
    /// or stays in the App.
    struct SyncMark: View {
        let state: SyncState

        var body: some View {
            if case .synced = state {
                EmptyView()
            } else {
                SyncLabel(state: state)
                    .labelStyle(.iconOnly)
                    .font(CardFont.note)
                    .help(state.text)
                    .accessibilityLabel(state.text)
            }
        }
    }

#endif
