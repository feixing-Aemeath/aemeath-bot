#if !os(watchOS)
    import AssistantCore
    import SwiftUI
    #if os(iOS)
        import UIKit
    #endif

    /// One visible Bot's tile and remaining panel contents, derived together.
    struct DashboardBot: Identifiable {
        let bot: Bot
        let glance: Glance?
        let cards: [Card]
        let hasPanel: Bool
        var id: String { bot.id }

        init(_ bot: Bot, store: AppStore) {
            self.bot = bot
            let error = store.plugin(bot.id)?.state == .error
            let panel = DashboardEdit.panel(error ? [] : BotCards.cards(of: bot, in: store))
            glance = panel.glance
            cards = panel.cards
            hasPanel = bot.kind == .knowledge || error || !cards.isEmpty
        }
    }

    /// The page above the Bots, holding every Bot's cards so a Bot's own page is its chat. What
    /// needs the user comes first: under the date and the title 「今天」, 助理's group (the todos
    /// first with what she could not settle under them, the weather and 出门提醒 beside them,
    /// 立即提炼); then 一览, a small tile per Bot that has a figure to show, the ones that need a
    /// look first, and one line for the Bots with nothing to report (a tap opens their chat);
    /// then a group per Bot that has cards left once its tile took its own: one rounded panel
    /// with its face, name and latest line on its color, 「聊天」 to its page, and its cards inside
    /// as cells, lists cut to their first few items. The Bots the user hid in 设置 → 仪表盘 are
    /// not on the page at all; when every one is hidden, the page says so and leads to 设置.
    /// The message sources and the Bots switched off are one line each at the bottom.
    struct DashboardPage: View {
        @EnvironmentObject private var store: AppStore
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        let openBot: (String) -> Void
        let openCards: (String) -> Void
        let openSettings: () -> Void
        /// The iPhone: one column, the tiles in a row that scrolls sideways, and a Bot's first two
        /// rows of cards (助理's all) before the rest fold.
        var compact = false
        /// The width of the page's column, for how many cards go in a row.
        @State private var width: CGFloat = 0
        /// The title has scrolled up under the bar (the iPhone): the bar shows it instead.
        @State private var titleHidden = false

        /// The page's id where a Bot's id is kept (the last page open).
        static let id = "dashboard"
        static let title = "仪表盘"
        static let maxWidth: CGFloat = 1120

        /// The Bots with a section: those shown on 仪表盘 (设置 → 仪表盘) that are the assistant,
        /// 知识库Bot or a character that is on, in the sidebar's order. Until the user orders them,
        /// the one about the App's own running (用量) comes after the ones about the day.
        static func sections(_ bots: [Bot], store: AppStore) -> [Bot] {
            let preferences = store.botPreferences
            let shown = preferences.dashboardBots(bots).filter { $0.isBuiltIn || isOn($0, store: store) }
            guard !preferences.hasCustomOrder else { return shown }
            let last = { (bot: Bot) in bot.isUpkeep && !preferences[bot.id].pinned }
            return shown.filter { !last($0) } + shown.filter(last)
        }

        static func isOn(_ bot: Bot, store: AppStore) -> Bool {
            guard let plugin = store.plugin(bot.id) else { return true }
            return plugin.state == .enabled || plugin.state == .error
        }

        /// 「健康Bot」 under its face in the row: 「健康」.
        static func shortName(_ bot: Bot) -> String {
            let name = bot.name.replacingOccurrences(of: "Bot", with: "").trimmingCharacters(in: .whitespaces)
            return name.isEmpty ? bot.name : name
        }

        /// A card in a group is at least this wide (three in a row in a Mac window of the usual
        /// size, four at the widest).
        var minWidth: CGFloat { compact ? 300 : 260 }

        /// The cards of a group's grid in a row.
        var columns: Int {
            compact ? 1 : CardPacking.columns(width: Double(width), minWidth: Double(minWidth), spacing: 0)
        }

        /// How much of a Bot shows before 「还有 N 张卡片」: on the iPhone its first couple of cards
        /// (whole rows, so two tiles stay together); wider, one row where three or more cards go
        /// in a row, two in a narrow window.
        var fold: CardFold { compact ? .cards(2) : .rows(columns >= 3 ? 1 : 2, columns: columns) }

        var body: some View {
            let bots = store.bots
            let shown = Self.sections(bots, store: store)
            let groups = shown.filter { !$0.isAssistant }.map { DashboardBot($0, store: store) }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: compact ? 16 : 20) {
                        header
                            .onGeometryChange(for: Bool.self) { $0.frame(in: .scrollView).maxY < 12 } action: { hidden in
                                withAnimation(.easeOut(duration: 0.18)) { titleHidden = hidden }
                            }
                        OfflineBanner()
                        AgentBanner()
                        if store.botPreferences.dashboardBots(bots).isEmpty {
                            ContentUnavailableView {
                                Label("没有显示的 Bot", systemImage: "square.grid.2x2")
                            } actions: {
                                Button("去设置", action: openSettings)
                            }
                        } else {
                            ForEach(shown.filter(\.isAssistant)) { bot in group(bot, cards: []) }
                            GlanceStrip(groups: groups, compact: compact, openBot: openBot) { section in
                                if section.hasPanel {
                                    withAnimation(reduceMotion ? nil : .snappy) { proxy.scrollTo(section.id, anchor: .top) }
                                } else {
                                    openCards(section.id)
                                }
                            }
                            ForEach(groups.filter(\.hasPanel)) { group($0.bot, cards: $0.cards) }
                            footer(bots)
                        }
                    }
                    .frame(maxWidth: Self.maxWidth, alignment: .leading)
                    .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, compact ? 16 : 28)
                    .padding(.top, compact ? 4 : 18)
                    .padding(.bottom, 28)
                }
                .background(Palette.page)
                #if os(iOS)
                    .refreshable { await store.refresh() }
                #endif
            }
            #if os(iOS)
                .toolbar {
                    if compact {
                        ToolbarItem(placement: .principal) {
                            Text(Self.title)
                                .font(Typography.serif(17))
                                .opacity(titleHidden ? 1 : 0)
                                .accessibilityHidden(!titleHidden)
                        }
                    }
                }
            #endif
            // A backend that does not name each card's plugin in /today: ask each plugin, after
            // every refresh, as the Bot pages did.
            .task(id: store.lastRefresh) {
                await store.loadCards(of: bots.filter { $0.isBuiltIn || ($0.kind == .character && Self.isOn($0, store: store)) })
            }
        }

        var phoneListLimit: Bool {
            #if os(iOS)
                UIDevice.current.userInterfaceIdiom == .phone
            #else
                false
            #endif
        }

        func group(_ bot: Bot, cards: [Card]) -> some View {
            DashboardGroup(
                bot: bot, compact: compact, wide: !compact && width >= 600, minWidth: minWidth,
                fold: fold, cards: cards, openBot: openBot
            )
            .environment(\.dashboardPreview, DashboardPreview(compact: phoneListLimit, openAll: { openCards(bot.id) }))
            .id(bot.id)
        }

        /// The date over the title, as Apple's Fitness and News do.
        var header: some View {
            VStack(alignment: .leading, spacing: 2) {
                Text(DueText.format(Date(), "M月d日 EEE", store.calendar))
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.secondary)
                Text(Self.title)
                    .font(Typography.serif(compact ? 30 : 34))
                    .accessibilityAddTraits(.isHeader)
            }
        }

        /// One line for the message sources and one for the Bots switched off: each a small chip
        /// that opens its page, with a dot when something is wrong.
        @ViewBuilder func footer(_ bots: [Bot]) -> some View {
            let sources = bots.filter { $0.kind == .source }
            let off = bots.filter { $0.kind == .character && store.botPreferences[$0.id].visible && !Self.isOn($0, store: store) }
            if !sources.isEmpty || !off.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    if !sources.isEmpty { chips("消息来源", sources) }
                    if !off.isEmpty { chips("已停用", off) }
                }
                .padding(.top, 4)
            }
        }

        func chips(_ title: String, _ bots: [Bot]) -> some View {
            BotChips(title: title, bots: bots, action: openBot)
        }
    }

    /// One line of Bots as small chips after a word that says what they share (「消息来源」,
    /// 「已停用」, 「没有新情况」): each with its face, its name and a dot when something is wrong.
    struct BotChips: View {
        @EnvironmentObject private var store: AppStore
        let title: String
        let bots: [Bot]
        let action: (String) -> Void

        var body: some View {
            HStack(alignment: .center, spacing: 10) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .fixedSize()
                ScrollView(.horizontal) {
                    HStack(spacing: 6) {
                        ForEach(bots) { bot in
                            Button {
                                action(bot.id)
                            } label: {
                                HStack(spacing: 5) {
                                    BotAvatar(bot: bot).frame(width: 16, height: 16)
                                    Text(store.displayName(bot))
                                    if let color = store.botState(bot).dotColor { StatusDot(color: color) }
                                }
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.leading, 4)
                                .padding(.trailing, 10)
                                .padding(.vertical, 4)
                                .background(Capsule().fill(Palette.bubble))
                                .contentShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .scrollIndicators(.hidden)
                .modifier(SidewaysZone(id: "chips-\(title)"))
            }
        }
    }

    /// 仪表盘 in the sidebar: a grid sign in a clay circle where the Bots have faces.
    struct DashboardRow: View {
        var side: CGFloat = 36

        var body: some View {
            HStack(spacing: 10) {
                Image(systemName: "square.grid.2x2.fill")
                    .font(.system(size: side * 0.42, weight: .medium))
                    .foregroundStyle(Palette.clay)
                    .frame(width: side, height: side)
                    .background(Circle().fill(Palette.clay.opacity(0.14)))
                Text(DashboardPage.title).font(.body.weight(.medium))
                Spacer(minLength: 0)
            }
        }
    }

    /// A Bot's cards beside its chat: a column on the right of the Mac and the iPad, a sheet
    /// from the bottom on the iPhone (SwiftUI's inspector), all of them, without folding.
    struct BotCardsPanel: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @Binding var shown: Bool
        @Environment(\.horizontalSizeClass) private var sizeClass

        var body: some View {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("\(store.displayName(bot)) 的卡片")
                            .font(Typography.serif(18))
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        #if os(iOS)
                            if sizeClass == .compact {
                                Button("完成") { shown = false }
                                    .font(.body.weight(.medium))
                            }
                        #endif
                    }
                    .padding(.bottom, 2)
                    BotCards(bot: bot, minWidth: 260)
                }
                .padding(16)
            }
            .background(Palette.page)
            #if os(iOS)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .presentationBackground(Palette.page)
            #endif
        }
    }

    /// The button that shows or hides a Bot's cards.
    struct CardsPanelButton: View {
        @Binding var shown: Bool

        var body: some View {
            Button {
                shown.toggle()
            } label: {
                Label(shown ? "隐藏卡片" : "卡片", systemImage: "square.grid.2x2")
            }
            .help(shown ? "隐藏卡片" : "显示这个 Bot 的卡片")
        }
    }
#endif
