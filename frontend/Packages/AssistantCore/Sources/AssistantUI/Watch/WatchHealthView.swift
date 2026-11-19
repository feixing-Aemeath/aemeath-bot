#if os(watchOS)
    import AssistantCore
    import SwiftUI

    /// 状态, 健康Bot: today's readiness on a ring out of 10, what it is made of, and last night's
    /// sleep from going to bed to getting up.
    struct WatchHealthView: View {
        @EnvironmentObject private var store: WatchStore
        @WatchDimmed private var dimmed

        var health: WatchSnapshot.Health? { store.snapshot?.health }
        var look: StickerLook { .watch(.health, in: store.snapshot) }

        var body: some View {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let readiness = health?.readiness { WatchReadiness(stat: readiness, look: look) }
                    if let sleep = health?.sleep { sleepSection(sleep) }
                    if health == nil {
                        Text("iPhone 上传健康数据、Mac 算出准备度后会出现在这里。")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.top, 5)
                .padding(.horizontal, WatchScale.margin)
                .padding(.bottom, 14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .opacity(dimmed ? 0.7 : 1)
            }
            .watchScrollFade()
            .watchKicker("今天状态", color: look.ink, look: look)
            .watchBackdrop(look.accent.color)
        }

        func sleepSection(_ sleep: WatchSnapshot.Health.Stat) -> some View {
            VStack(alignment: .leading, spacing: 4) {
                WatchHairline().padding(.bottom, 8)
                Text("昨晚睡眠")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(look.ink)
                WatchDigits(text: [sleep.value, sleep.unit].compactMap { $0 }.joined(separator: " "))
                if let span = sleep.sleepSpan {
                    HStack(spacing: 5) {
                        Image(systemName: "moon.fill").foregroundStyle(look.ink)
                        Text(span.start)
                        Capsule().fill(look.ink.opacity(0.7)).frame(height: 3)
                        Text(span.end)
                        Image(systemName: "sun.max.fill").foregroundStyle(.yellow)
                    }
                    .font(.caption2.weight(.medium).monospacedDigit())
                } else if let label = sleep.label {
                    Text(label).font(.caption2).foregroundStyle(.secondary)
                }
                if let delta = sleep.delta {
                    Text(delta).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }

    /// The ring, sweeping up to the score when the page first shows, like the Activity rings.
    struct WatchReadiness: View {
        let stat: WatchSnapshot.Health.Stat
        let look: StickerLook
        @State private var shown = 0.0
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @WatchDimmed private var dimmed

        var target: Double { min(max((stat.score ?? 0) / 10, 0), 1) }

        var body: some View {
            VStack(spacing: 4) {
                ZStack {
                    WatchRing(value: dimmed ? target : shown, tint: look.ink, lineWidth: WatchScale.of(12))
                    VStack(spacing: -2) {
                        Text(stat.value)
                            .font(.system(size: WatchScale.of(stat.score == nil ? 18 : 44), weight: .semibold, design: .rounded))
                            .monospacedDigit()
                            .minimumScaleFactor(0.5)
                            .lineLimit(2)
                            .multilineTextAlignment(.center)
                        if stat.score != nil, let unit = stat.unit {
                            Text(unit).font(.caption2.weight(.medium)).foregroundStyle(.secondary)
                        }
                    }
                    .padding(.horizontal, WatchScale.of(14))
                }
                .frame(width: WatchScale.of(122), height: WatchScale.of(122))
                .frame(maxWidth: .infinity)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("今日准备度 \(stat.value)")
                if let band = stat.band {
                    Text(band).font(.system(size: WatchScale.of(17), weight: .bold))
                }
                if let change = stat.change {
                    Text(change).font(.caption2).foregroundStyle(.secondary)
                }
                parts
            }
            .padding(.bottom, 12)
            .onAppear {
                guard shown != target else { return }
                if reduceMotion {
                    shown = target
                } else {
                    withAnimation(.easeOut(duration: 0.9)) { shown = target }
                }
            }
        }

        /// What the score is made of: short bars. A label that is not scores (数据不足的原因) as text.
        @ViewBuilder var parts: some View {
            if !stat.parts.isEmpty {
                VStack(spacing: 7) {
                    ForEach(stat.parts, id: \.name) { part in
                        HStack(spacing: 6) {
                            Text(part.name)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .frame(width: WatchScale.of(62), alignment: .leading)
                            WatchProgressLine(value: min(max(part.score / 10, 0), 1), tint: look.ink, height: 5)
                            Text(String(format: "%g", part.score))
                                .font(.caption2.weight(.semibold).monospacedDigit())
                                .frame(width: WatchScale.of(26), alignment: .trailing)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
                .padding(.top, 10)
            } else if let label = stat.label {
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 6)
            }
        }
    }

    /// 「7小时12分」 with the digits large and the words small.
    struct WatchDigits: View {
        let text: String

        var body: some View {
            HStack(alignment: .firstTextBaseline, spacing: 0) {
                ForEach(Array(pieces.enumerated()), id: \.offset) { _, piece in
                    Text(piece.text)
                        .font(
                            piece.digits
                                ? .system(size: WatchScale.of(34), weight: .semibold, design: .rounded)
                                : .system(size: WatchScale.of(15), weight: .semibold))
                }
            }
            .lineLimit(1)
            .minimumScaleFactor(0.7)
        }

        var pieces: [(text: String, digits: Bool)] {
            var result: [(String, Bool)] = []
            var current = ""
            var digits = false
            for character in text {
                let isDigit = character.isNumber || character == "."
                if isDigit != digits, !current.isEmpty {
                    result.append((current, digits))
                    current = ""
                }
                digits = isDigit
                current.append(character)
            }
            if !current.isEmpty { result.append((current, digits)) }
            return result
        }
    }
#endif
