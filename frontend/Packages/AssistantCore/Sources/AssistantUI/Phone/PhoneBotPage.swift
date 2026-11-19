#if os(iOS)
    import AssistantCore
    import SwiftUI
    import UIKit

    /// One Bot on the iPhone, as on the Mac: its face on top, the wavy line with the paper plane,
    /// then what it said like a chat, and a box at the bottom to write to it (message sources only
    /// read, so they have none). The page opens at the newest line. The button on the left opens
    /// the Bots; the one on the right brings up the Bot's cards in a sheet (all of them together
    /// are on 仪表盘). A plugin's switch and facts and the Agent's runs are in 设置.
    struct PhoneBotPage: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @Binding var cardsShown: Bool
        let openBots: () -> Void
        @State private var draft = ""
        /// What the face on top acts out: hello when the page opens, nodding when the Bot says
        /// something.
        @State private var cue: MascotCue?
        /// The header has scrolled up under the bar: the bar shows the Bot's name instead.
        @State private var heroHidden = false

        static let bottom = "bottom"

        var plugin: PluginView? { store.plugin(bot.id) }

        var body: some View {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        PhoneBotHero(bot: bot, status: store.statusLine(bot), cue: cue)
                            .onGeometryChange(for: Bool.self) { $0.frame(in: .scrollView).maxY < 12 } action: { hidden in
                                withAnimation(.easeOut(duration: 0.18)) { heroHidden = hidden }
                            }
                        OfflineBanner()
                        if bot.isAssistant {
                            AgentBanner()
                            SharePendingNotice()
                        }
                        WavyDivider(tint: bot.accent)
                        ChatTimeline(bot: bot)
                        Color.clear.frame(height: 1).id(Self.bottom)
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
                }
                .modifier(ChatScrollAnchor())
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await store.refresh() }
                .background(Palette.page)
                .safeAreaInset(edge: .bottom) { input }
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
            .environment(\.openBotCards) {
                hideKeyboard()
                cardsShown = true
            }
            .inspector(isPresented: $cardsShown) {
                BotCardsPanel(bot: bot, shown: $cardsShown)
            }
            .navigationTitle(store.displayName(bot))
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.page, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(action: openBots) {
                        Image(systemName: "line.3.horizontal")
                    }
                    .tint(.primary)
                    .accessibilityLabel("所有 Bot")
                }
                // The name is in the header; the bar repeats it, small with the face, only once
                // the header has scrolled away.
                ToolbarItem(placement: .principal) {
                    HStack(spacing: 7) {
                        BotAvatar(bot: bot).frame(width: 24, height: 24)
                        Text(store.displayName(bot)).font(Typography.serif(17))
                    }
                    .opacity(heroHidden ? 1 : 0)
                    .accessibilityHidden(!heroHidden)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        hideKeyboard()
                        cardsShown = true
                    } label: {
                        Image(systemName: "square.grid.2x2")
                    }
                    .tint(.primary)
                    .accessibilityLabel("\(store.displayName(bot)) 的卡片")
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
            // On opening, when the plugin is switched, and after every refresh of the App; nothing
            // to do when /today brought every plugin's cards.
            .task(id: CardsLoad(state: plugin?.state, refreshed: store.lastRefresh, plugins: bot.pluginIDs)) {
                await store.loadCards(of: [bot])
            }
            .task {
                await store.loadChat(bot)
            }
        }

        @ViewBuilder var input: some View {
            if bot.kind == .source {
                Text("消息来源只负责读消息，不能对话。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 18)
                    .padding(.bottom, 10)
                    .background(PageFade())
            } else {
                ChatInput(
                    placeholder: store.isReplying(bot) ? "\(store.displayName(bot)) 正在回复…" : "给 \(store.displayName(bot)) 发消息",
                    text: $draft, disabled: store.isReplying(bot),
                    returnSends: UIDevice.current.userInterfaceIdiom == .pad, inset: 12
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
    }

    /// The top of a Bot's page, compact like a profile row: its face in a round frame of its
    /// color on the left; its name with what it looks after beside it, and how it is doing
    /// under them; the assistant's 立即提炼 on the right. The face is alive: it blinks, plays
    /// when tapped and nods on `cue`; what an animation raises (an umbrella, Zzz) may leave the
    /// circle at the top. Scrolled away, the bar shows the name instead.
    struct PhoneBotHero: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let status: String
        var cue: MascotCue?

        static let side: CGFloat = 64

        var body: some View {
            let state = store.botState(bot)
            HStack(alignment: .center, spacing: 14) {
                face(state)
                VStack(alignment: .leading, spacing: 5) {
                    ViewThatFits(in: .horizontal) {
                        HStack(alignment: .center, spacing: 8) { name; tag }
                        VStack(alignment: .leading, spacing: 4) { name; tag }
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        if let color = state.dotColor { StatusDot(color: color) }
                        Text(status)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    .font(.footnote)
                }
                Spacer(minLength: 0)
                if bot.isAssistant { RunNowChip() }
            }
            .padding(.top, 6)
            .padding(.bottom, 2)
        }

        var name: some View {
            Text(store.displayName(bot))
                .font(Typography.serif(26))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .accessibilityAddTraits(.isHeader)
        }

        var tag: some View {
            Text(bot.tag)
                .font(.caption.weight(.medium))
                .foregroundStyle(bot.accentText)
                .lineLimit(1)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Capsule().fill(bot.accent.opacity(0.14)))
        }

        @ViewBuilder func face(_ state: MascotState) -> some View {
            if bot.kind == .source {
                BotAvatar(bot: bot)
                    .frame(width: Self.side, height: Self.side)
            } else {
                // Zoomed on the face, the same size for every character (as in the Bot list).
                let place = FacePlacement.hero(bot.look.resolved(in: .main), side: Self.side)
                MascotView(
                    look: bot.look, state: state, since: bot.isAssistant ? store.stateSince : Date(),
                    cue: cue, interactive: true, characterAnimations: true
                )
                .frame(width: place.drawn, height: place.drawn)
                .position(place.center)
                .frame(width: Self.side, height: Self.side)
                .mask(OpenTopCircle())
                .background(
                    Circle().fill(
                        LinearGradient(
                            colors: [bot.accent.opacity(0.24), bot.accent.opacity(0.08)],
                            startPoint: .topLeading, endPoint: .bottomTrailing))
                )
                .overlay(Circle().stroke(bot.accent.opacity(0.4), lineWidth: 1))
                .accessibilityLabel(store.displayName(bot))
            }
        }
    }

    /// A circle that is open above its middle.
    private struct OpenTopCircle: Shape {
        func path(in rect: CGRect) -> Path {
            Path(ellipseIn: rect).union(
                Path(
                    CGRect(
                        x: rect.minX - rect.width / 2, y: rect.minY - rect.height, width: rect.width * 2,
                        height: rect.height * 1.5)))
        }
    }

#endif
