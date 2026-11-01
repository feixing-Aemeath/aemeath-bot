#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// Edit a todo's title, notes and deadline. Only changed fields are saved; the change goes
    /// to Reminders through the backend.
    public struct TodoEditor: View {
        let todo: Todo
        let save: (TodoPatch) -> Void
        @State private var draft: TodoDraft
        @Environment(\.dismiss) private var dismiss

        public init(todo: Todo, save: @escaping (TodoPatch) -> Void) {
            self.todo = todo
            self.save = save
            _draft = State(initialValue: TodoDraft(todo))
        }

        public var body: some View {
            VStack(alignment: .leading, spacing: 12) {
                Text("编辑待办").font(.headline)
                Form {
                    TextField("标题", text: $draft.title)
                    TextField("备注", text: $draft.notes, axis: .vertical)
                        .lineLimit(2...5)
                    Toggle("有截止时间", isOn: $draft.hasDue)
                    if draft.hasDue {
                        Toggle("具体到几点", isOn: $draft.hasTime)
                        DatePicker(
                            "截止", selection: $draft.due,
                            displayedComponents: draft.hasTime ? [.date, .hourAndMinute] : [.date])
                    }
                }
                Text("来源：\(TodoSourceText.describe(todo))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if !todo.sourceExcerpt.isEmpty {
                    Text("原文：\(todo.sourceExcerpt)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                }
                HStack {
                    Spacer()
                    Button("取消") { dismiss() }
                    Button("保存") {
                        save(draft.patch(from: todo))
                        dismiss()
                    }
                    .disabled(!draft.isValid)
                    .buttonStyle(.borderedProminent)
                }
            }
            .padding(20)
            .frame(minWidth: 380)
        }
    }
#endif
