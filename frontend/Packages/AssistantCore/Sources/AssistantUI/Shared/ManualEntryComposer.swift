#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// A shared fallback entry: either record a todo directly or hand a message to the assistant.
    struct ManualEntryComposer: View {
        @EnvironmentObject private var store: AppStore
        @Environment(\.dismiss) private var dismiss
        @State private var title: String
        @State private var notes = ""
        @State private var hasDue = false
        @State private var due = Date()
        @State private var hasTime = false
        @State private var missed = false
        @State private var source: MissedSource?
        @State private var pasted = ""
        @State private var note = ""
        @State private var mode = 0
        @State private var saving = false
        @State private var errorText: String?
        @State private var result: TodoCreated?
        @State private var sent = false

        private var assistantName: String { store.bots.first(where: \.isAssistant).map(store.displayName) ?? Bot.assistantName }

        /// Called when a new todo was saved, before the sheet closes.
        var created: (() -> Void)?

        init(title: String = "", created: (() -> Void)? = nil) {
            _title = State(initialValue: title)
            self.created = created
        }

        var request: TodoNew {
            TodoNew(
                title: title.trimmingCharacters(in: .whitespacesAndNewlines), notes: notes,
                dueAt: hasDue ? (hasTime ? due : store.calendar.startOfDay(for: due)) : nil,
                hasTime: hasDue && hasTime, missed: missed, missedSource: source)
        }
        var inbox: InboxItem {
            InboxItem(
                text: pasted.trimmingCharacters(in: .whitespacesAndNewlines), note: note.isEmpty ? nil : note,
                sourceApp: source.map { Names.plugin($0.plugin) }, chatName: source?.chatName, missed: missed)
        }

        var body: some View {
            Group {
                if let result {
                    VStack(spacing: 0) {
                        // Only a todo that was already there stays on the sheet, to be seen
                        // and changed; a new one closes it and shows on the card.
                        Text("已经有这条了")
                            .font(CardFont.body).padding(.top, 22).padding(.bottom, 8)
                            .accessibilityAddTraits(.isHeader)
                        TodoEditor(todo: result.todo) { patch in
                            try await store.saveEdits(result.todo, patch)
                        }
                    }
                } else {
                    NavigationStack {
                        Form {
                            Section {
                                Picker("添加方式", selection: $mode) {
                                    Text("新建待办").tag(0)
                                    Text("贴一段消息").tag(1)
                                }
                                .pickerStyle(.segmented)
                                .accessibilityLabel("添加方式")
                            }
                            if mode == 0 {
                                Section {
                                    TextField("标题", text: $title).accessibilityLabel("待办标题")
                                    TextField("备注", text: $notes, axis: .vertical).lineLimit(2...5)
                                    Toggle("有截止时间", isOn: $hasDue)
                                    if hasDue {
                                        Toggle("具体到几点", isOn: $hasTime)
                                        DatePicker(
                                            "截止", selection: $due,
                                            displayedComponents: hasTime ? [.date, .hourAndMinute] : [.date])
                                    }
                                }
                            } else {
                                Section("消息原文") {
                                    TextEditor(text: $pasted).frame(minHeight: 150).accessibilityLabel(
                                        "粘贴消息原文，最多八千字")
                                    #if os(iOS)
                                        PasteButton(payloadType: String.self) { strings in
                                            if let text = strings.first { pasted = text }
                                        }
                                        .accessibilityLabel("从剪贴板粘贴消息")
                                    #endif
                                    Text("\(pasted.unicodeScalars.count) / \(ShareContent.textLimit) 字").font(CardFont.note).foregroundStyle(
                                        .secondary)
                                    TextField("加一句备注（可不填）", text: $note, axis: .vertical).lineLimit(1...3)
                                }
                            }
                            Section {
                                Toggle("这是助手漏掉的", isOn: $missed)
                                if missed || mode == 1 {
                                    Picker("来自哪个会话", selection: $source) {
                                        Text("不指定").tag(Optional<MissedSource>.none)
                                        ForEach(ManualEntryText.conversations(store.plugins), id: \.self) {
                                            item in
                                            Text(Names.plugin(item.plugin) + " · " + item.chatName).tag(
                                                Optional(item))
                                        }
                                    }
                                    .accessibilityLabel("选择消息来源和会话，可不选")
                                }
                            }
                            if let errorText {
                                Section {
                                    Text(errorText).foregroundStyle(.orange).accessibilityLabel(errorText)
                                }
                            }
                            if sent {
                                Section {
                                    Text("已经转给\(assistantName)，可在她的聊天里查看；处理期间会显示正在忙。")
                                    Button("完成") { dismiss() }
                                }
                            }
                        }
                        .scrollContentBackground(.hidden)
                        .background(Palette.page)
                        .navigationTitle("添加")
                        #if os(iOS)
                            .navigationBarTitleDisplayMode(.inline)
                        #endif
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) {
                                Button("取消") { dismiss() }.disabled(saving)
                            }
                            ToolbarItem(placement: .confirmationAction) {
                                Button(saving ? "正在提交…" : mode == 0 ? "保存" : "发送") { Task { await submit() } }
                                    .disabled(
                                        saving || sent || (mode == 0 ? !request.isValid : !inbox.isValid)
                                    )
                                    .accessibilityLabel(mode == 0 ? "保存新待办" : "转给\(assistantName)")
                            }
                        }
                    }
                }
            }
            #if os(iOS)
                .presentationDetents([.medium, .large])
                .presentationBackground(Palette.page)
            #else
                .frame(width: 520, height: 620)
            #endif
            .interactiveDismissDisabled(saving)
        }

        @MainActor func submit() async {
            guard !saving else { return }
            saving = true
            errorText = nil
            defer { saving = false }
            do {
                if mode == 0 {
                    let saved = try await store.createTodo(request)
                    if saved.duplicate {
                        result = saved
                    } else {
                        created?()
                        dismiss()
                    }
                } else {
                    try await store.submitInbox(inbox)
                    sent = true
                }
            } catch APIError.notFound { errorText = ManualEntryText.backendUpdate } catch {
                errorText = AppStore.describe(error)
            }
        }
    }
#endif
