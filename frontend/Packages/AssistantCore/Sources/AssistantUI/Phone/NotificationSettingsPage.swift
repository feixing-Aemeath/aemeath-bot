#if os(iOS)
    import AssistantCore
    import SwiftUI
    import UIKit

    struct NotificationSettingsPage: View {
        @ObservedObject private var reach = PhoneReach.shared
        @Environment(\.scenePhase) private var phase

        private var time: Binding<Date> {
            Binding(
                get: {
                    Calendar.current.date(
                        bySettingHour: reach.preferences.hour, minute: reach.preferences.minute, second: 0, of: Date())
                        ?? Date()
                }, set: { date in Task { await reach.setTime(date) } })
        }

        var body: some View {
            Form {
                Section {
                    ForEach(ReachKind.allCases, id: \.self) { kind in
                        Toggle(
                            kind.title,
                            isOn: Binding(
                                get: { reach.preferences[kind] },
                                set: { enabled in
                                    Task { await reach.set(kind, enabled: enabled) }
                                }))
                    }
                    DatePicker("出门提醒时间", selection: time, displayedComponents: .hourAndMinute)
                        .disabled(!reach.preferences.departure)
                } footer: {
                    Text("晨间简报每天每位助手最多一条。明早的出门提醒会提前排好；其他新内容需要 App 刷新后才能发现。待办到期由「提醒事项」提醒。")
                }
                Section("系统权限") {
                    if reach.authorization == .denied {
                        Text("系统通知已关闭。上面的开关会保留，允许通知后才会提醒你。")
                        Button("前往系统设置") {
                            UIApplication.shared.open(URL(string: UIApplication.openNotificationSettingsURLString)!)
                        }
                    } else if reach.authorization == .notDetermined {
                        Text("尚未允许通知。默认已选好前三类，点下方按钮后才会向系统申请权限。")
                        Button("允许通知") {
                            Task {
                                await reach.requestAccess()
                                _ = await reach.refresh()
                            }
                        }
                    } else {
                        Text("已允许通知；声音和锁屏显示方式可以在系统设置中调整。")
                        Button("系统通知设置") {
                            UIApplication.shared.open(URL(string: UIApplication.openNotificationSettingsURLString)!)
                        }
                    }
                }
                if let problem = reach.problem { Section { Text(problem).foregroundStyle(.secondary) } }
            }
            .settingsPage("通知")
            .task { await reach.checkAuthorization() }
            .onChange(of: phase) { _, phase in
                if phase == .active { Task { await reach.checkAuthorization() } }
            }
        }
    }
#endif
