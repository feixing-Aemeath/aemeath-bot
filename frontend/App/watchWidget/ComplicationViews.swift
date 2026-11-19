import AssistantCore
import SwiftUI
import WidgetKit

// What the watch face draws. These views take a snapshot and a time, and ask AssistantCore what
// to say, so a face and the App never disagree. The file is also compiled into the watch App,
// whose debug gallery (`-watch.gallery 1`) draws them in the simulator.

/// One state of a complication.
struct SnapshotEntry: TimelineEntry {
    let date: Date
    /// Nil until the watch received its first snapshot.
    let snapshot: WatchSnapshot?
    var relevance: TimelineEntryRelevance? = nil
}

extension WatchSnapshot {
    /// The Bot color for a mark on a face that shows color (tinted faces replace it).
    fileprivate func tint(_ mascot: PluginMascot?) -> Color {
        guard let color = mascot.flatMap({ MascotColor(hex: $0.accent) }).map(\.onBlack) else { return .white }
        return Color(red: color.red, green: color.green, blue: color.blue)
    }

    fileprivate var courseTint: Color { tint((courses ?? tomorrowCourses)?.mascot) }
    fileprivate var healthTint: Color { tint(health?.mascot) }
    /// 助理's, which no plugin declares: the App's own (stickers/app.json).
    fileprivate static let assistantTint = Color(red: 0.91, green: 0.42, blue: 0.60)

    fileprivate func tint(for moment: WatchMoment) -> Color {
        if moment.urgent { return .orange }
        switch moment.bot {
        case .assistant: return Self.assistantTint
        case .courses: return courseTint
        case .health: return healthTint
        }
    }
}

private func time(_ date: Date) -> String { DueText.format(date, "HH:mm", DueText.calendar()) }

