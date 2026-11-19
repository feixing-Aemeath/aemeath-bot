#if os(watchOS)
    import AssistantCore
    import SwiftUI

    /// 此刻: the one thing that matters at this moment (WatchMoment says which) with how long,
    /// what comes after it, and the weather. Fits one screen, nothing to scroll: the page is for
    /// a glance. A todo can be ticked off right here, by tapping or with a double tap.
    struct WatchNowView: View {
        @EnvironmentObject private var store: WatchStore
        let isCurrent: Bool
        let jump: (WatchPage) -> Void

        var body: some View {
            // Redrawn every minute: the countdown, and what leads when an edge of the day passes.
            TimelineView(.everyMinute) { context in
                let date = store.now(context.date)
                let moment = store.snapshot?.moment(at: date, calendar: DueText.calendar())
                let identity = moment.map { "\($0.kind.rawValue)-\($0.title)" }
                ZStack {
                    if let snapshot = store.snapshot, let moment {
                        if let done = store.justDone {
                            WatchJustDone(item: done, look: .watch(.assistant, in: snapshot)) { store.undoJustDone() }
                                .transition(.blurReplace)
                        } else {
                            WatchHero(moment: moment, snapshot: snapshot, date: date, isCurrent: isCurrent, jump: jump) {
                                if let item = moment.todo { store.completeFromFirstPage(item) }
                            }
                            .id(identity)
                            .transition(.blurReplace)
                        }
                    } else {
                        WatchWaiting()
                    }
                }
                // One thing giving way to the next, by a tick or because the time came.
                .animation(.smooth(duration: 0.35), value: identity)
                .animation(.smooth(duration: 0.35), value: store.justDone?.id)
            }
            .sensoryFeedback(.success, trigger: store.justDone?.id) { _, id in id != nil }
        }
    }

    /// The lead, like a poster: what it is, then how long, with the Bot sitting on the line that
    /// runs towards it, then what comes after.
    struct WatchHero: View {
        let moment: WatchMoment
        let snapshot: WatchSnapshot
        let date: Date
        let isCurrent: Bool
        let jump: (WatchPage) -> Void
        let complete: () -> Void
        @WatchDimmed private var dimmed

        var look: StickerLook { .watch(moment.bot, in: snapshot) }
        var ink: Color { moment.urgent ? Color.orange : look.ink }
        var calendar: Calendar { DueText.calendar() }

        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                heading.padding(.top, 3)
                Spacer(minLength: 2).frame(maxHeight: WatchScale.of(10))
                figureRow
                WatchProgressLine(value: moment.progress, tint: ink)
                    .opacity(dimmed ? 0.5 : 1)
                if !dimmed {
                    // Long titles, large text and small watches: what comes after gives way, so
                    // the lead stays whole and the page never needs scrolling.
                    ViewThatFits(in: .vertical) {
                        tail(lines: 2, weather: true)
                        tail(lines: 1, weather: true)
                        tail(lines: 1, weather: false)
                        Color.clear.frame(height: 0)
                    }
                    .frame(maxHeight: .infinity, alignment: .bottom)
                }
            }
            .padding(.bottom, WatchScale.isCompact ? 5 : 8)
            .padding(.horizontal, WatchScale.margin)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .ignoresSafeArea(edges: .bottom)
            .watchKicker(moment.kicker, color: ink, look: moment.todo == nil ? nil : look, state: snapshot.state)
            .watchBackdrop(look.accent.color, glow: (moment.urgent ? Color.orange : look.accent.color, UnitPoint(x: 0.84, y: 0.56)))
        }

        func tail(lines count: Int, weather: Bool) -> some View {
            VStack(alignment: .leading, spacing: 0) {
                lines(count)
                if weather { footer.padding(.top, WatchScale.of(4)) }
            }
            .padding(.top, 3)
        }

        /// The title and when: a tap opens the page with the rest of it.
        var heading: some View {
            Button {
                if moment.page != .now { jump(moment.page) }
            } label: {
                VStack(alignment: .leading, spacing: 3) {
                    Text(moment.title)
                        .font(.system(size: WatchScale.of(25), weight: .bold))
                        .lineLimit(2)
                        // Two full lines before what comes after gets any room.
                        .fixedSize(horizontal: false, vertical: true)
                        .privacySensitive()
                        .opacity(dimmed ? 0.75 : 1)
                    if let detail = detailText {
                        Text(detail)
                            .font(.system(size: WatchScale.of(14), weight: .medium))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .privacySensitive()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }

        /// 「17:00 截止 · 软件工程2班」: when, and for a todo, where it came from.
        var detailText: String? {
            let parts = [moment.detail, moment.figure == nil ? nil : moment.note].compactMap { $0 }
            return parts.isEmpty ? nil : parts.joined(separator: " · ")
        }

        /// The figure with the Bot or the check at its right, over the same stretch of line: the
        /// big digits and the sticker share height, so neither pushes the rest down.
        var figureRow: some View {
            HStack(alignment: .bottom, spacing: 0) {
                if let figure = moment.figure {
                    HStack(alignment: .firstTextBaseline, spacing: 3) {
                        if let symbol = moment.symbol {
                            Image(systemName: symbol)
                                .symbolRenderingMode(.multicolor)
                                .font(.system(size: WatchScale.of(26)))
                                .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 6 }
                        }
                        Text(figure.value)
                            .font(.system(size: figureSize(figure.value), weight: .semibold, design: .rounded))
                            .monospacedDigit()
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                            // Room is short sideways on small watches; never let height squeeze it.
                            .fixedSize(horizontal: false, vertical: true)
                            .contentTransition(.numericText(countsDown: true))
                            .animation(.snappy, value: figure.value)
                        if let unit = figure.unit {
                            Text(unit)
                                .font(.system(size: WatchScale.of(15), weight: .semibold))
                                .foregroundStyle(ink)
                        }
                    }
                    // The digits' line box is much taller than the digits.
                    .padding(.vertical, -WatchScale.of(7))
                    .padding(.bottom, 3)
                    .layoutPriority(1)
                    .accessibilityElement(children: .combine)
                }
                if moment.figure == nil, let note = moment.note {
                    Text("来自 " + note)
                        .font(.system(size: WatchScale.of(14), weight: .medium))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .padding(.bottom, 8)
                        .privacySensitive()
                }
                Spacer(minLength: 0)
                if !dimmed { accessory }
            }
        }

        /// As big as the room beside the Bot allows: minutes, then a clock time, then the weather.
        func figureSize(_ value: String) -> CGFloat {
            if moment.symbol != nil { return WatchScale.of(29) }
            return WatchScale.of(value.count <= 3 ? 54 : 40)
        }

        /// Beside the figure: the check that ticks a todo off, else the Bot, sitting on the line.
        @ViewBuilder var accessory: some View {
            if moment.todo != nil {
                let size = WatchScale.of(56)
                let button = Button(action: complete) {
                    ZStack {
                        Circle().fill(ink)
                        Image(systemName: "checkmark")
                            .font(.system(size: WatchScale.of(24), weight: .heavy))
                            .foregroundStyle(.black)
                    }
                    .frame(width: size, height: size)
                }
                .buttonStyle(WatchPressStyle())
                .accessibilityLabel("完成这件待办")
                .padding(.bottom, 7)
                if #available(watchOS 11.0, *) {
                    button.handGestureShortcut(.primaryAction, isEnabled: isCurrent)
                } else {
                    button
                }
            } else {
                // A little smaller on the small watches, where 「8.2 / 10」 needs the room.
                let size: CGFloat = WatchScale.isCompact ? 60 : 70
                MascotView(
                    look: look, state: mascotState, interactive: true, liveliness: isCurrent ? .full : .subtle
                )
                .frame(width: size, height: size)
            }
        }

        /// Asleep through the night when there is nothing on; otherwise the Agents' state, as on
        /// the other devices.
        var mascotState: MascotState {
            let hour = calendar.component(.hour, from: date)
            if moment.kind == .clear, hour >= 23 || hour < 6 { return .offline }
            return snapshot.state == .error ? .idle : snapshot.state
        }

        func lines(_ count: Int) -> some View {
            VStack(alignment: .leading, spacing: WatchScale.of(3)) {
                ForEach(moment.lines.prefix(count)) { line in
                    Button {
                        jump(line.page)
                    } label: {
                        HStack(spacing: 5) {
                            if let time = line.time {
                                Text(time)
                                    .font(.system(size: WatchScale.of(14), weight: .semibold).monospacedDigit())
                                    .foregroundStyle(ink)
                            } else {
                                Image(systemName: line.icon == .sleep ? "bed.double.fill" : "checklist")
                                    .font(.system(size: WatchScale.of(12), weight: .semibold))
                                    .foregroundStyle(ink)
                            }
                            Text(line.text)
                                .font(.system(size: WatchScale.of(14)))
                                .lineLimit(1)
                                .privacySensitive()
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }

        /// The weather, unless the data is old or the iPhone could not reach the Mac, which is
        /// what one needs to know then.
        @ViewBuilder var footer: some View {
            if !snapshot.online || isStale {
                HStack(spacing: 4) {
                    Image(systemName: snapshot.online ? "clock" : "wifi.slash")
                    Text(footerText).lineLimit(1)
                }
                .font(.system(size: WatchScale.of(13), weight: .medium))
                .foregroundStyle(snapshot.online ? Color.secondary : Color.orange)
            } else if moment.symbol == nil,
                let shown = snapshot.weather?.shown(at: date, since: snapshot.generatedAt, calendar: calendar)
            {
                HStack(spacing: 4) {
                    if let symbol = shown.day.symbol {
                        Image(systemName: symbol).symbolRenderingMode(.multicolor)
                    }
                    Text(shown.line).lineLimit(1)
                }
                .font(.system(size: WatchScale.of(13)))
                .foregroundStyle(.secondary)
            }
        }

        var isStale: Bool { date.timeIntervalSince(snapshot.generatedAt) > 2 * 3600 }

        var footerText: String {
            let when = RelativeTime.describe(snapshot.generatedAt, now: date, calendar: calendar)
            return snapshot.online ? "\(when)同步的" : "连不上 Mac · \(when)"
        }
    }

    /// The moment after a tick: done, with a way back, and the Bot pleased about it.
    struct WatchJustDone: View {
        let item: WatchSnapshot.Item
        let look: StickerLook
        let undo: () -> Void
        @State private var cue = MascotCue(.poke)

        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                Text(item.title)
                    .font(.system(size: WatchScale.of(19), weight: .semibold))
                    .strikethrough(true, color: .white.opacity(0.5))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
                Spacer(minLength: 3)
                HStack(alignment: .bottom, spacing: 0) {
                    Text("做完了")
                        .font(.system(size: WatchScale.of(32), weight: .bold))
                        .padding(.bottom, 4)
                    Spacer(minLength: 0)
                    MascotView(look: look, state: .done, cue: cue, liveliness: .full)
                        .frame(width: WatchScale.of(70), height: WatchScale.of(70))
                }
                WatchProgressLine(value: 1, tint: look.ink)
                Spacer(minLength: 3)
                Button("撤销", systemImage: "arrow.uturn.backward", action: undo)
                    .font(.system(size: WatchScale.of(14), weight: .semibold))
                    .buttonStyle(.plain)
                    .foregroundStyle(look.ink)
                    .padding(.bottom, WatchScale.of(10))
            }
            .padding(.horizontal, WatchScale.margin)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .ignoresSafeArea(edges: .bottom)
            .watchKicker("待办", color: look.ink)
            .watchBackdrop(look.accent.color)
        }
    }

    /// Before the first snapshot: the Bot asleep on an empty line.
    struct WatchWaiting: View {
        @EnvironmentObject private var store: WatchStore
        private let look = StickerCatalog.main.assistant

        var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                Text("还没有收到数据")
                    .font(.system(size: WatchScale.of(25), weight: .bold))
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
                Text(store.message ?? "打开 iPhone 上的 App，就会送到手表上。")
                    .font(.system(size: WatchScale.of(14)))
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
                Spacer(minLength: 3)
                HStack(alignment: .bottom) {
                    Spacer()
                    MascotView(look: look, state: .offline, liveliness: .subtle)
                        .frame(width: WatchScale.of(70), height: WatchScale.of(70))
                }
                WatchProgressLine(value: nil, tint: look.ink)
                Spacer(minLength: 3)
                WatchRefreshButton(tint: look.ink)
                    .padding(.bottom, WatchScale.of(10))
            }
            .padding(.horizontal, WatchScale.margin)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .ignoresSafeArea(edges: .bottom)
            .watchKicker("此刻", color: look.ink)
            .watchBackdrop(look.accent.color)
        }
    }
#endif
