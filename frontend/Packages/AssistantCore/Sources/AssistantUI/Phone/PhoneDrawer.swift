#if os(iOS)
    import AssistantCore
    import SwiftUI

    /// Every Bot, under the page: a row each with its round face, name and what it looks after
    /// (the assistant first, then the characters, then below a line 知识库Bot, who keeps the
    /// knowledge base); the open one on a gray rounded row; settings and whether the Mac is
    /// reachable at the bottom. The message sources are in 设置, out of the way of a thumb.
    struct PhoneDrawer: View {
        @EnvironmentObject private var store: AppStore
        let chosen: String
        let choose: (String) -> Void
        let openSettings: () -> Void

        var body: some View {
            let bots = store.bots.filter { $0.kind != .source }
            let shelf = bots.filter { $0.kind == .knowledge }
            VStack(alignment: .leading, spacing: 0) {
                Text("Aemeath Bot")
                    .font(Typography.serif(24))
                    .padding(.horizontal, 20)
                    .padding(.top, 12)
                    .padding(.bottom, 8)
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        header("Bots")
                        ForEach(bots.filter { $0.kind != .knowledge }) { bot in
                            row(bot)
                        }
                        if !shelf.isEmpty {
                            // A line between the Bots of the day and the one who keeps the knowledge base.
                            Palette.hairline
                                .frame(height: 1)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                            ForEach(shelf) { bot in
                                row(bot)
                            }
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.bottom, 12)
                }
                Divider().overlay(Palette.hairline)
                HStack(spacing: 12) {
                    Button(action: openSettings) {
                        Image(systemName: "gearshape")
                            .font(.system(size: 20))
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("设置")
                    ConnectionBadge()
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
            }
            .background(Palette.sidebar)
        }

        func header(_ title: String) -> some View {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 10)
                .padding(.top, 6)
                .padding(.bottom, 2)
        }

        func row(_ bot: Bot) -> some View {
            Button {
                choose(bot.id)
            } label: {
                PhoneBotRow(bot: bot)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 12)
                            .fill(bot.id == chosen ? Palette.selection : .clear))
                    .contentShape(RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(bot.id == chosen ? .isSelected : [])
        }
    }

    /// A Bot in the list: its face, its name and what it looks after; bold with a dot while it
    /// has said something new since its page was last open, and the assistant's count of
    /// today's todos left.
    struct PhoneBotRow: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        var preview: String? = nil

        var body: some View {
            let unread = store.unread(bot)
            HStack(spacing: 12) {
                BotAvatar(bot: bot, state: store.botState(bot))
                    .frame(width: bot.kind == .source ? 34 : 42, height: bot.kind == .source ? 34 : 42)
                    .frame(width: 42)
                VStack(alignment: .leading, spacing: 2) {
                    Text(bot.name)
                        .font(.body.weight(unread > 0 ? .bold : .medium))
                        .lineLimit(1)
                    Text(preview ?? bot.intro)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                if unread > 0 {
                    Circle()
                        .fill(bot.accent)
                        .frame(width: 8, height: 8)
                        .accessibilityLabel("\(unread) 条新消息")
                }
                if bot.isAssistant, store.remainingToday > 0 {
                    Text("\(store.remainingToday)")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(bot.accent))
                        .accessibilityLabel("今天还有 \(store.remainingToday) 件待办")
                }
            }
        }
    }
#endif
