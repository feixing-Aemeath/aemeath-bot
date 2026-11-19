#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// Edit a todo's title, notes and deadline. Only changed fields are saved; the change goes
    /// to Reminders through the backend.
    public struct TodoEditor: View {
        let todo: Todo
        let save: (TodoPatch) async throws -> Void
        @State private var saving = false
        @State private var errorText: String?
        @State private var draft: TodoDraft
        @Environment(\.dismiss) private var dismiss

        public init(todo: Todo, save: @escaping (TodoPatch) async throws -> Void) {
            self.todo = todo
            self.save = save
            _draft = State(initialValue: TodoDraft(todo))
        }

        public var body: some View {
            editor
                .disabled(saving)
                .interactiveDismissDisabled(saving)
                .alert("没能保存待办", isPresented: Binding(
                    get: { errorText != nil }, set: { if !$0 { errorText = nil } })
                ) { Button("好") { errorText = nil } } message: { Text(errorText ?? "") }
        }

        private var editor: some View {
            #if os(iOS)
                // A native sheet: title in the bar, 取消 and 保存 at its ends, half the screen first.
                NavigationStack {
                    Form {
                        Section {
                            TextField("标题", text: $draft.title)
                            TextField("备注", text: $draft.notes, axis: .vertical)
                                .lineLimit(2...5)
                        }
                        .listRowBackground(Palette.card)
                        Section {
                            Toggle("有截止时间", isOn: $draft.hasDue)
                            if draft.hasDue {
                                Toggle("具体到几点", isOn: $draft.hasTime)
                                DatePicker(
                                    "截止", selection: $draft.due,
                                    displayedComponents: draft.hasTime ? [.date, .hourAndMinute] : [.date])
                            }
                        }
                        .listRowBackground(Palette.card)
                        Section { TodoOriginalTexts(todo: todo) }
                        .listRowBackground(Palette.card)
                    }
                    .scrollContentBackground(.hidden)
                    .background(Palette.page)
                    .navigationTitle("编辑待办")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("取消") { dismiss() }
                        }
                        ToolbarItem(placement: .confirmationAction) {
                            saveButton
                                .fontWeight(.semibold)
                                .disabled(!draft.isValid)
                        }
                    }
                }
                .presentationDetents([.medium, .large])
                .presentationBackground(Palette.page)
            #else
                VStack(alignment: .leading, spacing: 12) {
                    Text("编辑待办").font(Typography.serif(17))
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
                    ScrollView { TodoOriginalTexts(todo: todo) }.frame(maxHeight: 260)
                    HStack {
                        Spacer()
                        Button("取消") { dismiss() }
                            .keyboardShortcut(.cancelAction)
                        saveButton
                            .keyboardShortcut(.defaultAction)
                            .disabled(!draft.isValid)
                            .buttonStyle(.borderedProminent)
                    }
                }
                .padding(20)
                .frame(minWidth: 400)
            #endif
        }

        private var saveButton: some View {
            Button(saving ? "正在保存…" : "保存") {
                guard !saving else { return }
                saving = true
                let patch = draft.patch(from: todo)
                Task { @MainActor in
                    defer { saving = false }
                    do {
                        try await save(patch)
                        dismiss()
                    } catch {
                        errorText = AppStore.describe(error)
                    }
                }
            }
        }
    }

    /// Where a todo came from, and the message it was read from as a quote with a bar beside it.
    struct TodoSourceQuote: View {
        let todo: Todo

        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                Label(TodoSourceText.describe(todo), systemImage: "bubble.left")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                if !todo.sourceExcerpt.isEmpty {
                    Text(todo.sourceExcerpt)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .lineLimit(6)
                        .selectableText()
                        .padding(.leading, 10)
                        .overlay(alignment: .leading) {
                            Capsule().fill(Palette.trail).frame(width: 2)
                        }
                }
            }
            .padding(.vertical, 2)
        }
    }
#endif
