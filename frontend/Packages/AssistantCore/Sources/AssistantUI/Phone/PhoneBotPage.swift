#if os(iOS)
    import AssistantCore
    import SwiftUI
    import UIKit

    /// One Bot on the iPhone, as on the Mac: its face on top, its cards, the wavy line with the
    /// paper plane, then what it said like a chat, and a box at the bottom to write to it (message
    /// sources only read, so they have none). The grip above the line folds the cards away. The
    /// only button on top opens the Bots; a plugin's switch and facts and the Agent's runs are in
    /// 设置.
    struct PhoneBotPage: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let openBots: () -> Void
        @State private var cardsShown = true
        @State private var draft = ""
        /// What the face on top acts out: hello when the page opens, nodding when the Bot says
        /// something.
        @State private var cue: MascotCue?

        static let bottom = "bottom"

        var plugin: PluginView? { store.plugin(bot.id) }

        var body: some View {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        PhoneBotHero(bot: bot, status: store.statusLine(bot), cue: cue)
                        if cardsShown {
                            VStack(alignment: .leading, spacing: 12) {
                                BotCards(bot: bot, minWidth: 300)
                            }
                            .transition(.opacity.combined(with: .move(edge: .top)))
                        }
                        CardsHandle(shown: $cardsShown, count: BotCards.count(of: bot, in: store))
                        WavyDivider(tint: bot.accent)
                        ChatTimeline(bot: bot)
                        Color.clear.frame(height: 1).id(Self.bottom)
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
                }
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await store.refresh() }
                .background(Palette.page)
                .safeAreaInset(edge: .bottom) { input }
                .onChange(of: store.chats[bot.id]?.count) { old, new in
                    // Down to a new line, not when the chat first loads: the page opens at the
                    // top, with the face and the cards.
                    guard let old, let new, new > old else { return }
                    withAnimation { proxy.scrollTo(Self.bottom, anchor: .bottom) }
                }
            }
            .navigationTitle(bot.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.page, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(action: openBots) {
                        Image(systemName: "line.3.horizontal")
                    }
                    .accessibilityLabel("所有 Bot")
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
            // On opening, when the plugin is switched, and after every refresh of the App.
            .task(id: CardsLoad(state: plugin?.state, refreshed: store.lastRefresh, plugins: bot.pluginIDs)) {
                for id in bot.pluginIDs {
                    await store.loadCards(id)
                }
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
                    .padding(.vertical, 10)
                    .background(Palette.page)
            } else {
                ChatInput(
                    placeholder: store.isReplying(bot) ? "\(bot.name) 正在回复…" : "给 \(bot.name) 发消息",
                    text: $draft, disabled: store.isReplying(bot),
                    returnSends: UIDevice.current.userInterfaceIdiom == .pad, inset: 12
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
    }

    /// The top of a Bot's page, as in the sketch: its face in a round frame of its color in the
    /// middle, then its name, what it looks after and how it is doing. The face is alive: it
    /// blinks, plays when tapped and nods on `cue`.
    struct PhoneBotHero: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let status: String
        var cue: MascotCue?

        static let side: CGFloat = 116

        var body: some View {
            let state = store.botState(bot)
            VStack(spacing: 8) {
                face(state)
                    .padding(.top, 8)
                Text(bot.name)
                    .font(Typography.serif(24))
                Text(bot.tag)
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(bot.accent)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(bot.accent.opacity(0.16)))
                HStack(spacing: 6) {
                    StatusDot(color: state.color)
                    Text(status)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                }
                .font(.footnote)
            }
            .frame(maxWidth: .infinity)
            .padding(.bottom, 4)
        }

        @ViewBuilder func face(_ state: MascotState) -> some View {
            if bot.kind == .source {
                BotAvatar(bot: bot)
                    .frame(width: Self.side * 0.8, height: Self.side * 0.8)
            } else {
                MascotView(
                    look: bot.look, state: state, since: bot.isAssistant ? store.stateSince : Date(),
                    cue: cue, interactive: true, characterAnimations: true
                )
                // The head fills the circle and is cut round by it at the bottom, like an
                // avatar; what an animation raises above it (an umbrella, Zzz, numbers) may
                // leave the circle.
                .frame(width: Self.side * 1.2, height: Self.side * 1.2)
                .offset(y: Self.side * 0.06)
                .frame(width: Self.side, height: Self.side)
                .mask(OpenTopCircle())
                .background(
                    Circle().fill(
                        LinearGradient(
                            colors: [bot.accent.opacity(0.26), bot.accent.opacity(0.08)],
                            startPoint: .topLeading, endPoint: .bottomTrailing))
                )
                .overlay(Circle().stroke(bot.accent.opacity(0.45), lineWidth: 1.5))
                .accessibilityLabel(bot.name)
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
