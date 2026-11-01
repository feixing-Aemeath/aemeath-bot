#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// Claude Code / Codex: available or not.
    struct AgentAvailability: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            let names = (store.status?.agents ?? [:]).keys.sorted()
            HStack(spacing: 14) {
                if names.isEmpty {
                    Text("Agent：未知").foregroundStyle(.secondary)
                }
                ForEach(names, id: \.self) { name in
                    let available = store.online && (store.status?.agents[name]?.available ?? false)
                    HStack(spacing: 5) {
                        StatusDot(color: available ? .green : .gray)
                        Text(Names.harness(name))
                        Text(available ? "可用" : "不可用").foregroundStyle(.secondary)
                    }
                    .font(.callout)
                }
            }
        }
    }

    /// 「立即运行一次」: starts todo extraction; disabled while a job runs.
    struct RunNowButton: View {
        @EnvironmentObject private var store: AppStore
        @State private var starting = false

        var body: some View {
            HStack(spacing: 12) {
                Button {
                    starting = true
                    Task {
                        await store.runNow()
                        starting = false
                    }
                } label: {
                    Label("立即运行一次", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(!store.online || starting || store.isJobRunning)
                if store.isJobRunning || starting {
                    ProgressView().controlSize(.small)
                    Text("运行中…").foregroundStyle(.secondary)
                }
            }
        }
    }

    /// One run in two lines, for narrow screens.
    struct RunRow: View {
        let run: Run
        let calendar: Calendar

        var color: Color {
            switch run.status {
            case .running: return .blue
            case .succeeded: return .green
            case .failed: return .red
            case .cancelled: return .gray
            }
        }

        var body: some View {
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(Names.job(run.job)).font(.body.weight(.medium))
                    Spacer()
                    Text(RunText.status(run.status)).foregroundStyle(color)
                }
                HStack(spacing: 8) {
                    Text(DueText.format(run.startedAt, "M月d日 HH:mm", calendar))
                    Text(Names.harness(run.harness))
                    if run.attempt > 1 {
                        Text("第 \(run.attempt) 次")
                    }
                    Text(RunText.duration(run, now: Date()))
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                if let reason = RunText.error(run.error) {
                    Text(reason).font(.caption).foregroundStyle(.red)
                }
            }
            .padding(.vertical, 2)
        }
    }

    /// The assistant's second card: which Agents are available, the latest todo extraction and
    /// a "run once now" button. (Whether the Mac is online is in the page's header.)
    struct AgentCard: View {
        @EnvironmentObject private var store: AppStore

        var lastRunText: String {
            AgentText.lastRun(
                store.runs.first { $0.job == "extract_todos" } ?? store.status?.lastRun, now: Date(),
                calendar: store.calendar)
        }

        var body: some View {
            // The button beside the facts on the Mac, under them where that does not fit.
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 16) {
                    facts
                    Spacer()
                    RunNowButton()
                }
                VStack(alignment: .leading, spacing: 10) {
                    facts
                    RunNowButton()
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
        }

        var facts: some View {
            VStack(alignment: .leading, spacing: 6) {
                CardTitle("Agent", icon: "cpu")
                Text(lastRunText).font(.callout).foregroundStyle(.secondary)
                AgentAvailability()
            }
        }
    }
#endif
