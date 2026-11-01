#if os(iOS)
    import AssistantCore
    import SwiftUI

    /// The shared cards and conversation in a wider page, with an input that stays above the
    /// keyboard. Sources only show what they read; every action is available by touch.
    struct PadBotPage: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @State private var cardsShown = true
        @State private var draft = ""
        @State private var cue: MascotCue?
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        static let bottom = "pad-bottom"

        var plugin: PluginView? { store.plugin(bot.id) }

        var body: some View {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        PadBotHero(bot: bot, status: store.statusLine(bot), cue: cue)
                        if cardsShown {
                            VStack(alignment: .leading, spacing: 12) {
                                BotCards(bot: bot, minWidth: PadPresentation.cardMinimumWidth, wideLayout: true)
                            }
                            .transition(reduceMotion ? .opacity : .opacity.combined(with: .move(edge: .top)))
                        }
                        CardsHandle(shown: $cardsShown, count: BotCards.count(of: bot, in: store))
                            .frame(minHeight: 44)
                            .hoverEffect(.highlight)
                        WavyDivider(tint: bot.accent)
                        ChatTimeline(bot: bot)
                        Color.clear.frame(height: 1).id(Self.bottom)
                    }
                    .padding(.horizontal, 24)
                    .padding(.vertical, 16)
                }
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await store.refresh() }
                .background(Palette.page)
                .safeAreaInset(edge: .bottom) { input }
                .onChange(of: store.chats[bot.id]?.count) { old, new in
                    guard let old, let new, new > old else { return }
                    withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
                        proxy.scrollTo(Self.bottom, anchor: .bottom)
                    }
                }
            }
            .navigationTitle(bot.name)
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                store.markSeen(bot)
                cue = MascotCue(.greet)
            }
            .onChange(of: store.messages(bot).count) {
                store.markSeen(bot)
                if store.messages(bot).last?.author == .bot { cue = MascotCue(.talk) }
            }
            .task(id: CardsLoad(state: plugin?.state, refreshed: store.lastRefresh, plugins: bot.pluginIDs)) {
                for id in bot.pluginIDs { await store.loadCards(id) }
            }
            .task { await store.loadChat(bot) }
        }

        @ViewBuilder var input: some View {
            if bot.kind == .source {
                Text(PadPresentation.sourceNotice)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(Palette.page)
            } else {
                ChatInput(
                    placeholder: PadPresentation.placeholder(botName: bot.name, replying: store.isReplying(bot)),
                    text: $draft, disabled: store.isReplying(bot), returnSends: true
                ) {
                    let text = draft
                    draft = ""
                    cue = MascotCue(.greet)
                    Task {
                        await store.send(text, to: bot)
                        if store.message != nil { draft = text }
                    }
                }
            }
        }
    }

    /// The Mac's peeking character and the phone's tag, on the same warm page colors.
    struct PadBotHero: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let status: String
        var cue: MascotCue?

        var body: some View {
            HStack(spacing: 16) {
                face
                VStack(alignment: .leading, spacing: 8) {
                    Text(bot.name).font(Typography.pageTitle)
                    Text(bot.tag)
                        .font(.callout.weight(.medium))
                        .foregroundStyle(bot.accent)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 3)
                        .background(Capsule().fill(bot.accent.opacity(0.16)))
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        StatusDot(color: store.botState(bot).color)
                        Text(status).foregroundStyle(.secondary)
                    }
                    .font(.callout)
                }
                .padding(.vertical, 20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 20)
            .background(
                LinearGradient(
                    colors: [bot.accent.opacity(0.22), bot.accent.opacity(0.05)],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
            .clipShape(RoundedRectangle(cornerRadius: 18))
        }

        @ViewBuilder var face: some View {
            if bot.kind == .source {
                BotAvatar(bot: bot)
                    .frame(width: 84, height: 84)
            } else {
                let place = FacePlacement.header(bot.look.resolved(in: .main), side: 136)
                MascotView(
                    look: bot.look, state: store.botState(bot), since: bot.isAssistant ? store.stateSince : Date(),
                    cue: cue, interactive: true, characterAnimations: true
                )
                .frame(width: place.drawn, height: place.drawn)
                .position(place.center)
                .frame(width: 136, height: 136)
                .accessibilityLabel(bot.name)
            }
        }
    }
#endif
