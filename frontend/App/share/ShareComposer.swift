import AssistantCore
import SwiftUI

struct ShareComposer: View {
    let queue: ShareQueue?
    let finish: () -> Void
    let assistantName = BotPreferences.load().displayName(for: Bot.assistantID, fallback: "助理")
    @State private var text: String
    @State private var truncated: Bool
    @State private var note = ""
    @State private var source: MissedSource?
    @State private var missed = false
    @State private var saving = false
    @State private var status: ShareDeliveryStatus?
    @State private var configuration: ShareConfiguration?
    @Environment(\.colorScheme) private var colorScheme

    init(content: ShareContent, queue: ShareQueue?, finish: @escaping () -> Void) {
        self.queue = queue
        self.finish = finish
        _text = State(initialValue: content.text)
        _truncated = State(initialValue: content.truncated)
        _status = State(initialValue: content.text.isEmpty ? .invalid : nil)
        _configuration = State(initialValue: try? queue?.configuration())
    }
    private var page: Color {
        colorScheme == .dark ? Color(red: 0.15, green: 0.15, blue: 0.14) : Color(red: 0.98, green: 0.976, blue: 0.961)
    }
    private var request: InboxItem { ShareContent.item(text: text, note: note, source: source, missed: missed) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("转给\(assistantName)")
                        .font(.system(size: 27, weight: .semibold, design: .serif))
                        .accessibilityAddTraits(.isHeader)
                    Text("把重要的消息交给她，再加一句你的想法。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("消息原文") {
                    TextEditor(text: $text).frame(minHeight: 150, maxHeight: 240)
                        .accessibilityLabel("消息原文，可编辑，最多八千字")
                    Text("\(text.unicodeScalars.count) / 8000 字").font(.caption).foregroundStyle(.secondary)
                    if truncated {
                        Text("原文超过 8000 字，已保留前 8000 字，请检查后发送。")
                            .font(.footnote).foregroundStyle(.orange)
                    }
                }
                Section {
                    TextField("加一句备注（可不填）", text: $note, axis: .vertical).lineLimit(1...3)
                        .accessibilityLabel("备注，最多五百字")
                    Text("\(note.unicodeScalars.count) / 500 字").font(.caption).foregroundStyle(.secondary)
                    Picker("来自哪个会话", selection: $source) {
                        Text("不指定").tag(Optional<MissedSource>.none)
                        ForEach(configuration?.conversations ?? [], id: \.self) { item in
                            Text(Names.plugin(item.plugin) + " · " + item.chatName).tag(Optional(item))
                        }
                    }
                    Toggle("这是助手漏掉的", isOn: $missed)
                }
                if configuration?.useMockData == true {
                    Section { Text(ShareDeliveryStatus.mock.text).foregroundStyle(.secondary) }
                } else if configuration == nil {
                    Section { Text(ShareDeliveryStatus.unavailable.text).foregroundStyle(.orange) }
                }
                if let status {
                    Section {
                        Label(status.text(assistantName: assistantName), systemImage: status.shouldClose ? "checkmark.circle" : "info.circle")
                            .accessibilityLabel(status.text(assistantName: assistantName))
                    }
                }
            }
            .disabled(saving || status?.shouldClose == true)
            .scrollContentBackground(.hidden)
            .background(page)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消", action: finish).disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "正在发送…" : "发送") { Task { await send() } }
                        .disabled(saving || !request.isValid || configuration == nil || configuration?.useMockData == true || status?.shouldClose == true)
                }
            }
        }
        .tint(Color(red: 0.85, green: 0.47, blue: 0.34))
        .onChange(of: text) { _, value in
            if status == .invalid, !value.isEmpty { status = nil }
            if value.unicodeScalars.count > ShareContent.textLimit {
                text = ShareContent.limit(value, to: ShareContent.textLimit)
                truncated = true
            }
        }
        .onChange(of: note) { _, value in note = ShareContent.limit(value, to: ShareContent.noteLimit) }
        .interactiveDismissDisabled(saving)
    }

    @MainActor private func send() async {
        guard !saving, request.isValid, let queue else { return }
        saving = true
        defer { saving = false }
        do {
            // Re-read: the main App may have changed its mode or destination while this was open.
            guard let config = try queue.configuration() else { status = .unavailable; return }
            configuration = config
            guard !config.useMockData else { status = .mock; return }
            let entry = try queue.enqueue(request, configuration: config)
            if entry.receipt != nil { status = .sent }
            else if let url = URL(string: config.baseURL), ["http", "https"].contains(url.scheme), url.host != nil,
                    let tokens = SharedKeychainToken.store() {
                let session = ShareDelivery.session()
                defer { session.invalidateAndCancel() }
                let client = APIClient(baseURL: url, tokens: tokens, session: session)
                status = await ShareDelivery.send(entry: entry, queue: queue) { try await client.submitInbox($0) }
            } else { status = .queued }
            if status?.shouldClose == true {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
                finish()
            }
        } catch ShareQueueError.mock { status = .mock }
        catch { status = .unavailable }
    }
}
