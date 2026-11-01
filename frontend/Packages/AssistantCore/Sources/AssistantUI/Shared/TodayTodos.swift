#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// The assistant's first card: today's todos. Each todo shows where it came from (which
    /// chat, who sent it), its deadline and whether it reached Reminders; it can be completed,
    /// edited and deleted.
    struct TodayTodosCard: View {
        @EnvironmentObject private var store: AppStore
        @State private var editing: Todo?
        @State private var deleting: Todo?

        var title: String {
            store.today == nil ? "今日待办" : "今日待办（还有 \(store.remainingToday) 件）"
        }

        var body: some View {
            let todos = store.today?.todos ?? []
            VStack(alignment: .leading, spacing: 8) {
                CardTitle(title, icon: "checklist")
                if todos.isEmpty {
                    Text("今天没有要做的事。").foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 0) {
                        ForEach(todos) { todo in
                            TodoLine(
                                todo: todo,
                                toggle: { Task { await store.toggleDone(todo) } },
                                edit: { editing = todo },
                                delete: { deleting = todo })
                            if todo.id != todos.last?.id {
                                Divider()
                            }
                        }
                    }
                    .background(RoundedRectangle(cornerRadius: 8).fill(Palette.page))
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
            .sheet(item: $editing) { todo in
                TodoEditor(todo: todo) { patch in
                    Task { await store.update(todo, patch) }
                }
            }
            .confirmationDialog(
                "删除这条待办？",
                isPresented: Binding(
                    get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                titleVisibility: .visible,
                presenting: deleting
            ) { todo in
                Button("删除", role: .destructive) {
                    Task { await store.delete(todo) }
                }
                Button("取消", role: .cancel) {}
            } message: { _ in
                Text("提醒事项里对应的条目也会一起删除。")
            }
        }
    }

    /// One todo: checkbox, title, deadline, source and sync state, with edit and delete.
    struct TodoLine: View {
        let todo: Todo
        let toggle: () -> Void
        let edit: () -> Void
        let delete: () -> Void
        @State private var hovering = false

        var done: Bool { todo.status == .completed }

        var overdue: Bool {
            todo.status == .pending
                && DueText.isOverdue(todo, now: Date(), calendar: DueText.calendar())
        }

        @ViewBuilder var details: some View {
            Label(DueText.describe(todo), systemImage: "clock")
                .foregroundStyle(overdue ? Color.red : Color.secondary)
            Label(TodoSourceText.describe(todo), systemImage: "bubble.left")
                .foregroundStyle(.secondary)
                .help(todo.sourceExcerpt)
            SyncLabel(state: SyncState(todo))
        }

        var body: some View {
            HStack(alignment: .top, spacing: 12) {
                Button(action: toggle) {
                    Image(systemName: done ? "checkmark.circle.fill" : "circle")
                        .font(.title3)
                        .foregroundStyle(done ? Color.green : Color.secondary)
                }
                .buttonStyle(.plain)
                .help(done ? "标记为未完成" : "完成")

                VStack(alignment: .leading, spacing: 4) {
                    Text(todo.title)
                        .font(.body.weight(.medium))
                        .strikethrough(done)
                        .foregroundStyle(done ? Color.secondary : Color.primary)
                    // One line on the Mac, one fact per line where that does not fit (the iPhone).
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 12) { details }
                        VStack(alignment: .leading, spacing: 3) { details }
                    }
                    .font(.caption)
                    if !todo.notes.isEmpty {
                        Text(todo.notes).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                Spacer()
                #if os(macOS)
                    HStack(spacing: 4) {
                        Button(action: edit) { Image(systemName: "pencil") }
                            .help("编辑")
                        Button(action: delete) { Image(systemName: "trash") }
                            .help("删除")
                    }
                    .buttonStyle(.borderless)
                    .opacity(hovering ? 1 : 0.35)
                #endif
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
            .onHover { hovering = $0 }
            #if os(iOS)
                // No pointer to show the buttons: a tap edits, a long press has the rest.
                .onTapGesture(perform: edit)
            #endif
            .contextMenu {
                Button(done ? "标记为未完成" : "完成", action: toggle)
                Button("编辑…", action: edit)
                Divider()
                Button("删除", role: .destructive, action: delete)
            }
        }
    }

#endif