/// The rows every rectangular complication shares: a tinted first line, the thing itself, one
/// line under it.
private struct ComplicationRows: View {
    let kicker: String
    let title: String
    var detail: String? = nil
    var tint: Color = .white
    var interval: ClosedRange<Date>? = nil
    /// The third line: how long until this, counting by itself on the face.
    var until: Date? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(kicker)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(tint)
                .widgetAccentable()
                .lineLimit(1)
            Text(title)
                .font(.system(size: 17, weight: .bold))
                .lineLimit(detail == nil && interval == nil && until == nil ? 2 : 1)
                .minimumScaleFactor(0.75)
            if let interval {
                ProgressView(timerInterval: interval, countsDown: false, label: { EmptyView() }, currentValueLabel: { EmptyView() })
                    .progressViewStyle(.linear)
                    .tint(tint)
                    .scaleEffect(x: 1, y: 0.5, anchor: .center)
            } else if let detail {
                Text(detail).font(.system(size: 13)).foregroundStyle(.secondary).lineLimit(1)
            } else if let until {
                Text("还有 \(Text(until, style: .relative))").font(.system(size: 13)).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// 此刻: the same thing the App's first page leads with. For the Smart Stack and the big slot.
struct NowComplicationView: View {
    @Environment(\.widgetFamily) private var faceFamily
    let entry: SnapshotEntry
    /// The family to draw when not on a face: the gallery's, which cannot set the environment.
    var galleryFamily: WidgetFamily? = nil
    var family: WidgetFamily { galleryFamily ?? faceFamily }

    var body: some View {
        Group {
            if let snapshot = entry.snapshot {
                let moment = snapshot.moment(at: entry.date, calendar: DueText.calendar())
                if family == .accessoryInline {
                    Text([moment.kicker, moment.title].joined(separator: " · "))
                } else {
                    ComplicationRows(
                        kicker: [moment.kicker, moment.detail].compactMap { $0 }.joined(separator: " · "),
                        title: moment.title,
                        detail: moment.lines.first.map { [$0.time, $0.text].compactMap { $0 }.joined(separator: " ") },
                        tint: snapshot.tint(for: moment),
                        interval: moment.kind == .inClass
                            ? snapshot.currentClass(at: entry.date, calendar: DueText.calendar()).map { $0.start...$0.end } : nil)
                }
            } else {
                if family == .accessoryInline {
                    Text("打开 iPhone 上的 App")
                } else {
                    ComplicationRows(kicker: "此刻", title: "打开 iPhone 上的 App", detail: nil)
                }
            }
        }
        .widgetURL(WatchPage.now.url)
    }
}

/// 下一节课: the class going on with the time passing around it, else the next one.
struct NextClassComplicationView: View {
    @Environment(\.widgetFamily) private var faceFamily
    let entry: SnapshotEntry
    /// The family to draw when not on a face: the gallery's, which cannot set the environment.
    var galleryFamily: WidgetFamily? = nil
    var family: WidgetFamily { galleryFamily ?? faceFamily }

    var slot: WatchClass? { entry.snapshot?.currentClass(at: entry.date, calendar: DueText.calendar()) }

    var body: some View {
        content.widgetURL(WatchPage.courses.url)
    }

    @ViewBuilder var content: some View {
        let tint = entry.snapshot?.courseTint ?? .white
        switch (family, slot) {
        case (.accessoryCircular, let slot?):
            if slot.isRunning(at: entry.date) {
                ProgressView(timerInterval: slot.start...slot.end, countsDown: false) {
                    EmptyView()
                } currentValueLabel: {
                    Text(time(slot.end)).font(.system(size: 13, weight: .semibold))
                }
                .progressViewStyle(.circular)
                .tint(tint)
            } else {
                ZStack {
                    AccessoryWidgetBackground()
                    VStack(spacing: -1) {
                        Text(slot.isToday ? "下节" : "明天").font(.system(size: 11, weight: .semibold)).widgetAccentable().foregroundStyle(tint)
                        Text(time(slot.start)).font(.system(size: 14, weight: .bold))
                    }
                }
            }
        case (.accessoryCircular, nil):
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: -1) {
                    Image(systemName: "graduationcap.fill").widgetAccentable().foregroundStyle(tint)
                    Text("无课").font(.system(size: 12, weight: .semibold))
                }
            }
        case (.accessoryCorner, _):
            Text(slot.map { time($0.isRunning(at: entry.date) ? $0.end : $0.start) } ?? "无课")
                .font(.system(size: 16, weight: .semibold))
                .widgetCurvesContent()
                .widgetLabel {
                    // The time alone would not say whether a class starts or ends then.
                    Text(slot.map { ($0.isRunning(at: entry.date) ? "下课 · " : "上课 · ") + $0.title } ?? "今天没有课")
                }
        case (.accessoryInline, let slot?):
            Text((slot.isRunning(at: entry.date) ? "\(time(slot.end)) 下课" : (slot.isToday ? time(slot.start) : "明天 \(time(slot.start))")) + " · " + slot.title)
        case (.accessoryInline, nil):
            Text("今天没有课")
        case (_, let slot?):
            if slot.isRunning(at: entry.date) {
                ComplicationRows(
                    kicker: slot.isOwnEvent ? "正在进行 · \(time(slot.end)) 结束" : "正在上课 · \(time(slot.end)) 下课", title: slot.title, tint: tint,
                    interval: slot.start...slot.end)
            } else {
                ComplicationRows(
                    kicker: (slot.isToday ? "下一节 · " : "明天 · ") + time(slot.start), title: slot.title,
                    detail: slot.estimated ? "按上周课表推测" : nil, tint: tint, until: slot.isToday ? slot.start : nil)
            }
        default:
            ComplicationRows(kicker: "课表", title: entry.snapshot == nil ? "打开 iPhone 上的 App" : "今天没有课", tint: tint)
        }
    }
}

/// 准备度: today's score on a gauge, and last night's sleep.
struct ReadinessComplicationView: View {
    @Environment(\.widgetFamily) private var faceFamily
    let entry: SnapshotEntry
    /// The family to draw when not on a face: the gallery's, which cannot set the environment.
    var galleryFamily: WidgetFamily? = nil
    var family: WidgetFamily { galleryFamily ?? faceFamily }

    var readiness: WatchSnapshot.Health.Stat? { entry.snapshot?.health?.readiness }
    var sleep: WatchSnapshot.Health.Stat? { entry.snapshot?.health?.sleep }
    var score: Double { min(max(readiness?.score ?? 0, 0), 10) }
    var text: String { readiness?.value ?? "–" }

    var body: some View {
        content.widgetURL(WatchPage.health.url)
    }

    @ViewBuilder var content: some View {
        let tint = entry.snapshot?.healthTint ?? .white
        switch family {
        case .accessoryCircular:
            Gauge(value: score, in: 0...10) {
                EmptyView()
            } currentValueLabel: {
                Text(readiness?.score == nil ? "–" : text).font(.system(size: 18, weight: .bold))
            }
            .gaugeStyle(.accessoryCircular)
            .tint(tint)
        case .accessoryCorner:
            Text(readiness?.score == nil ? "–" : text)
                .font(.system(size: 18, weight: .bold))
                .widgetCurvesContent()
                .widgetLabel {
                    Gauge(value: score, in: 0...10) { Text("准备度") }.tint(tint)
                }
        case .accessoryInline:
            Text(["准备度 \(text)", sleep.map { "睡了 \($0.value)" }].compactMap { $0 }.joined(separator: " · "))
        default:
            ComplicationRows(
                kicker: "今日准备度",
                title: readiness.map { "\($0.value)" + ($0.band.map { "  \($0)" } ?? "") } ?? "还没有数据",
                detail: sleep.map { "昨晚睡了 \($0.value)" }, tint: tint)
        }
    }
}

