#if os(macOS)
    import AssistantCore
    import SwiftUI

    /// One Bot: its character on top, then its cards, a wavy line, then what it said like a
    /// chat, and a box at the bottom to write to it (message sources only read, so they have
    /// none). Scrolling up moves the cards away so the chat gets the whole window; the grip
    /// above the line folds them, and they fold by themselves once scrolled out of the window
    /// or on a hard flick down (CardsFold).
    struct MacBotPage: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @State private var cardsShown = true
        @State private var scroll = PageScroll()
        @State private var draft = ""
        @State private var showingInfo = false
        @State private var showingRuns = false
        /// What the character in the header acts out: hello when the page opens, nodding when
        /// the Bot says something.
        @State private var cue: MascotCue?
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        static let bottom = "bottom"
        static let space = "page"
        static let spacing: CGFloat = 14

        var plugin: PluginView? { store.plugin(bot.id) }

        var cardCount: Int { BotCards.count(of: bot, in: store) }

        var body: some View {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: Self.spacing) {
                        BotHero(bot: bot, status: store.statusLine(bot), cue: cue)
                        if cardsShown {
                            VStack(alignment: .leading, spacing: Self.spacing) {
                                BotCards(bot: bot)
                            }
                            .reportGeometry { PageGeometry(cardsHeight: $0.size.height) }
                        }
                        CardsHandle(shown: $cardsShown, count: cardCount)
                            .reportGeometry { PageGeometry(cardsEnd: $0.frame(in: .named(Self.space)).minY) }
                        WavyDivider(tint: bot.accent)
                        ChatTimeline(bot: bot)
                        Color.clear.frame(height: 1).id(Self.bottom)
                    }
                    .padding(.horizontal, 24)
                    .padding(.vertical, 16)
                    .reportGeometry { PageGeometry(top: $0.frame(in: .named(Self.space)).minY) }
                    .background(ScrollViewFinder { scroll.view = $0 })
                }
                .coordinateSpace(name: Self.space)
                .onPreferenceChange(PageGeometryKey.self, perform: scrolled)
                .onChange(of: cardsShown) { _, shown in
                    if shown { scroll.fold = CardsFold() }  // opened again: measure afresh
                }
                .background(Palette.page)
                .safeAreaInset(edge: .bottom) {
                    if bot.kind == .source {
                        Text("消息来源只负责读消息，不能对话。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Palette.page)
                    } else {
                        ChatInput(
                            placeholder: store.isReplying(bot) ? "\(bot.name) 正在回复…" : "给 \(bot.name) 发消息",
                            text: $draft, disabled: store.isReplying(bot)
                        ) {
                            let text = draft
                            draft = ""
                            cue = MascotCue(.greet)
                            Task {
                                await store.send(text, to: bot)
                                if store.message != nil { draft = text }  // not sent: keep it
                            }
                        }
                    }
                }
                .onChange(of: store.chats[bot.id]?.count) { old, new in
                    // Down to a new line, not when the chat first loads: the page opens at the
                    // top, with the character and its cards.
                    guard let old, let new, new > old else { return }
                    withAnimation { proxy.scrollTo(Self.bottom, anchor: .bottom) }
                }
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
            .navigationTitle(bot.name)
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
                }
            }
            .sheet(isPresented: $showingRuns) {
                RunsSheet().environmentObject(store)
            }
            // On opening, when the plugin is switched, and after every refresh of the App (each
            // 30 s and the refresh button), so cards such as the usage limits stay current.
            .task(id: CardsLoad(state: plugin?.state, refreshed: store.lastRefresh, plugins: bot.pluginIDs)) {
                for id in bot.pluginIDs {
                    await store.loadCards(id)
                }
            }
            .task {
                await store.loadChat(bot)
            }        }

        func scrolled(_ geometry: PageGeometry) {
            if let height = geometry.cardsHeight { scroll.cardsHeight = height }
            guard cardsShown, !scroll.folding, let top = geometry.top, let end = geometry.cardsEnd
            else { return }
            let now = ProcessInfo.processInfo.systemUptime
            if let reason = scroll.fold.check(offset: -top, cardsEnd: end, at: now) {
                fold(reason)
            }
        }

        func fold(_ reason: CardsFold.Reason) {
            guard reason == .scrolledPast, let view = scroll.view else {
                withAnimation(reduceMotion ? nil : .snappy) { cardsShown = false }
                return
            }
            // The cards are above the window: take them out and scroll back by their height
            // before anything is drawn, so the chat being read stays where it is.
            let removed = scroll.cardsHeight + Self.spacing
            scroll.folding = true
            DispatchQueue.main.async {
                let origin = view.contentView.bounds.origin
                cardsShown = false
                view.window?.contentView?.layoutSubtreeIfNeeded()
                view.contentView.scroll(to: NSPoint(x: origin.x, y: origin.y - removed))
                view.reflectScrolledClipView(view.contentView)
                scroll.folding = false
            }
        }
    }

#endif
