import AssistantCore
import SwiftUI
import WidgetKit

/// The timeline of every complication: the watch app keeps the latest snapshot in the shared
/// App Group and reloads them when a new one comes, and between snapshots each one changes by
/// itself at the moments the day turns (a class starts, a deadline comes close).
struct SnapshotProvider: TimelineProvider {
    func placeholder(in context: Context) -> SnapshotEntry {
        SnapshotEntry(date: Date(), snapshot: Self.sample(now: Date()))
    }

    func getSnapshot(in context: Context, completion: @escaping (SnapshotEntry) -> Void) {
        let now = Date()
        let saved = WatchSnapshot.load(from: ComplicationData.defaults())
        // When choosing a complication with nothing received yet, show what it would look like.
        completion(SnapshotEntry(date: now, snapshot: saved ?? (context.isPreview ? Self.sample(now: now) : nil)))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SnapshotEntry>) -> Void) {
        let now = Date()
        let calendar = DueText.calendar()
        let snapshot = WatchSnapshot.load(from: ComplicationData.defaults())
        let dates = [now] + (snapshot?.changes(after: now, hours: 24, calendar: calendar) ?? [])
        let entries = dates.map { date in
            SnapshotEntry(
                date: date, snapshot: snapshot,
                relevance: snapshot.map { TimelineEntryRelevance(score: $0.moment(at: date, calendar: calendar).relevance) })
        }
        // The watch app reloads the timelines on every new snapshot; this is a fallback.
        completion(Timeline(entries: entries, policy: .after(max(dates.last ?? now, now.addingTimeInterval(30 * 60)))))
    }

    /// Made up, for the place holder on a face being edited.
    static func sample(now: Date) -> WatchSnapshot {
        let calendar = DueText.calendar()
        let start = calendar.date(byAdding: .minute, value: -30, to: now) ?? now
        let end = calendar.date(byAdding: .minute, value: 65, to: now) ?? now
        let span = DueText.format(start, "HH:mm", calendar) + "–" + DueText.format(end, "HH:mm", calendar)
        return WatchSnapshot(
            generatedAt: now, online: true, state: .idle, summary: "",
            todos: [.init(id: "sample", title: "交一次作业", due: "今天", overdue: false, source: "", done: false)],
            courses: .init(title: "今天的课", items: [.init(title: "高等数学", time: span)], mascot: nil),
            health: .init(readiness: .init(value: "8.2", unit: "/ 10"), sleep: .init(value: "7小时12分"), mascot: nil))
    }
}

struct RemainingWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "remaining-todos", provider: SnapshotProvider()) { entry in
            RemainingComplicationView(entry: entry, legacy: ComplicationData.load(from: ComplicationData.defaults()))
                .containerBackground(for: .widget) { Color.clear }
        }
        .configurationDisplayName("剩余待办")
        .description("今天还有几件待办")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular])
    }
}

struct NowWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "watch-now", provider: SnapshotProvider()) { entry in
            NowComplicationView(entry: entry)
                .containerBackground(for: .widget) { Color.clear }
        }
        .configurationDisplayName("此刻")
        .description("现在最要紧的一件事和接下来的安排")
        .supportedFamilies([.accessoryRectangular, .accessoryInline])
    }
}

struct NextClassWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "next-class", provider: SnapshotProvider()) { entry in
            NextClassComplicationView(entry: entry)
                .containerBackground(for: .widget) { Color.clear }
        }
        .configurationDisplayName("下一节课")
        .description("正在上的课和什么时候下课，课后是下一节")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular])
    }
}

struct ReadinessWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "readiness", provider: SnapshotProvider()) { entry in
            ReadinessComplicationView(entry: entry)
                .containerBackground(for: .widget) { Color.clear }
        }
        .configurationDisplayName("准备度")
        .description("健康Bot 算的今日准备度和昨晚睡眠")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular])
    }
}

@main
struct AssistantWidgets: WidgetBundle {
    var body: some Widget {
        NowWidget()
        NextClassWidget()
        ReadinessWidget()
        RemainingWidget()
    }
}
