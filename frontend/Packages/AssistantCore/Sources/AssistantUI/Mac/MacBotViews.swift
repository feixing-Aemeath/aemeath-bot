#if os(macOS)
    import AppKit
    import AssistantCore
    import SwiftUI

    /// A Bot in the sidebar: its face, its name and what it looks after; bold with a dot while
    /// it has said something new since its page was last open. The face comes alive
    /// (blinks, follows the mouse) under the mouse only: on the Mac an animation inside a list
    /// row lays out the whole list every frame, and the chosen Bot is already alive in its
    /// page's header.
    struct BotRow: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        @State private var hovering = false

        var body: some View {
            let unread = store.unread(bot)
            HStack(spacing: 10) {
                BotAvatar(bot: bot, state: store.botState(bot), live: hovering)
                    .frame(width: bot.kind == .source ? 28 : 36, height: bot.kind == .source ? 28 : 36)
                VStack(alignment: .leading, spacing: 1) {
                    Text(bot.name)
                        .font(.body.weight(unread > 0 ? .bold : .medium))
                        .lineLimit(1)
                    Text(bot.intro)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                if unread > 0 {
                    Circle()
                        .fill(bot.accent)
                        .frame(width: 8, height: 8)
                        .help("\(unread) 条新消息")
                }
                if bot.isAssistant, store.remainingToday > 0 {
                    Text("\(store.remainingToday)")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(bot.accent))
                        .help("今天还有 \(store.remainingToday) 件待办")
                }
            }
            .padding(.vertical, 3)
            .pointerHover($hovering)
        }
    }

    /// A mascot whose eyes follow the mouse in the window. Only this reads the pointer, so a
    /// mouse move redraws the mascots and nothing around them.
    struct LiveMascot: View {
        @Environment(PointerTracker.self) private var pointer: PointerTracker?
        let look: StickerLook
        var state: MascotState = .idle
        var since = Date()
        var cue: MascotCue?
        var interactive = false
        var liveliness: MascotLiveliness = .full
        var characterAnimations = false

        var body: some View {
            MascotView(
                look: look, state: state, since: since, pointer: pointer?.location, cue: cue,
                interactive: interactive, liveliness: liveliness, characterAnimations: characterAnimations)
        }
    }

    /// The top of a Bot's page: the character peeking up from the bottom of a band in its
    /// color, its name, what it looks after and how it is doing. The character is alive: it
    /// blinks, looks at the mouse, hops when the mouse comes over, plays when clicked and nods
    /// on `cue`.
    struct BotHero: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        let status: String
        var cue: MascotCue?

        var state: MascotState { store.botState(bot) }

        var body: some View {
            HStack(alignment: .bottom, spacing: 16) {
                face
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(bot.name)
                            .font(Typography.pageTitle)
                        Text(bot.tag)
                            .font(.callout.weight(.medium))
                            .foregroundStyle(bot.accent)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(bot.accent.opacity(0.16)))
                    }
                    HStack(spacing: 6) {
                        StatusDot(color: state.color)
                        Text(status)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    .font(.callout)
                }
                .padding(.bottom, 20)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 20)
            .frame(height: 150, alignment: .bottom)
            .background(
                LinearGradient(
                    colors: [bot.accent.opacity(0.22), bot.accent.opacity(0.05)],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
            .clipShape(RoundedRectangle(cornerRadius: 16))
        }

        @ViewBuilder var face: some View {
            if bot.kind == .source {
                BotAvatar(bot: bot)
                    .frame(width: 84, height: 84)
                    .padding(.bottom, 24)
            } else {
                LiveMascot(
                    look: bot.look, state: state, since: bot.isAssistant ? store.stateSince : Date(),
                    cue: cue, interactive: true, characterAnimations: true
                )
                .frame(width: 150, height: 150)
                .help("点我一下")
            }
        }
    }

#endif
