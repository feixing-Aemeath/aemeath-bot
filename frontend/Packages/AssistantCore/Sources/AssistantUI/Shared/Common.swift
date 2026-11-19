#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// A Bot's page opens at its newest line and stays there as lines come in, like a chat. A
    /// page shorter than the window starts at the top, its face first: anchored to the bottom
    /// as a whole, a Bot with nothing new today sat under half a window of empty page.
    struct ChatScrollAnchor: ViewModifier {
        func body(content: Content) -> some View {
            if #available(iOS 18.0, macOS 15.0, *) {
                content
                    .defaultScrollAnchor(.bottom, for: .initialOffset)
                    .defaultScrollAnchor(.bottom, for: .sizeChanges)
                    .defaultScrollAnchor(.top, for: .alignment)
            } else {
                content.defaultScrollAnchor(.bottom)
            }
        }
    }

    /// A small colored circle for states.
    struct StatusDot: View {
        let color: Color

        var body: some View {
            Circle().fill(color).frame(width: 8, height: 8)
        }
    }

    extension PluginState {
        var color: Color {
            switch self {
            case .enabled: return .green
            case .disabled, .unsupported: return .gray
            case .error: return .red
            }
        }

        /// A dot only when the plugin needs a look: gray while off, red when broken. Running is
        /// the normal state and has none (every row was green, which said nothing).
        var dotColor: Color? {
            switch self {
            case .enabled: return nil
            case .disabled, .unsupported: return .gray
            case .error: return .red
            }
        }
    }

    extension MascotState {
        var color: Color {
            switch self {
            case .idle, .done: return .green
            case .working: return .blue
            case .error: return .red
            case .offline: return .gray
            }
        }

        /// A dot only when it says something: blue while working, red after an error, gray
        /// offline; none when all is well.
        var dotColor: Color? {
            switch self {
            case .idle, .done: return nil
            default: return color
            }
        }
    }

    /// Something new in a Bot's chat: the same blue dot for every Bot, so a red Bot's dot does
    /// not read as an error.
    struct UnreadDot: View {
        let count: Int

        var body: some View {
            Circle()
                .fill(Palette.unread)
                .frame(width: 8, height: 8)
                .accessibilityLabel("\(count) 条新消息")
        }
    }

    /// The assistant's todos left today, quiet beside its row like a mailbox count.
    struct RemainingCount: View {
        let count: Int

        var body: some View {
            Text("\(count)")
                .font(.caption.weight(.medium))
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .accessibilityLabel("今天还有 \(count) 件待办")
        }
    }
    /// Whether the todo reached the 「AI 待办」 list in Reminders.
    struct SyncLabel: View {
        let state: SyncState

        var body: some View {
            switch state {
            case .synced:
                Label(state.text, systemImage: "checkmark.icloud")
                    .foregroundStyle(.green)
            case .pending:
                Label(state.text, systemImage: "arrow.triangle.2.circlepath.icloud")
                    .foregroundStyle(.orange)
            case .failed(let error):
                Label(state.text, systemImage: "exclamationmark.icloud")
                    .foregroundStyle(.red)
                    .help(RunText.error(error) ?? error)
            case .appOnly:
                Label(state.text, systemImage: "iphone.slash")
                    .foregroundStyle(.secondary)
            }
        }
    }
    struct InfoRow: View {
        let label: String
        let value: String

        var body: some View {
            HStack(alignment: .firstTextBaseline) {
                Text(label).foregroundStyle(.secondary).frame(width: 60, alignment: .leading)
                Text(value).selectableText()
            }
            .font(.callout)
        }
    }

    /// Online / offline and mock data, at the bottom of the sidebar (Mac) and the Bot list (iPhone).
    struct ConnectionBadge: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            HStack(spacing: 6) {
                StatusDot(color: store.online ? .green : .red)
                Text(store.authorizationIssue ?? (store.online ? "Mac 在线" : "连不上 Mac"))
                    .font(.caption)
                if store.connection.useMockData {
                    Text("模拟数据")
                        .font(.caption2)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(Color.orange.opacity(0.2)))
                }
                Spacer()
            }
        }
    }
#endif
