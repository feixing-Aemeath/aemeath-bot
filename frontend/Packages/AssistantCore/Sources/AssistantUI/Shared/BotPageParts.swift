#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// How many of a Bot's cards show before 「还有 N 张卡片」 (on 仪表盘). When only one card
    /// would fold, it shows too.
    enum CardFold: Equatable {
        /// All of them (the cards beside a chat).
        case none
        /// The cards in the first `rows` rows of a grid of `columns` (the Mac, the iPad), a pair
        /// of tiles being one cell.
        case rows(Int, columns: Int)
        /// The iPhone's first rows that hold at most so many cards (its first couple).
        case cards(Int)
    }

    /// A Bot's color and the chosen card style for its cards, and ticking a todo in one of them.
    struct BotCardEnvironment: ViewModifier {
        @EnvironmentObject private var store: AppStore
        @AppStorage(CardStyle.storageKey) private var style = CardStyle.plain
        let bot: Bot

        func body(content: Content) -> some View {
            // The Bot's color: progress bars, timelines and rings, and the chosen card style.
            content.tint(bot.accent).environment(\.cardStyle, style)
                .environment(\.cardAccentText, bot.accentText)
                .environment(\.toggleTodo) { todo in Task { await store.toggleDone(todo) } }
        }
    }

    /// A Bot's cards, on 仪表盘 and in the panel beside its chat: the assistant's todos, 知识库Bot's
    /// knowledge base and inputs, or the plugin's cards (健康Bot's with 「今天的记录」, the
    /// readiness check-in, in a row under them), in as many columns of at least `minWidth` as fit.
    /// On 仪表盘 a Bot shows the cards of its first rows (its leading ones first) and folds the
    /// rest into a row 「还有 N 张卡片」; opening them is remembered per Bot on this device. In a
    /// group's cells (`cardChrome` `.cell`, 仪表盘) the cards touch, a hairline between them.
    struct BotCards: View {
        @EnvironmentObject private var store: AppStore
        @AppStorage private var showAll: Bool
        @Environment(\.cardChrome) private var chrome
        let bot: Bot
        var minWidth: CGFloat
        /// The iPad opens the knowledge base in its detail column, not in a sheet.
        var wideLayout: Bool
        var fold: CardFold
        var dashboardCards: [Card]?

        init(
            bot: Bot, minWidth: CGFloat = 240, wideLayout: Bool = false, fold: CardFold = .none,
            dashboardCards: [Card]? = nil
        ) {
            self.bot = bot
            self.minWidth = minWidth
            self.wideLayout = wideLayout
            self.fold = fold
            self.dashboardCards = dashboardCards
            _showAll = AppStorage(wrappedValue: false, "assistant.cards.all.\(bot.id)")
        }

        var cells: Bool { chrome == .cell }
        var spacing: CGFloat { cells ? 0 : 12 }

        /// The cards of the Bot's plugins (its own, then the joined ones). The health plugin's
        /// reports are in the chat, so its text cards (the same reports) are left out, and so
        /// is a list that says it has nothing (健康预警 with no alerts: 「count」 0).
        static func cards(of bot: Bot, in store: AppStore) -> [Card] {
            let all = bot.pluginIDs.flatMap { store.cards[$0] ?? [] }
            let cards = (bot.id == "health" ? all.filter { $0.type != .text } : all)
                .filter { !($0.type == .list && $0.data["count"]?.doubleValue == 0) }
            // Cards a plugin puts first (today's classes, the page's big number) before the
            // others, whichever plugin.
            let leads = { (card: Card) in card.first || card.isWide }
            return cards.filter(leads) + cards.filter { !leads($0) }
        }

        static func hasCheckin(_ bot: Bot) -> Bool { bot.id == "health" }

        var body: some View {
            content.modifier(BotCardEnvironment(bot: bot))
        }

        /// The cards in the first rows and how many fold away when closed (none when only one
        /// would). `leading` cells come before the cards in the grid (知识库Bot's knowledge base and
        /// inputs) and take their room in the first rows.
        func folding(_ cards: [Card], leading: Int = 0) -> (cards: [Card], folded: Int) {
            let tiles = Array(repeating: false, count: leading) + cards.map(\.isTile)
            let first: [Int]
            switch fold {
            case .none: return (cards, 0)
            case .rows(let rows, let columns): first = CardPacking.firstRows(tiles: tiles, columns: columns, rows: rows)
            case .cards(let most): first = CardPacking.firstRows(tiles: tiles, holding: most + leading)
            }
            let kept = first.filter { $0 >= leading }.map { $0 - leading }
            guard cards.count - kept.count > 1 else { return (cards, 0) }
            return (kept.map { cards[$0] }, cards.count - kept.count)
        }

        /// The cards shown and how many are folded away, and whether there is a fold row (also
        /// while open, to fold them again).
        func shown(_ cards: [Card], leading: Int = 0) -> (cards: [Card], folded: Int, folds: Bool) {
            let closed = folding(cards, leading: leading)
            return showAll ? (cards, 0, closed.folded > 0) : (closed.cards, closed.folded, closed.folded > 0)
        }

        @ViewBuilder var content: some View {
            let all = dashboardCards ?? Self.cards(of: bot, in: store)
            let hasCheckin = Self.hasCheckin(bot)
            if bot.isAssistant {
                let (cards, folded, folds) = shown(all)
                // Her own plugins first (天气: 今日提醒, the weather, what to take), then the
                // todos, out of the grid, as on the Mac. The Agent is in the header: its state,
                // the latest extraction and 立即提炼.
                if !cards.isEmpty {
                    CardGrid(minWidth: minWidth, spacing: spacing, fillsLastRow: cells) { pluginCards(cards) }
                }
                foldRow(folded, folds: folds)
                assistantCards(startingAt: cards.count)
            } else if bot.kind == .knowledge {
                // The knowledge base and where its material comes from, then the inputs' own
                // cards (视频's queue); in a group's cells all in one grid.
                let leading = bot.joined.isEmpty ? 1 : 2
                let (cards, folded, folds) = shown(all, leading: cells ? leading : 0)
                if cells {
                    CardGrid(minWidth: minWidth, spacing: 0, fillsLastRow: true) {
                        knowledgeCards
                        pluginCards(cards, startingAt: leading)
                    }
                } else {
                    knowledgeCards
                    if !cards.isEmpty {
                        CardGrid(minWidth: minWidth, spacing: spacing) {
                            pluginCards(cards, startingAt: 2)
                        }
                    }
                }
                foldRow(folded, folds: folds)
            } else if let plugin = store.plugin(bot.id), plugin.state != .enabled {
                PluginOffCard(plugin: plugin)
            } else if all.isEmpty && !hasCheckin {
                Text("\(store.displayName(bot)) 现在没有要显示的卡片。").foregroundStyle(.secondary)
            } else {
                let (cards, folded, folds) = shown(all)
                CardGrid(minWidth: minWidth, spacing: spacing, fillsLastRow: cells) {
                    pluginCards(cards)
                }
                // A slim row of its own under the grid, so it does not leave a gap in a row.
                if hasCheckin && !cells {
                    CheckinCard().modifier(CardMotion(index: cards.count))
                }
                foldRow(folded, folds: folds)
            }
        }

        @ViewBuilder func foldRow(_ folded: Int, folds: Bool) -> some View {
            if folds {
                Button {
                    withAnimation(.snappy) { showAll.toggle() }
                } label: {
                    HStack(spacing: 6) {
                        Text(folded > 0 ? "还有 \(folded) 张卡片" : "收起")
                        Image(systemName: folded > 0 ? "chevron.down" : "chevron.up")
                            .font(.caption.weight(.semibold))
                    }
                    .font(CardFont.note)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 6)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.horizontal, CardSurface.padding)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityLabel(folded > 0 ? "\(store.displayName(bot))还有 \(folded) 张卡片" : "收起\(store.displayName(bot))的卡片")
            }
        }

        @ViewBuilder func assistantCards(startingAt index: Int) -> some View {
            TodayTodosCard().modifier(CardMotion(index: index))
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
    /// bouncing dots while one of its jobs runs. Only today at first; a button above it brings
    /// back the days before, one day per tap.
    struct ChatTimeline: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @State private var earlier = 0
        @State private var answeredQuestion: String?
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        /// The App has heard from the Mac once, and has the chat (a message source has none).
        var loaded: Bool {
            store.lastRefresh != nil && (bot.kind == .source || store.chats[bot.id] != nil)
        }

        var body: some View {
            let now = Date()
            let (messages, hidden) = BotTimeline.recent(
                store.messages(bot), earlier: earlier, now: now, calendar: store.calendar)
            let question = BotChat.replyQuestion(
                store.chats[bot.id] ?? [], visible: messages, now: now, calendar: store.calendar)
            VStack(alignment: .leading, spacing: 14) {
                if let next = hidden.first {
                    Button {
                        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { earlier += 1 }
                    } label: {
                        Label(
                            "看\(BotTimeline.day(next, now: now, calendar: store.calendar))的消息"
                                + (hidden.count > 1 ? "（更早还有 \(hidden.count - 1) 天）" : ""),
                            systemImage: "chevron.up"
                        )
                        .font(.footnote)
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                } else if store.chatCursors[bot.id] != nil {
                    // Nothing left in memory: read further back, and open the day it brings.
                    Button {
                        Task {
                            await store.loadEarlierChat(bot)
                            let left = BotTimeline.recent(
                                store.messages(bot), earlier: earlier, now: Date(),
                                calendar: store.calendar
                            ).hidden
                            if !left.isEmpty {
                                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { earlier += 1 }
                            }
                        }
                    } label: {
                        Label("看更早的消息", systemImage: "chevron.up")
                            .font(.footnote)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                }
                if messages.isEmpty {
                    if !loaded {
                        // Nothing read yet: say so rather than "never said anything".
                        if store.online {
                            MessageSkeleton()
                        } else {
                            Text("连不上 Mac，暂时看不到 \(store.displayName(bot)) 的消息。")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 24)
                        }
                    } else {
                        VStack(spacing: 12) {
                            Text(hidden.isEmpty ? "\(store.displayName(bot)) 还没有说过话。" : "今天还没有新消息。")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                            if hidden.isEmpty, bot.kind != .source, store.online, !store.isReplying(bot) {
                                SuggestionChips(bot: bot)
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 20)
                    }
                }
                ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                    let day = BotTimeline.day(message.date, now: now, calendar: store.calendar)
                    if index == 0
                        || day != BotTimeline.day(messages[index - 1].date, now: now, calendar: store.calendar)
                    {
                        DayDivider(text: day)
                    }
                    MessageView(
                        message: message, bot: bot,
                        showsHeader: BotTimeline.startsGroup(at: index, in: messages, calendar: store.calendar)
                    )
                    .transition(
                            reduceMotion
                                ? .opacity
                                : .asymmetric(
                                    insertion: .move(edge: .bottom).combined(with: .opacity), removal: .opacity))
                    if let question, question.id == message.id, question.id != answeredQuestion, !store.isReplying(bot)
                    {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                ForEach(Array((question.replies ?? []).prefix(4)), id: \.self) { reply in
                                    Button {
                                        answeredQuestion = question.id
                                        Task { await store.send(reply, to: bot) }
                                    } label: {
                                        Text(reply)
                                            .font(.callout)
                                            .padding(.horizontal, 14)
                                            .padding(.vertical, 8)
                                            .background(.tint.opacity(0.1), in: Capsule())
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityLabel("回答：\(reply)")
                                }
                            }
                        }
                    }
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

    /// Two gray message shapes while a Bot's chat is on its way.
    struct MessageSkeleton: View {
        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                Text("今天的总结").font(.headline)
                Text("这里是一段 Bot 说的话，正在从 Mac 上读出来。\n第二行稍微短一些。")
                Text("还有一行。")
            }
            .redacted(reason: .placeholder)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel("正在读取消息")
        }
    }

    /// A few things to ask a Bot that has not said anything yet, like the starters in an AI app;
    /// a tap sends one.
    struct SuggestionChips: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot

        static func suggestions(for bot: Bot) -> [String] {
            switch bot.isAssistant ? "assistant" : bot.id {
            case "assistant": return ["今天还有什么要做？", "这周有哪些截止的？"]
            case Bot.knowledgeID: return ["最近整理了哪些笔记？", "帮我找上周的课堂笔记"]
            case "health": return ["昨晚睡得怎么样？", "今天适合练多大强度？"]
            case "finance": return ["这个月还能花多少？", "最近钱都花在哪儿了？"]
            case "homework": return ["这周还有哪些作业没交？", "明天有什么课？"]
            case "grades": return ["我的学分还差多少？"]
            case "token_usage": return ["这个月用了多少 Token？"]
            case "campus": return ["最近有什么学校活动？"]
            default: return ["你最近在忙什么？"]
            }
        }

        var body: some View {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Self.suggestions(for: bot), id: \.self) { text in
                        Button {
                            Task { await store.send(text, to: bot) }
                        } label: {
                            Text(text)
                                .font(.callout)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 8)
                                .background(Capsule().strokeBorder(Palette.hairline))
                                .contentShape(Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 2)
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// On the assistant's page when an Agent cannot run: a note while the other one covers, a
    /// warning when neither can (todos stop being extracted). Nothing while all is well.
    struct AgentBanner: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            let agents = store.status?.agents ?? [:]
            let down = AgentText.unavailable(agents)
            if store.online, !down.isEmpty {
                let all = down.count == agents.count
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(all ? Color.orange : Color.secondary)
                    Text(
                        all
                            ? "\(down.joined(separator: "和"))都不可用，待办暂时不会提炼。"
                            : "\(down.joined(separator: "、"))不可用，由其他 Agent 顶上。"
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(all ? Color.orange.opacity(0.1) : Palette.bubble))
            }
        }
    }

    /// Under a page's header while the Mac cannot be reached: since when the page's data is, and
    /// a button to try again.
    struct OfflineBanner: View {
        @EnvironmentObject private var store: AppStore
        @State private var trying = false

        var body: some View {
            if !store.online {
                HStack(spacing: 10) {
                    Image(systemName: store.authorizationIssue == nil ? "wifi.slash" : "key")
                        .foregroundStyle(.secondary)
                    Text(text).font(.callout).foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                    Button {
                        trying = true
                        Task {
                            await store.refresh()
                            trying = false
                        }
                    } label: {
                        if trying {
                            ProgressView().controlSize(.small)
                        } else {
                            Text("重试").font(.callout.weight(.medium))
                        }
                    }
                    .buttonStyle(.borderless)
                    .disabled(trying)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Palette.bubble))
                .transition(.opacity)
            }
        }

        var text: String {
            let reason = store.authorizationIssue ?? "连不上 Mac"
            guard let at = store.todayUpdatedAt else { return reason }
            return "\(reason) · 显示的是 \(RelativeTime.describe(at, now: Date(), calendar: store.calendar))的数据"
        }
    }

    /// Cards fade and rise in one after another when a page opens, and lift a little under the
    /// pointer (loose cards only: a group's cells stay in their place).
    struct CardMotion: ViewModifier {
        let index: Int
        @State private var shown = false
        @State private var hovering = false
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @Environment(\.cardChrome) private var chrome

        func body(content: Content) -> some View {
            let lifted = hovering && chrome == .card
            content
                .opacity(shown ? 1 : 0)
                .offset(y: shown || reduceMotion ? 0 : 6)
                .scaleEffect(lifted && !reduceMotion ? 1.012 : 1)
                .shadow(color: .black.opacity(lifted ? 0.08 : 0), radius: 10, y: 4)
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
                    // Quick: the last of them used to arrive nearly a second after a click, which
                    // made switching Bots feel slow.
                    withAnimation(reduceMotion ? nil : .spring(response: 0.28, dampingFraction: 0.9).delay(Double(min(index, 6)) * 0.02)) {
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

    /// The box at the bottom, like Claude's: one rounded row while it is empty, with which Agent
    /// and model answer (a menu) on the left and a round clay button that sends (or Return on
    /// the Mac) on the right; it grows upward as the text takes more lines. The page fades out
    /// behind it instead of stopping at a solid band. Offline it cannot send and says why.
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
        /// The widest the box gets (the Mac's reading column).
        var maxWidth: CGFloat = .infinity
        let send: () -> Void

        var empty: Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        var offline: Bool { !store.online }
        var blocked: Bool { empty || disabled || offline }

        var body: some View {
            HStack(alignment: .bottom, spacing: 8) {
                ModelMenu()
                    .padding(.bottom, 1)
                TextField(offline ? "连上 Mac 后才能发送" : placeholder, text: $text, axis: .vertical)
                    .textFieldStyle(.plain)
                    .accessibilityLabel("消息内容，回复期间可以继续输入")
                    .lineLimit(1...6)
                    .padding(.vertical, 6)
                    .onSubmit {
                        if !blocked { send() }
                    }
                    .onKeyPress(.return, phases: .down) { key in
                        guard returnSends, key.modifiers.isEmpty else { return .ignored }
                        if !blocked { send() }
                        return .handled
                    }
                Button(action: send) {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(blocked ? AnyShapeStyle(.tertiary) : AnyShapeStyle(Color.white))
                        .frame(width: 32, height: 32)
                        .background(Circle().fill(blocked ? AnyShapeStyle(Color.primary.opacity(0.08)) : AnyShapeStyle(Palette.clay)))
                }
                .buttonStyle(.plain)
                .disabled(blocked)
                .help(disabled ? "上一句回复后就能发送，期间可以继续输入" : "发送（Return）")
                .accessibilityLabel("发送")
            }
            .padding(.leading, 8)
            .padding(.trailing, 6)
            .padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Palette.card))
            .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(Palette.hairline))
            .shadow(color: .black.opacity(0.06), radius: 10, y: 3)
            .frame(maxWidth: maxWidth)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, inset)
            .padding(.top, 14)
            .padding(.bottom, inset == 24 ? 16 : 8)
            .background(PageFade())
        }
    }

    /// Behind what sits at the bottom of a page (the chat box): the page fading out above it, so
    /// the chat scrolls away under it softly; solid down through the home indicator.
    struct PageFade: View {
        var body: some View {
            VStack(spacing: 0) {
                LinearGradient(colors: [Palette.page.opacity(0), Palette.page], startPoint: .top, endPoint: .bottom)
                    .frame(height: 18)
                Palette.page
            }
            .ignoresSafeArea(edges: .bottom)
            .allowsHitTesting(false)
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
                .padding(.vertical, 7)
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
                if opensInDetail {
                    card.navigationDestination(isPresented: $open) { KnowledgeHome() }
                } else {
                    // From the cards beside a chat, which have no page of their own to go on to.
                    card.sheet(isPresented: $open) {
                        NavigationStack {
                            KnowledgeHome()
                                .toolbar {
                                    ToolbarItem(placement: .cancellationAction) {
                                        Button("完成") { open = false }
                                    }
                                }
                        }
                        .frame(minWidth: 820, minHeight: 600)
                        .environmentObject(store)
                    }
                }
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
                .padding(CardSurface.padding)
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
        @Environment(\.dashboardPreview) private var preview
        let bot: Bot

        var body: some View {
            VStack(alignment: .leading, spacing: 10) {
                CardTitle("资料来源", icon: "tray.and.arrow.down")
                let inputs = store.inputs(of: bot)
                if preview != nil {
                    Text(DashboardEdit.sourceSummary(inputs))
                        .font(CardFont.body)
                } else {
                    ForEach(inputs, id: \.id) { plugin in
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 6) {
                                    Text(Names.plugin(plugin.id))
                                    if let color = plugin.state.dotColor { StatusDot(color: color) }
                                }
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
            }
            .padding(CardSurface.padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
        }
    }

    struct PluginOffCard: View {
        @EnvironmentObject private var store: AppStore
        @Environment(\.dashboardPreview) private var preview
        let plugin: PluginView

        var text: String {
            plugin.state == .disabled ? "已停用，打开后才会有卡片。" : store.statusLine(plugin)
        }

        var body: some View {
            HStack(spacing: 10) {
                StatusDot(color: plugin.state.color)
                Text(text)
                Spacer()
                if preview == nil { PluginSwitch(plugin: plugin) }
            }
            .padding(CardSurface.padding)
            .cardSurface()
        }
    }

#endif
