#if os(macOS)
    import AssistantCore
    import SwiftUI

    /// Every run of the Agent (which Agent, when, success or failure and why), from the
    /// assistant's toolbar.
    struct RunsSheet: View {
        @EnvironmentObject private var store: AppStore
        @Environment(\.dismiss) private var dismiss

        var body: some View {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("运行记录").font(.title2.weight(.semibold))
                    Spacer()
                    Button("完成") { dismiss() }
                        .keyboardShortcut(.defaultAction)
                }
                ScrollView {
                    RunTable(runs: store.runs, calendar: store.calendar)
                }
            }
            .padding(20)
            .frame(minWidth: 780, minHeight: 420)
        }
    }

    /// Runs, newest first: time, task, Agent, duration, result and the reason of a failure.
    struct RunTable: View {
        let runs: [Run]
        let calendar: Calendar

        var body: some View {
            VStack(spacing: 0) {
                row(time: "开始时间", job: "任务", agent: "Agent", duration: "用时", result: "结果", reason: "原因")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 6)
                Divider()
                if runs.isEmpty {
                    Text("还没有运行记录").foregroundStyle(.secondary).padding(12)
                }
                ForEach(runs) { run in
                    row(
                        time: DueText.format(run.startedAt, "M月d日 HH:mm", calendar),
                        job: Names.job(run.job),
                        agent: run.attempt > 1 ? "\(Names.harness(run.harness))（第 \(run.attempt) 次）" : Names.harness(run.harness),
                        duration: RunText.duration(run, now: Date()),
                        result: RunText.status(run.status),
                        reason: RunText.error(run.error) ?? "")
                        .foregroundStyle(color(run.status))
                        .padding(.vertical, 5)
                    Divider()
                }
            }
            .padding(.horizontal, 12)
            .background(RoundedRectangle(cornerRadius: 10).stroke(Color.secondary.opacity(0.2)))
        }

        func color(_ status: RunStatus) -> Color {
            switch status {
            case .running: return .blue
            case .succeeded: return .primary
            case .failed: return .red
            case .cancelled: return .secondary
            }
        }

        func row(
            time: String, job: String, agent: String, duration: String, result: String, reason: String
        ) -> some View {
            HStack(spacing: 8) {
                Text(time).frame(width: 110, alignment: .leading)
                Text(job).frame(width: 140, alignment: .leading)
                Text(agent).frame(width: 150, alignment: .leading)
                Text(duration).frame(width: 90, alignment: .leading)
                Text(result).frame(width: 60, alignment: .leading)
                Text(reason).frame(maxWidth: .infinity, alignment: .leading)
            }
            .lineLimit(1)
            .font(.callout)
        }
    }
#endif
