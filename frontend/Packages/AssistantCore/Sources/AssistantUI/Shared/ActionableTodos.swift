#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// The same interactive rows in a card and in chat; actions always use the store's newest copy.
    struct ActionableTodos: View {
        @EnvironmentObject private var store: AppStore
        let todos: [Todo]
        @State private var editing: Todo?
        @State private var deleting: Todo?

        var body: some View {
            VStack(spacing: 0) {
                ForEach(todos) { original in
                    let todo = store.todosByID[original.id] ?? original
                    if todo.status != .deleted && todo.status != .cancelled {
                        TodoLine(
                            todo: todo, toggle: { Task { await store.toggleDone(todo) } },
                            edit: { editing = todo },
                            delete: {
                                if ManualEntryText.needsDeleteReason(todo) {
                                    deleting = todo
                                } else {
                                    Task { await store.delete(todo) }
                                }
                            })
                        if todo.id != todos.last?.id {
                            Palette.hairline.frame(height: 1).padding(.leading, TodoLine.textInset)
                        }
                    }
                }
            }
            .sheet(item: $editing) { todo in
                TodoEditor(todo: todo) { patch in try await store.saveEdits(todo, patch) }
            }
            .confirmationDialog(
                "为什么删除这条待办？",
                isPresented: Binding(
                    get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible,
                presenting: deleting
            ) { todo in
                ForEach(TodoDeleteReason.allCases, id: \.self) { reason in
                    Button(reason.title, role: .destructive) {
                        Task { await store.delete(todo, reason: reason) }
                    }
                }
                Button("直接删除", role: .destructive) { Task { await store.delete(todo) } }
                Button("取消", role: .cancel) {}
            } message: { _ in
                Text("提醒事项里对应的条目也会一起删除。")
            }
        }
    }
#endif
