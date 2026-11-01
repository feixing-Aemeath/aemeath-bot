import AssistantCore
import SwiftUI
import WidgetKit

/// Watch face complication: how many of today's todos are left. The watch app writes the
/// number into the shared App Group whenever the iPhone sends a new snapshot.
struct RemainingEntry: TimelineEntry {
    let date: Date
    /// Nil until the watch received its first snapshot.
    let data: ComplicationData?
}

struct RemainingProvider: TimelineProvider {
    func placeholder(in context: Context) -> RemainingEntry {
        RemainingEntry(date: Date(), data: ComplicationData(remaining: 3, updated: Date()))
    }

    func getSnapshot(in context: Context, completion: @escaping (RemainingEntry) -> Void) {
        completion(current())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<RemainingEntry>) -> Void) {
        // The watch app reloads the timeline on every new snapshot; this is a fallback.
        let next = Date().addingTimeInterval(30 * 60)
        completion(Timeline(entries: [current()], policy: .after(next)))
    }

    func current() -> RemainingEntry {
        RemainingEntry(date: Date(), data: ComplicationData.load(from: ComplicationData.defaults()))
    }
}

struct RemainingView: View {
    @Environment(\.widgetFamily) private var family
    let entry: RemainingEntry

    var count: String { entry.data.map { "\($0.remaining)" } ?? "–" }

    var body: some View {
        switch family {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: 0) {
                    Text(count).font(.title3.weight(.semibold))
                    Text("待办").font(.caption2)
                }
            }
        case .accessoryCorner:
            Text(count)
                .font(.title3.weight(.semibold))
                .widgetLabel { Text("剩余待办") }
        case .accessoryInline:
            Text("剩余 \(count) 件待办")
        default:
            VStack(alignment: .leading, spacing: 2) {
                Text("今日待办").font(.headline)
                Text("还有 \(count) 件").font(.body)
                if let updated = entry.data?.updated {
                    Text(updated, style: .time).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }
}

@main
struct RemainingWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "remaining-todos", provider: RemainingProvider()) { entry in
            RemainingView(entry: entry)
                .containerBackground(for: .widget) { Color.clear }
        }
        .configurationDisplayName("剩余待办")
        .description("今天还有几件待办")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular])
    }
}
