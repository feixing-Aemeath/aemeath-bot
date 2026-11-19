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
        /// In a card of its own; in a sheet the bar names it.
        var showsTitle = true

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
                if showsTitle {
                    CardTitle("今天的记录", icon: "square.and.pencil")
                }
                Text("记满 3 周后，用来校准综合准备度的权重。")
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
            .frame(maxWidth: 520, alignment: .leading)
            .padding(CardSurface.padding)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
    }

    /// 「今天的记录」 among 健康Bot's cards, slim: what it is for and a button that opens the form
    /// in a sheet (the form used to be the page's first card, pushing the numbers down).
    struct CheckinCard: View {
        @State private var open = false

        var body: some View {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    CardTitle("今天的记录", icon: "square.and.pencil")
                    Text("记下「健身」App 的准备指数和自己的感觉")
                        .font(CardFont.note)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                Button("记录") { open = true }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
            .padding(CardSurface.padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
            .sheet(isPresented: $open) { CheckinSheet() }
        }
    }

    /// The check-in form in a sheet.
    struct CheckinSheet: View {
        @Environment(\.dismiss) private var dismiss

        var body: some View {
            #if os(iOS)
                NavigationStack {
                    ScrollView { ReadinessCheckinForm(showsTitle: false) }
                        .background(Palette.page)
                        .navigationTitle("今天的记录")
                        .navigationBarTitleDisplayMode(.inline)
                        .toolbar {
                            ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } }
                        }
                }
                .presentationDetents([.medium, .large])
            #else
                VStack(alignment: .leading, spacing: 0) {
                    Text("今天的记录").font(Typography.serif(17)).padding([.horizontal, .top], 20)
                    ReadinessCheckinForm(showsTitle: false).padding(.horizontal, 6)
                    HStack {
                        Spacer()
                        Button("完成") { dismiss() }.keyboardShortcut(.defaultAction)
                    }
                    .padding([.horizontal, .bottom], 20)
                }
                .frame(width: 420)
            #endif
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
