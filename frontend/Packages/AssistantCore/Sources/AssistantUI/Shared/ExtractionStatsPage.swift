#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    struct ExtractionStatsPage: View {
        @EnvironmentObject private var store: AppStore
        @State private var stats: ExtractionStats?
        @State private var errorText: String?
        @State private var loading = false
        var body: some View {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let stats {
                        Text(stats.weeks.first?.summary ?? "这周没有数据")
                            .font(Typography.serif(18)).fixedSize(horizontal: false, vertical: true)
                        // A row per week, newest first: a six-column table ran off a phone's edge.
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(stats.weeks.enumerated()), id: \.element.id) { index, week in
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(alignment: .firstTextBaseline) {
                                        Text(index == 0 ? "这周（\(week.startText)）" : week.startText)
                                            .font(CardFont.body.weight(.medium))
                                        Spacer(minLength: 8)
                                        if week.hasData {
                                            Text("抓到 \(week.caught) 条")
                                                .font(CardFont.body.weight(.semibold)).monospacedDigit()
                                        }
                                    }
                                    Text(week.detail)
                                        .font(CardFont.note).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                .padding(.vertical, 10)
                                .accessibilityElement(children: .combine)
                                if index < stats.weeks.count - 1 { Palette.hairline.frame(height: 1) }
                            }
                        }
                        .padding(.horizontal, CardSurface.padding).padding(.vertical, 4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .cardSurface()
                        Text("「自己补的」只统计标了“这是助手漏掉的”的待办和转发。抓到的比例＝抓到的 ÷（抓到的＋自己补的）。")
                            .font(CardFont.note).foregroundStyle(.secondary)
                    } else if loading {
                        ProgressView("正在读取对账记录…")
                    }
                    if let errorText { Text(errorText).font(CardFont.body).foregroundStyle(.secondary) }
                    Button("刷新") { Task { await load() } }.disabled(loading).accessibilityLabel("刷新对账记录")
                }
                .padding(20).frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Palette.page).navigationTitle("对账")
            .task { await load() }
            .refreshable { await load() }
        }
        @MainActor func load() async {
            guard !loading else { return }
            loading = true
            errorText = nil
            defer { loading = false }
            do { stats = try await store.backend.extractionStats(weeks: 4) } catch APIError.notFound {
                errorText = ManualEntryText.backendUpdate + "，暂时看不到对账记录。"
            } catch { errorText = "没读到对账记录：" + AppStore.describe(error) }
        }
    }
#endif
