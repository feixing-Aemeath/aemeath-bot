import AssistantCore
import SwiftUI
import WidgetKit

struct PhoneEntry: TimelineEntry {
    var date: Date
    var snapshot: WatchSnapshot?
    var content: PhoneWidgetContent { PhoneWidgetContent(snapshot: snapshot, now: date, calendar: .current) }
}

struct PhoneProvider: TimelineProvider {
    func placeholder(in context: Context) -> PhoneEntry {
        PhoneEntry(date: Date(), snapshot: sample())
    }
    func getSnapshot(in context: Context, completion: @escaping (PhoneEntry) -> Void) {
        completion(
            PhoneEntry(
                date: Date(),
                snapshot: PhoneWidgetSnapshot.load(from: ComplicationData.defaults())
                    ?? (context.isPreview ? sample() : nil)))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<PhoneEntry>) -> Void) {
        let now = Date()
        let snapshot = PhoneWidgetSnapshot.load(from: ComplicationData.defaults())
        var dates = [now] + (snapshot?.changes(after: now, hours: 24, calendar: .current) ?? [])
        if let snapshot {
            let expiry = snapshot.generatedAt.addingTimeInterval(86400)
            if expiry > now { dates.append(expiry) }
        }
        dates = Array(Set(dates)).sorted()
        completion(
            Timeline(
                entries: dates.map { PhoneEntry(date: $0, snapshot: snapshot) },
                policy: .after(now.addingTimeInterval(30 * 60))))
    }
    private func sample() -> WatchSnapshot {
        WatchSnapshot(
            generatedAt: Date(), online: true, state: .idle, summary: "",
            todos: [
                .init(id: "preview", title: "准备明天的材料", due: "今天", overdue: false, source: "", done: false)
            ])
    }
}

struct PhoneWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: PhoneEntry
    private var content: PhoneWidgetContent { entry.content }
    private let accent = Color(red: 0.68, green: 0.30, blue: 0.39)

    var body: some View {
        Group {
            switch family {
            case .accessoryCircular:
                VStack(spacing: 1) {
                    Text(content.remaining.map(String.init) ?? "–").font(
                        .system(.title, design: .rounded).weight(.semibold))
                    Text(content.stale ? "待更新" : "待办").font(.caption2)
                }
                .accessibilityLabel(content.remaining.map { "今天还剩 \($0) 件待办" } ?? content.detail)
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Text(content.title).font(.headline).lineLimit(2)
                    Text(content.detail).font(.caption).lineLimit(1)
                }
            case .systemMedium:
                HStack(alignment: .top, spacing: 14) {
                    lead.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    Divider()
                    Link(destination: PhoneDestination.dashboard.url) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(content.stale ? "等待更新" : "今天的待办").font(.caption).foregroundStyle(.secondary)
                            if content.stale {
                                Text(content.detail).font(.caption)
                            } else if content.todos.isEmpty {
                                Text("今天的待办都完成了").font(.subheadline)
                            } else {
                                ForEach(content.todos) { item in
                                    HStack(alignment: .top, spacing: 5) {
                                        Image(systemName: "circle").font(.caption2)
                                        Text(item.title).font(.subheadline).lineLimit(2)
                                    }
                                    .foregroundStyle(item.overdue ? Color.red : Color.primary)
                                    .accessibilityLabel(item.title + (item.overdue ? "，已逾期" : ""))
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                }
            default: lead
            }
        }
        .widgetURL(family == .accessoryCircular ? PhoneDestination.dashboard.url : content.destination.url)
        .privacySensitive()
        .containerBackground(for: .widget) { Color(uiColor: .secondarySystemBackground) }
    }

    private var lead: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(content.stale ? "等待更新" : "此刻", systemImage: content.stale ? "clock.arrow.circlepath" : "sparkle")
                .font(.caption).foregroundStyle(accent)
            Text(content.title).font(.system(.headline, design: .serif)).lineLimit(3)
            Text(content.detail).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            Spacer(minLength: 0)
            Text(content.remaining.map { "今天还剩 \($0) 件待办" } ?? "打开 App 刷新")
                .font(.caption).foregroundStyle(accent).lineLimit(1).minimumScaleFactor(0.8)
        }
    }
}

@main
struct AssistantPhoneWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "phone-now", provider: PhoneProvider()) { PhoneWidgetView(entry: $0) }
            .configurationDisplayName("此刻与待办")
            .description("现在最该看的一件事，和今天剩下的待办。")
            .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular])
    }
}
