#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// Fetches source text only after the user asks; excerpts remain usable with an old backend.
    struct TodoOriginalTexts: View {
        @EnvironmentObject private var store: AppStore
        let todo: Todo
        @State private var items: [TodoSourceTextItem]?
        @State private var loading = false
        @State private var errorText: String?
        var body: some View {
            VStack(alignment: .leading, spacing: 10) {
                Text("原文").font(CardFont.body.weight(.semibold))
                TodoSourceQuote(todo: todo)
                if todo.sourceExcerpt.isEmpty {
                    Text("这条待办没有原文片段。").font(CardFont.note).foregroundStyle(.secondary)
                }
                if let items {
                    if items.isEmpty {
                        Text("没有可用的完整原文，引用的消息可能已不在记录里。").font(CardFont.note).foregroundStyle(.secondary)
                    }
                    ForEach(items) { item in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(
                                [Names.plugin(item.plugin), item.chatName, item.sender].filter { !$0.isEmpty }
                                    .joined(separator: " · ")
                            )
                            .font(CardFont.note).foregroundStyle(.secondary)
                            Text(DueText.format(item.sentAt, "yyyy年M月d日 HH:mm", store.calendar))
                                .font(CardFont.note).foregroundStyle(.secondary)
                            Text(item.text).font(CardFont.body).selectableText()
                        }
                        .padding(.vertical, 4)
                    }
                } else {
                    Button(loading ? "正在读取…" : "看完整原文") { Task { await load() } }
                        .disabled(loading).accessibilityLabel("看这条待办的完整原文")
                }
                if let errorText { Text(errorText).font(CardFont.note).foregroundStyle(.secondary) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        @MainActor func load() async {
            loading = true
            errorText = nil
            defer { loading = false }
            do { items = try await store.backend.todoSources(id: todo.id).items } catch APIError.notFound {
                errorText = ManualEntryText.backendUpdate + "，或这条待办已被删除，暂时不能读取完整原文。"
            } catch { errorText = "没读到完整原文：" + AppStore.describe(error) }
        }
    }
#endif
