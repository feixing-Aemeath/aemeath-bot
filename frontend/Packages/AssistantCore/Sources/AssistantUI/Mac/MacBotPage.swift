#if os(macOS)
    import AssistantCore
    import SwiftUI

    /// One Bot: its character on top, a wavy line, then what it said like a chat, and a box at
    /// the bottom to write to it (message sources only read, so they have none). The page opens
    /// at the newest line. Its cards are in a column on the right (the toolbar's 卡片 shows or
    /// hides it, remembered on this Mac); all the Bots' cards together are on 仪表盘.
    struct MacBotPage: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @Binding var cardsShown: Bool
        @State private var draft = ""
        @State private var showingInfo = false
        @State private var showingRuns = false
        /// What the character in the header acts out: hello when the page opens, nodding when
        /// the Bot says something.
        @State private var cue: MascotCue?

        static let bottom = "bottom"
        static let spacing: CGFloat = 14
        /// The chat and its box: a reading column, as in Claude's and ChatGPT's apps.
        static let chatWidth: CGFloat = 760

        var plugin: PluginView? { store.plugin(bot.id) }

        var body: some View {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .center, spacing: Self.spacing) {
                        BotHero(bot: bot, status: store.statusLine(bot), cue: cue)
                        OfflineBanner()
                        if bot.isAssistant { AgentBanner() }
                        WavyDivider(tint: bot.accent)
                        ChatTimeline(bot: bot)
                        Color.clear.frame(height: 1).id(Self.bottom)
                    }
                    .frame(maxWidth: Self.chatWidth)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 16)
                }
                .modifier(ChatScrollAnchor())
                .background(Palette.page)
                .safeAreaInset(edge: .bottom) {
                    if bot.kind == .source {
                        Text("消息来源只负责读消息，不能对话。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 18)
                            .padding(.bottom, 12)
                            .background(PageFade())
                    } else {
                        ChatInput(
                            placeholder: store.isReplying(bot) ? "\(store.displayName(bot)) 正在回复…" : "给 \(store.displayName(bot)) 发消息",
                            text: $draft, disabled: store.isReplying(bot), maxWidth: Self.chatWidth
                        ) {
                            let text = draft
                            draft = ""
                            cue = MascotCue(.greet)
                            Task {
                                let sent = await store.send(text, to: bot)
                                if !sent, draft.isEmpty { draft = text }  // not sent: keep it
                            }
                        }
                    }
                }
                .onChange(of: store.chats[bot.id]?.count) { old, new in
                    guard let new else { return }
                    // The chat came in after the page opened: down to its newest line at once;
                    // a new line afterwards: down to it smoothly.
                    if let old {
                        guard new > old else { return }
                        withAnimation { proxy.scrollTo(Self.bottom, anchor: .bottom) }
                    } else {
                        proxy.scrollTo(Self.bottom, anchor: .bottom)
                    }
                }
            }
            .environment(\.openBotCards) { cardsShown = true }
            .inspector(isPresented: $cardsShown) {
                BotCardsPanel(bot: bot, shown: $cardsShown)
                    .inspectorColumnWidth(min: 300, ideal: 360, max: 520)
            }
            .onAppear {
                store.markSeen(bot)
                cue = MascotCue(.greet)
            }
            .onChange(of: store.messages(bot).count) {
                store.markSeen(bot)
                if store.messages(bot).last?.author == .bot {
                    cue = MascotCue(.talk)
                }
            }
            .navigationTitle(store.displayName(bot))
            // The header already shows the name large; the window keeps it for its menu only.
            .hiddenWindowTitle()
            .toolbar {
                ToolbarItemGroup {
                    if bot.isAssistant {
                        Button {
                            showingRuns = true
                        } label: {
                            Label("运行记录", systemImage: "list.bullet.rectangle")
                        }
                        .help("Agent 的运行记录")
                    } else if plugin != nil {
                        Button {
                            showingInfo = true
                        } label: {
                            Label("插件信息", systemImage: "info.circle")
                        }
                        .help("插件开关和信息")
                        .popover(isPresented: $showingInfo, arrowEdge: .bottom) {
                            PluginInfoPanel(pluginID: bot.id).environmentObject(store)
                        }
                    }
                    RefreshButton()
                    CardsPanelButton(shown: $cardsShown)
                }
            }
            .sheet(isPresented: $showingRuns) {
                RunsSheet().environmentObject(store)
            }
            // On opening, when the plugin is switched, and after every refresh of the App (each
            // 30 s and the refresh button), so cards such as the usage limits stay current;
            // nothing to do when /today brought every plugin's cards.
            .task(id: CardsLoad(state: plugin?.state, refreshed: store.lastRefresh, plugins: bot.pluginIDs)) {
                await store.loadCards(of: [bot])
            }
            .task {
                await store.loadChat(bot)
            }
        }
    }

    extension View {
        /// The window title's text taken out of the toolbar (macOS 15 and later).
        @ViewBuilder func hiddenWindowTitle() -> some View {
            if #available(macOS 15.0, *) {
                self.toolbar(removing: .title)
            } else {
                self
            }
        }
    }

#endif
