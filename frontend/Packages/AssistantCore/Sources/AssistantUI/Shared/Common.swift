#if !os(watchOS)
    import AssistantCore
    import SwiftUI

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
                Text(store.online ? "Mac 在线" : "连不上 Mac")
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
