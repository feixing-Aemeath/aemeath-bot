#if os(iOS) && canImport(HealthKit)
    import AssistantCore
    import SwiftUI

    /// 设置 → 健康数据: whether the App may read the health data, when it last uploaded, and a
    /// button to upload now.
    struct HealthSyncPage: View {
        @EnvironmentObject private var store: AppStore
        @ObservedObject private var sync = HealthSync.shared

        var body: some View {
            Form {
                Section {
                    if !sync.available {
                        Text("这台设备没有「健康」数据。")
                    } else if !sync.asked {
                        Button("允许读取健康数据") {
                            Task {
                                await sync.requestAccess()
                                await sync.sync(force: true)
                            }
                        }
                    } else {
                        LabeledContent("上次上传", value: lastText)
                        Button {
                            Task { await sync.sync(force: true) }
                        } label: {
                            HStack {
                                Text("立即上传")
                                if sync.syncing {
                                    Spacer()
                                    ProgressView()
                                }
                            }
                        }
                        .disabled(sync.syncing || store.connection.useMockData)
                    }
                    if let problem = sync.problem {
                        Text(problem).font(.caption).foregroundStyle(.red)
                    }
                } footer: {
                    Text(footer)
                }
            }
            .settingsPage("健康数据")
        }

        var lastText: String {
            guard let last = sync.lastSync else { return "还没上传过" }
            let when = RelativeTime.describe(last, now: Date(), calendar: store.calendar)
            return sync.lastCount > 0 ? "\(when)，\(sync.lastCount) 条" : "\(when)，没有新数据"
        }

        var footer: String {
            if store.connection.useMockData { return "现在用的是模拟数据，连上 Mac 之后才会上传。" }
            return "读取睡眠、心率、静息心率、HRV、呼吸频率、血氧、手腕温度、步数、活动能量、最大摄氧量和训练记录（手表的数据会同步到 iPhone），"
                + "只上传到你自己的 Mac。打开 App 时和有新数据时自动上传，第一次会补上最近 \(HealthSync.firstDays) 天。"
                + "想改读哪些项目：iPhone 的 设置 → 健康 → 数据访问与设备 → Aemeath Bot。"
        }
    }
#endif
