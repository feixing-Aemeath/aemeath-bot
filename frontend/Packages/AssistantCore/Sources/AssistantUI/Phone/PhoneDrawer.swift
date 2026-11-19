#if os(iOS)
    import AssistantCore
    import SwiftUI

    /// Every Bot, under the page: 仪表盘 first, then a row each with its round face, name and what
    /// it looks after (the assistant first, then the characters, then below a line 知识库Bot, who
    /// keeps the knowledge base); the open one on a gray rounded row; settings and whether the
    /// Mac is reachable at the bottom. The message sources are in 设置 and on 仪表盘, out of the
    /// way of a thumb.
    struct PhoneDrawer: View {
        @EnvironmentObject private var store: AppStore
        let chosen: String
        let choose: (String) -> Void
        let openSettings: () -> Void

        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                Text("Aemeath Bot")
                    .font(Typography.serif(24))
                    .padding(.horizontal, 20)
                    .padding(.top, 12)
                    .padding(.bottom, 8)
                BotSidebarList(showsSources: false) { dashboard } row: { row($0) }
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

        var dashboard: some View {
            Button {
                choose(DashboardPage.id)
            } label: {
                DashboardRow(side: 42)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 12)
                            .fill(chosen == DashboardPage.id ? Palette.selection : .clear))
                    .contentShape(RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(chosen == DashboardPage.id ? .isSelected : [])
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

    /// A Bot in the list, like a chat list: its face, its name, and the last thing it said (what
    /// it looks after until it has said something) with when on the right; the name semibold with
    /// a blue dot while there is something new since its page was last open, and the assistant's
    /// count of today's todos left.
    struct PhoneBotRow: View {
        @EnvironmentObject private var store: AppStore
        let bot: Bot
        var preview: String? = nil

        var body: some View {
            let unread = store.unread(bot)
            let last = store.messages(bot).last { $0.author == .bot && !$0.preview.isEmpty }
            HStack(spacing: 12) {
                BotAvatar(bot: bot, state: store.botState(bot))
                    .frame(width: bot.kind == .source ? 34 : 42, height: bot.kind == .source ? 34 : 42)
                    .frame(width: 42)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(store.displayName(bot))
                            .font(.body.weight(unread > 0 ? .semibold : .regular))
                            .lineLimit(1)
                        Spacer(minLength: 4)
                        if let last {
                            Text(BotTimeline.listTime(last.date, now: Date(), calendar: store.calendar))
                                .font(.caption)
                                .monospacedDigit()
                                .foregroundStyle(.tertiary)
                        }
                    }
                    Text(preview ?? last?.preview ?? bot.tag)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                if bot.isAssistant, store.remainingToday > 0 {
                    RemainingCount(count: store.remainingToday)
                }
                if unread > 0 {
                    UnreadDot(count: unread)
                }
            }
        }
    }
#endif
