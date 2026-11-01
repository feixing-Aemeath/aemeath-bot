#if os(watchOS)
    import AssistantCore
    import SwiftUI

    /// The first page on the watch, 助理Bot's: the weather in a line, then today's todos from the
    /// iPhone; tap one to tick it off.
    public struct WatchTodayView: View {
        @EnvironmentObject private var store: WatchStore

        public init() {}

        var updated: String {
            guard let snapshot = store.snapshot else { return "还没有从 iPhone 收到数据" }
            let calendar = DueText.calendar()
            let when = RelativeTime.describe(snapshot.generatedAt, now: Date(), calendar: calendar)
            return snapshot.online ? "更新于 \(when)" : "更新于 \(when) · iPhone 连不上 Mac"
        }

        public var body: some View {
            List {
                WatchBotHeader(
                    look: StickerCatalog.main.assistant,
                    name: StickerCatalog.main.assistant.name(or: Bot.assistantName),
                    detail: "还有 \(store.snapshot?.remaining ?? 0) 件 · \(updated)",
                    state: store.snapshot?.state ?? .offline)
                if let snapshot = store.snapshot, let weather = snapshot.weather {
                    // Redrawn every minute, so tomorrow's forecast comes in at 19:00.
                    TimelineView(.everyMinute) { context in
                        if let shown = weather.shown(
                            at: context.date, since: snapshot.generatedAt, calendar: DueText.calendar())
                        {
                            WatchWeatherRow(shown: shown, ink: StickerCatalog.main.assistant.ink)
                        }
                    }
                }
                if let snapshot = store.snapshot {
                    if snapshot.todos.isEmpty {
                        Text("今天没有待办").font(.footnote)
                    }
                    ForEach(snapshot.todos) { item in
                        Button {
                            store.toggle(item)
                        } label: {
                            WatchTodoRow(item: item)
                        }
                    }
                }
                Button {
                    store.refresh()
                } label: {
                    if store.waiting {
                        ProgressView()
                    } else {
                        Label("让 iPhone 刷新", systemImage: "arrow.clockwise")
                    }
                }
                .disabled(store.waiting)
                if let message = store.message {
                    Text(message).font(.caption2).foregroundStyle(.secondary)
                }
            }
            .watchPage(StickerCatalog.main.assistant)
        }
    }

    /// 「11–23°C」 by the day's weather symbol, the sky and rain chance under it, and today's
    /// 「出门提醒」 (umbrella, coat) in 助理's color; tomorrow's in the evening.
    struct WatchWeatherRow: View {
        let shown: WatchSnapshot.Weather.Shown
        let ink: Color

        var body: some View {
            HStack(spacing: 8) {
                if let symbol = shown.day.symbol {
                    Image(systemName: symbol)
                        .symbolRenderingMode(.multicolor)
                        .font(.title2)
                        .frame(width: 34)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text((shown.isToday ? "" : "明天 ") + shown.day.temperatures + (shown.day.unit ?? ""))
                        .font(.headline.monospacedDigit())
                    if let label = shown.day.label {
                        Text(label).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                    }
                    if !shown.advice.isEmpty {
                        Text(shown.advice.joined(separator: " · "))
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(ink)
                            .lineLimit(2)
                    }
                }
            }
        }
    }

    struct WatchTodoRow: View {
        let item: WatchSnapshot.Item

        var body: some View {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: item.done ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(item.done ? Color.green : Color.secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.title)
                        .font(.footnote)
                        .strikethrough(item.done)
                        .lineLimit(3)
                    Text(item.due)
                        .font(.caption2)
                        .foregroundStyle(item.overdue ? Color.red : Color.secondary)
                }
            }
        }
    }
#endif
