import Foundation

/// Presentation choices for the iPad. The narrow layout uses the same saved Bot as the sidebar.
public enum PadPresentation {
    public static let botStorageKey = "assistant.pad.bot"
    public static let cardMinimumWidth = 270.0
    public static let sourceNotice = "消息来源只负责读消息，不能对话。"

    /// Wait for the plugins before deciding that a saved Bot has gone away.
    public static func selectedBot(storedID: String, bots: [Bot], settled: Bool) -> Bot? {
        bots.first { $0.id == storedID }
            ?? (settled ? bots.first { $0.isAssistant } : nil)
    }

    /// The last thing the Bot said; my messages and App notices do not replace its preview.
    public static func preview(messages: [BotMessage], fallback: String) -> String {
        messages.last { $0.author == .bot && !$0.preview.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }?
            .preview ?? fallback
    }

    public static func placeholder(botName: String, replying: Bool) -> String {
        replying ? "\(botName) 正在回复…" : "给 \(botName) 发消息"
    }
}
