import Foundation

/// Who answers a chat line: the backend's usual Agent order, or one Agent first with one of
/// its models (ChatSend.harness / .model). Remembered on the device between launches.
public struct ChatChoice: Hashable, Sendable {
    public var harness: String?
    public var model: String?

    public init(harness: String?, model: String?) {
        self.harness = harness
        self.model = model
    }

    public static let automatic = ChatChoice(harness: nil, model: nil)

    /// As kept in UserDefaults: "", "codex" or "claude_code/opus".
    public var stored: String {
        guard let harness else { return "" }
        return model.map { "\(harness)/\($0)" } ?? harness
    }

    public init(stored: String?) {
        let parts = (stored ?? "").split(separator: "/", maxSplits: 1).map(String.init)
        self.init(harness: parts.first, model: parts.count > 1 ? parts[1] : nil)
    }
}

/// The choices the chat box offers, from the Agents in the status: 自动 first, then each
/// available Agent's models, or the Agent alone (its default model) when it lists none.
public struct ChatOption: Hashable, Identifiable, Sendable {
    public var choice: ChatChoice
    /// The Agent, for grouping: "Claude Code"; empty for 自动.
    public var agent: String
    /// The model, or 自动 / 默认模型.
    public var title: String

    public var id: String { choice.stored }

    /// In the chat box: the model alone; the Agent too when it has no models listed.
    public var short: String {
        if choice.harness == nil { return title }
        return choice.model == nil ? agent : title
    }

    public static func build(status: Status?) -> [ChatOption] {
        var options = [ChatOption(choice: .automatic, agent: "", title: "自动")]
        for harness in ["claude_code", "codex", "deepseek"] {
            guard let agent = status?.agents[harness], agent.available else { continue }
            let name = Names.harness(harness)
            if agent.models.isEmpty {
                options.append(
                    ChatOption(choice: ChatChoice(harness: harness, model: nil), agent: name, title: "默认模型"))
            }
            for model in agent.models {
                options.append(
                    ChatOption(choice: ChatChoice(harness: harness, model: model.id), agent: name, title: model.name))
            }
        }
        return options
    }

    /// What the chat box shows for `choice`, also when the status does not list it (yet).
    public static func label(of choice: ChatChoice, in options: [ChatOption]) -> String {
        if let option = options.first(where: { $0.choice == choice }) { return option.short }
        if let model = choice.model { return model }
        return choice.harness.map(Names.harness) ?? "自动"
    }
}
