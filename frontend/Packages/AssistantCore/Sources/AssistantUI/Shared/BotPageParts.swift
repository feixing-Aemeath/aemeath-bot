#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// A Bot's cards: the assistant's todos and Agent, 知识库Bot's knowledge base and inputs, or the
    /// plugin's cards (健康Bot's with 「今天的记录」, the readiness check-in, packed among them), in
    /// as many columns of at least `minWidth` as fit.
    struct BotCards: View {
        @EnvironmentObject private var store: AppStore
        @AppStorage(CardStyle.storageKey) private var style = CardStyle.plain
        let bot: Bot
        var minWidth: CGFloat = 240
        /// The iPad opens the knowledge base in its detail column, not in a sheet.
        var wideLayout = false

        /// The cards of the Bot's plugins (its own, then the joined ones). The health plugin's
        /// reports are in the chat, so its text cards (the same reports) are left out.
        static func cards(of bot: Bot, in store: AppStore) -> [Card] {
            let all = bot.pluginIDs.flatMap { store.cards[$0] ?? [] }
            let cards = bot.id == "health" ? all.filter { $0.type != .text } : all
            // Cards a plugin puts first (today's classes) before the others, whichever plugin.
            return cards.filter(\.first) + cards.filter { !$0.first }
        }

        static func hasCheckin(_ bot: Bot) -> Bool { bot.id == "health" }

        /// How many cards folding takes away.
        @MainActor static func count(of bot: Bot, in store: AppStore) -> Int {
            switch bot.kind {
            case .assistant:
                return 2 + cards(of: bot, in: store).count
            case .knowledge:
                return 1 + (bot.joined.isEmpty ? 0 : 1) + cards(of: bot, in: store).count
            case .character, .source:
                return max(cards(of: bot, in: store).count + (hasCheckin(bot) ? 1 : 0), 1)
            }
        }

        var body: some View {
            // The Bot's color: progress bars, timelines and rings, and the chosen card style.
            content.tint(bot.accent).environment(\.cardStyle, style)
        }

        @ViewBuilder var content: some View {
            let cards = Self.cards(of: bot, in: store)
            let hasCheckin = Self.hasCheckin(bot)
            if bot.isAssistant {
                // Her own plugins first (天气: 今日提醒, the weather, what to take), then the
                // todos and the Agent.
                // The grid packs cards by height, so the todos and the Agent stay out of it, in
                // this order, as on the Mac.
                if !cards.isEmpty {
                    CardGrid(minWidth: minWidth, spacing: 12) { pluginCards(cards) }
                }
                assistantCards(startingAt: cards.count)
            } else if bot.kind == .knowledge {
                // The knowledge base and where its material comes from, then the inputs' own
                // cards (视频's queue).
                knowledgeCards
                if !cards.isEmpty {
                    CardGrid(minWidth: minWidth, spacing: 12) {
                        pluginCards(cards, startingAt: 2)
                    }
                }
            } else if let plugin = store.plugin(bot.id), plugin.state != .enabled {
                PluginOffCard(plugin: plugin)
            } else if cards.isEmpty && !hasCheckin {
                Text("\(bot.name) 现在没有要显示的卡片。").foregroundStyle(.secondary)
            } else {
                CardGrid(minWidth: minWidth, spacing: 12) {
                    if hasCheckin {
                        ReadinessCheckinForm().modifier(CardMotion(index: 0))
                    }
                    ForEach(Array(cards.enumerated()), id: \.element.id) { index, card in
                        CardView(card: card).modifier(CardMotion(index: index + (hasCheckin ? 1 : 0)))
                            .layoutValue(key: CardTileKey.self, value: card.isTile)
                    }
                }
            }
        }

        @ViewBuilder func assistantCards(startingAt index: Int) -> some View {
            TodayTodosCard().modifier(CardMotion(index: index))
            AgentCard().modifier(CardMotion(index: index + 1))
        }

        @ViewBuilder var knowledgeCards: some View {
            KnowledgeCard(opensInDetail: wideLayout).modifier(CardMotion(index: 0))
            if !bot.joined.isEmpty {
                KnowledgeInputsCard(bot: bot).modifier(CardMotion(index: 1))
            }
        }

        func pluginCards(_ cards: [Card], startingAt start: Int = 0) -> some View {
            ForEach(Array(cards.enumerated()), id: \.element.id) { index, card in
                CardView(card: card).modifier(CardMotion(index: start + index))
                    .layoutValue(key: CardTileKey.self, value: card.isTile)
            }
        }
    }

    /// What a Bot said, like a chat: a day's first message under its date, the Bot's three
    /// bouncing dots while one of its jobs runs. Only the last two days with messages at first;
    /// a button above them brings back earlier days a few at a time.
    struct ChatTimeline: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @State private var days = 2
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            let (messages, hidden) = BotTimeline.recent(store.messages(bot), days: days, calendar: store.calendar)
            let now = Date()
            VStack(alignment: .leading, spacing: 14) {
                if hidden > 0 {
                    Button {
                        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { days += 3 }
                    } label: {
                        Label("更早的消息（还有 \(hidden) 天）", systemImage: "chevron.up")
                            .font(.footnote)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                }
                if messages.isEmpty {
                    Text("\(bot.name) 还没有说过话。")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 24)
                }
                ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                    let day = BotTimeline.day(message.date, now: now, calendar: store.calendar)
                    if index == 0
                        || day != BotTimeline.day(messages[index - 1].date, now: now, calendar: store.calendar)
                    {
                        DayDivider(text: day)
                    }
                    MessageView(message: message, bot: bot)
                        .transition(
                            reduceMotion
                                ? .opacity
                                : .asymmetric(
                                    insertion: .move(edge: .bottom).combined(with: .opacity), removal: .opacity))
                }
                if store.botState(bot) == .working {
                    TypingBubble(bot: bot)
                        .transition(.opacity)
                }
            }
            .animation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.85), value: messages.map(\.id))
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: store.botState(bot) == .working)
        }
    }

    /// Cards fade and rise in one after another when a page opens, and lift a little under the
    /// pointer.
    struct CardMotion: ViewModifier {
        let index: Int
        @State private var shown = false
        @State private var hovering = false
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        func body(content: Content) -> some View {
            content
                .opacity(shown ? 1 : 0)
                .offset(y: shown || reduceMotion ? 0 : 10)
                .scaleEffect(hovering && !reduceMotion ? 1.012 : 1)
                .shadow(color: .black.opacity(hovering ? 0.08 : 0), radius: 10, y: 4)
                .animation(.easeOut(duration: 0.15), value: hovering)
                #if os(macOS)
                    // Like the sidebar rows: onHover can miss the exit when a sheet (editing a
                    // todo) takes the focus, and leave a card lifted.
                    .pointerHover($hovering)
                #else
                    .onHover { hovering = $0 }
                #endif
                .onAppear {
                    guard !shown else { return }
                    withAnimation(reduceMotion ? nil : .spring(response: 0.45, dampingFraction: 0.85).delay(Double(min(index, 8)) * 0.05)) {
                        shown = true
                    }
                }
        }
    }

    /// When a Bot page loads its cards again.
    struct CardsLoad: Equatable {
        var state: PluginState?
        var refreshed: Date?
        /// A built-in Bot learns its plugins (助理's 天气) with the first refresh; the page
        /// may have asked for its cards before, with none.
        var plugins: [String] = []
    }

    /// The grip between the cards and the chat: folds the cards away or brings them back.
    struct CardsHandle: View {
        @Binding var shown: Bool
        let count: Int
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            Button {
                withAnimation(reduceMotion ? nil : .snappy) { shown.toggle() }
            } label: {
                VStack(spacing: 2) {
                    Image(systemName: "line.3.horizontal")
                        .foregroundStyle(.secondary)
                    Text(shown ? "收起卡片" : "展开卡片（\(count)）")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    /// The box at the bottom, like Claude's: a white rounded box with the text on top and, under
    /// it, which Agent and model answer (a menu) and a round clay button that sends (or Return on
    /// the Mac).
    struct ChatInput: View {
        @EnvironmentObject private var store: AppStore
        let placeholder: String
        @Binding var text: String
        /// While the Bot is still answering: typing works, sending waits.
        var disabled = false
        /// An iPad hardware keyboard sends with Return; Shift-Return still adds a line.
        var returnSends = false
        /// The page's side margin.
        var inset: CGFloat = 24
        let send: () -> Void

        var empty: Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                TextField(placeholder, text: $text, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(1...6)
                    .padding(.horizontal, 4)
                    .padding(.top, 3)
                    .onSubmit {
                        if !empty && !disabled { send() }
                    }
                    .onKeyPress(.return, phases: .down) { key in
                        guard returnSends, key.modifiers.isEmpty else { return .ignored }
                        if !empty && !disabled { send() }
                        return .handled
                    }
                HStack(spacing: 8) {
                    ModelMenu()
                    Spacer(minLength: 0)
                    Button(action: send) {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.white)
                            .frame(width: 30, height: 30)
                            .background(Circle().fill(empty || disabled ? Palette.trail : Palette.clay))
                    }
                    .buttonStyle(.plain)
                    .disabled(empty || disabled)
                    .help("发送（Return）")
                    .accessibilityLabel("发送")
                }
            }
            .padding(.leading, 12)
            .padding(.trailing, 9)
            .padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 20).fill(Palette.card))
            .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(Palette.hairline))
            .shadow(color: .black.opacity(0.05), radius: 8, y: 2)
            .padding(.horizontal, inset)
            .padding(.top, 6)
            .padding(.bottom, inset == 24 ? 16 : 8)
            .background(Palette.page)
        }
    }

    /// Which Agent and model answer the next line: 自动 (the Mac's order) or one model of
    /// Claude Code or Codex, as the Mac lists them. The choice is remembered on this device.
    struct ModelMenu: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            let options = store.chatOptions
            let agents = options.map(\.agent).filter { !$0.isEmpty }.reduce(into: [String]()) { seen, agent in
                if !seen.contains(agent) { seen.append(agent) }
            }
            Menu {
                item("自动", .automatic)
                ForEach(agents, id: \.self) { agent in
                    Section(agent) {
                        ForEach(options.filter { $0.agent == agent }) { option in
                            item(option.title, option.choice)
                        }
                    }
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "sparkle")
                        .font(.system(size: 11, weight: .semibold))
                    Text(ChatOption.label(of: store.chatChoice, in: options))
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 9, weight: .semibold))
                }
                .font(.footnote.weight(.medium))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Capsule().fill(Palette.bubble))
                .contentShape(Capsule())
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .fixedSize()
            .help("选择回复用的 Agent 和模型")
            .accessibilityLabel("回复用的模型：\(ChatOption.label(of: store.chatChoice, in: options))")
        }

        /// A choice, checked when it is the current one.
        func item(_ title: String, _ choice: ChatChoice) -> some View {
            Button {
                store.chatChoice = choice
            } label: {
                if store.chatChoice == choice {
                    Label(title, systemImage: "checkmark")
                } else {
                    Text(title)
                }
            }
        }
    }

    /// A plugin's switch and manifest facts, from the info button in the toolbar.
    struct PluginInfoPanel: View {
        @EnvironmentObject private var store: AppStore
        let pluginID: String

        var body: some View {
            if let plugin = store.plugin(pluginID) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(plugin.name).font(.headline)
                    if !plugin.description.isEmpty {
                        Text(plugin.description)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    PluginSwitch(plugin: plugin)
                    if PluginConfigEdit.hasPreviewMode(plugin) {
                        PreviewModeSwitch(plugin: plugin)
                    }
                    ForEach(PluginConfigEdit.lists(of: plugin), id: \.key) { list in
                        Divider()
                        NameListEditor(plugin: plugin, key: list.key, title: list.title)
                    }
                    Divider()
                    InfoRow(label: "ID", value: plugin.id)
                    InfoRow(label: "版本", value: plugin.version)
                    InfoRow(label: "平台", value: plugin.platforms.joined(separator: "、"))
                    InfoRow(label: "提供", value: plugin.provides.joined(separator: "、"))
                    InfoRow(
                        label: "权限",
                        value: plugin.permissions.isEmpty ? "无" : plugin.permissions.joined(separator: "、"))
                    InfoRow(label: "贴图", value: plugin.mascot?.sticker ?? "占位图（还没有贴图）")
                    if let schedule = plugin.schedule {
                        InfoRow(label: "时间表", value: CronText.describe(schedule))
                    }
                }
                .padding(16)
                #if os(macOS)
                    .frame(width: 320, alignment: .leading)
                #else
                    .frame(maxWidth: .infinity, alignment: .leading)
                #endif
            }
        }
    }

    /// 启用 for a plugin; one this computer cannot run stays off.
    struct PluginSwitch: View {
        @EnvironmentObject private var store: AppStore
        let plugin: PluginView

        var body: some View {
            Toggle(
                "启用",
                isOn: Binding(
                    get: { plugin.enabled },
                    set: { value in Task { await store.setPlugin(plugin.id, enabled: value) } })
            )
            .toggleStyle(.switch)
            .disabled(plugin.state == .unsupported)
        }
    }

    /// Preview mode of QQ and WeChat: read only the conversation list, never open a chat.
    struct PreviewModeSwitch: View {
        @EnvironmentObject private var store: AppStore
        let plugin: PluginView

        var body: some View {
            VStack(alignment: .leading, spacing: 2) {
                Toggle(
                    "仅预览",
                    isOn: Binding(
                        get: { PluginConfigEdit.previewOnly(plugin) },
                        set: { value in
                            Task {
                                await store.setPluginConfig(
                                    plugin.id, PluginConfigEdit.setting(plugin, "preview_only", to: .bool(value)))
                            }
                        })
                )
                .toggleStyle(.switch)
                Text(
                    PluginConfigEdit.previewOnly(plugin)
                        ? "只读会话列表里每个会话今天的最新一条，不点开聊天，不会标为已读。"
                        : "会点开白名单会话、往上滚动读完整内容，消息会被标为已读；需要辅助功能权限。"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// A list of chat names in a plugin's configuration: the whitelist of a message source,
    /// the course groups of the homework plugin.
    struct NameListEditor: View {
        @EnvironmentObject private var store: AppStore
        let plugin: PluginView
        let key: String
        let title: String
        @State private var draft = ""

        var names: [String] { PluginConfigEdit.names(plugin, key) }

        var body: some View {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.subheadline.weight(.semibold))
                if names.isEmpty {
                    Text(key == "whitelist" ? "还没有会话。只有加进来的会话会被读取。" : "还没有。这些会话里来的待办都算作业。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(names, id: \.self) { name in
                    HStack {
                        Text(name).lineLimit(1)
                        Spacer()
                        Button {
                            save(names.filter { $0 != name })
                        } label: {
                            Image(systemName: "minus.circle")
                        }
                        .buttonStyle(.borderless)
                        .help("移除")
                    }
                }
                HStack {
                    TextField("名字和聊天软件里显示的一样", text: $draft)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(add)
                    Button("添加", action: add)
                        .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }

        func add() {
            let next = PluginConfigEdit.adding(draft, to: names)
            guard next != names else { return }
            draft = ""
            save(next)
        }

        func save(_ list: [String]) {
            let value = JSONValue.array(list.map(JSONValue.string))
            Task { await store.setPluginConfig(plugin.id, PluginConfigEdit.setting(plugin, key, to: value)) }
        }
    }

    /// In place of the cards while a plugin is off or broken.
    /// 知识库Bot's first card: the knowledge base at a glance; it opens the knowledge base (in a
    /// sheet on the iPhone, where a sideways swipe opens the Bots; pushed on the Mac).
    struct KnowledgeCard: View {
        @EnvironmentObject private var store: AppStore
        var opensInDetail = false
        @State private var overview: KnowledgeOverview?
        @State private var open = false

        var summary: String {
            guard let overview else { return "主题、资料和每晚整理好的笔记" }
            var parts = ["\(overview.topics.count) 个主题"]
            if overview.pending > 0 { parts.append("\(overview.pending) 份待整理") }
            if let last = overview.organize.lastRunAt {
                let when = RelativeTime.describe(last, now: Date(), calendar: store.calendar)
                parts.append(overview.organize.status == "failed" ? "\(when)整理失败" : "\(when)整理过")
            }
            return parts.joined(separator: " · ")
        }

        var body: some View {
            #if os(iOS)
                if opensInDetail {
                    card.navigationDestination(isPresented: $open) { KnowledgeHome() }
                } else {
                    card.sheet(isPresented: $open) { PhoneKnowledge() }
                }
            #else
                card.navigationDestination(isPresented: $open) { KnowledgeHome() }
            #endif
        }

        var card: some View {
            Button {
                open = true
            } label: {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 6) {
                        CardTitle("知识库", icon: "books.vertical")
                        Text(summary).font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .cardSurface()
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // Once per page: reading the overview has the Mac look through the whole vault.
            .task { overview = try? await store.backend.knowledge() }
        }
    }

    /// Where 知识库Bot's material comes from: each input (GoodNotes, 课堂照片, 视频) with how it is
    /// doing and its switch. Setting one up happens on the Mac (docs/webdav.md, docs/knowledge.md).
    struct KnowledgeInputsCard: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot

        var body: some View {
            VStack(alignment: .leading, spacing: 10) {
                CardTitle("资料来源", icon: "tray.and.arrow.down")
                ForEach(store.inputs(of: bot), id: \.id) { plugin in
                    HStack(spacing: 10) {
                        StatusDot(color: plugin.state.color)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(Names.plugin(plugin.id))
                            Text(store.statusLine(plugin))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer(minLength: 0)
                        // The row already names it.
                        PluginSwitch(plugin: plugin)
                            .labelsHidden()
                            .accessibilityLabel("启用\(Names.plugin(plugin.id))")
                    }
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
        }
    }

    struct PluginOffCard: View {
        @EnvironmentObject private var store: AppStore
        let plugin: PluginView

        var text: String {
            plugin.state == .disabled ? "已停用，打开后才会有卡片。" : store.statusLine(plugin)
        }

        var body: some View {
            HStack(spacing: 10) {
                StatusDot(color: plugin.state.color)
                Text(text)
                Spacer()
                PluginSwitch(plugin: plugin)
            }
            .padding(12)
            .cardSurface()
        }
    }

#endif
