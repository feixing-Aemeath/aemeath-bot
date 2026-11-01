#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// 「今天的记录」: the readiness shown in Apple's Fitness app and how ready you feel, kept to
    /// calibrate the readiness score (docs/readiness-calibration.md). Either one is enough.
    struct ReadinessCheckinForm: View {
        @EnvironmentObject private var store: AppStore
        @State private var day = Date()
        @State private var appleText = ""
        @State private var perceived = 0  // 0: not given
        @State private var sending = false
        @State private var saved: String?

        var checkin: ReadinessCheckin {
            ReadinessCheckin(
                appleReadiness: ReadinessCheckin.parseApple(appleText),
                perceived: perceived == 0 ? nil : perceived)
        }

        var appleInvalid: Bool {
            !appleText.trimmingCharacters(in: .whitespaces).isEmpty && checkin.appleReadiness == nil
        }

        var body: some View {
            VStack(alignment: .leading, spacing: 10) {
                CardTitle("今天的记录", icon: "square.and.pencil")
                Text("用来校准综合准备度：记满 3 周后，在 Mac 上运行 scripts.readiness_calibration，对比这两项和算出的分数，给出调整权重的建议。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                DatePicker("日期", selection: $day, in: ...Date(), displayedComponents: .date)
                HStack {
                    Text("「健身」App 的准备指数")
                    Spacer()
                    TextField("0–10", text: $appleText)
                        .frame(width: 90)
                        .multilineTextAlignment(.trailing)
                        .decimalKeyboard()
                }
                if appleInvalid {
                    Text("请填 0 到 10 之间的数").font(.caption).foregroundStyle(.red)
                }
                Picker("自己的感觉", selection: $perceived) {
                    Text("不填").tag(0)
                    ForEach(1...5, id: \.self) { value in
                        Text("\(value)").tag(value)
                    }
                }
                .pickerStyle(.segmented)
                Text("1 很累，3 一般，5 状态很好").font(.caption2).foregroundStyle(.secondary)
                HStack(spacing: 10) {
                    Button {
                        let entry = checkin
                        let chosen = day
                        sending = true
                        Task {
                            if await store.logCheckin(entry, day: chosen) {
                                saved = "已保存 \(DueText.format(chosen, "M月d日", store.calendar)) 的记录"
                                appleText = ""
                                perceived = 0
                            }
                            sending = false
                        }
                    } label: {
                        Text("保存")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!checkin.isValid || appleInvalid || sending || !store.online)
                    if sending {
                        ProgressView().controlSize(.small)
                    }
                    if let saved {
                        Text(saved).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            // One of 健康Bot's cards (BotCards): drawn like them, as tall as its row; the fields
            // stay within 520 points when the card spans a wide window.
            .frame(maxWidth: 520, alignment: .leading)
            .padding(12)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .cardSurface()
        }
    }

    extension View {
        /// The decimal keypad on iPhone; nothing elsewhere.
        @ViewBuilder
        func decimalKeyboard() -> some View {
            #if os(iOS)
                self.keyboardType(.decimalPad)
            #else
                self
            #endif
        }
    }
#endif