/// 剩余待办: how many are left, and a ring for how much of the day's list is done.
struct RemainingComplicationView: View {
    @Environment(\.widgetFamily) private var faceFamily
    let entry: SnapshotEntry
    /// The family to draw when not on a face: the gallery's, which cannot set the environment.
    var galleryFamily: WidgetFamily? = nil
    var family: WidgetFamily { galleryFamily ?? faceFamily }
    /// Faces set up before the watch App kept its whole snapshot in the App Group still get the
    /// number from there.
    var legacy: ComplicationData? = nil

    var remaining: Int? { entry.snapshot?.remaining ?? legacy?.remaining }
    var count: String { remaining.map(String.init) ?? "–" }
    var total: Int { entry.snapshot?.todos.count ?? 0 }
    var next: WatchSnapshot.Item? {
        entry.snapshot?.orderedTodos(at: entry.date, calendar: DueText.calendar()).first { !$0.done }
    }

    var body: some View {
        content.widgetURL(WatchPage.todos.url)
    }

    @ViewBuilder var content: some View {
        let tint = WatchSnapshot.assistantTint
        switch family {
        case .accessoryCircular:
            if total > 0, let remaining {
                Gauge(value: Double(total - remaining), in: 0...Double(total)) {
                    EmptyView()
                } currentValueLabel: {
                    Text(count).font(.system(size: 20, weight: .bold))
                }
                .gaugeStyle(.accessoryCircularCapacity)
                .tint(tint)
            } else {
                ZStack {
                    AccessoryWidgetBackground()
                    VStack(spacing: 0) {
                        Text(count).font(.title3.weight(.semibold))
                        Text("待办").font(.caption2)
                    }
                }
            }
        case .accessoryCorner:
            Text(count)
                .font(.title3.weight(.semibold))
                .widgetCurvesContent()
                .widgetLabel { Text("剩余待办") }
        case .accessoryInline:
            Text(remaining == 0 ? "待办都做完了" : "剩余 \(count) 件待办")
        default:
            ComplicationRows(
                kicker: remaining == 0 ? "待办" : "还有 \(count) 件待办",
                title: next?.title ?? (remaining == 0 ? "都做完了" : "打开 iPhone 上的 App"),
                detail: next.flatMap { item in
                    entry.snapshot?.dueWords(of: item, at: entry.date, calendar: DueText.calendar())
                },
                tint: tint)
        }
    }
}

#if DEBUG
    /// Every complication about the size it has on a 49 mm face, for the simulator: `-watch.gallery 1`
    /// shows the rectangular ones, `2` the round and inline ones, with a `-watch.mock` state.
    /// WidgetKit's curved labels on corner complications only draw on a real face.
    struct ComplicationGallery: View {
        let entry: SnapshotEntry
        let set: Int

        var body: some View {
            VStack(spacing: 8) {
                if set == 1 {
                    row(.accessoryRectangular) { NowComplicationView(entry: entry, galleryFamily: $0) }
                    row(.accessoryRectangular) { NextClassComplicationView(entry: entry, galleryFamily: $0) }
                    row(.accessoryRectangular) { ReadinessComplicationView(entry: entry, galleryFamily: $0) }
                } else {
                    HStack(spacing: 8) {
                        round { NextClassComplicationView(entry: entry, galleryFamily: .accessoryCircular) }
                        round { ReadinessComplicationView(entry: entry, galleryFamily: .accessoryCircular) }
                        round { RemainingComplicationView(entry: entry, galleryFamily: .accessoryCircular) }
                    }
                    row(.accessoryRectangular) { RemainingComplicationView(entry: entry, galleryFamily: $0) }
                    row(.accessoryInline) { NextClassComplicationView(entry: entry, galleryFamily: $0) }
                    row(.accessoryInline) { ReadinessComplicationView(entry: entry, galleryFamily: $0) }
                    row(.accessoryInline) { RemainingComplicationView(entry: entry, galleryFamily: $0) }
                }
            }
            .padding(.horizontal, 8)
            .containerBackground(.black, for: .navigation)
        }

        func row<Content: View>(_ family: WidgetFamily, @ViewBuilder _ content: (WidgetFamily) -> Content) -> some View {
            content(family)
                .frame(width: 180, height: family == .accessoryInline ? 20 : 62, alignment: .leading)
        }

        func round<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
            content().frame(width: 50, height: 50)
        }
    }
#endif
