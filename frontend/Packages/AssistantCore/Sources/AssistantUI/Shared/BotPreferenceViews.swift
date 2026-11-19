#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// Both lists move rows within their pin group. The other group keeps its order.
    extension AppStore {
        func moveBots(in group: [Bot], from offsets: IndexSet, to destination: Int) {
            guard let first = group.first else { return }
            var moved = group
            moved.move(fromOffsets: offsets, toOffset: destination)
            let pinned = botPreferences[first.id].pinned
            let others = bots.filter { $0.kind != .source && botPreferences[$0.id].pinned != pinned }
            botPreferences.setOrder((pinned ? moved + others : others + moved).map(\.id))
        }
    }

    struct BotOrderGroup<Row: View>: View {
        @EnvironmentObject private var store: AppStore
        var pinned: Bool
        var movable: Bool
        @ViewBuilder var row: (Bot, [Bot], Int) -> Row

        var body: some View {
            let group = store.bots.filter { $0.kind != .source && store.botPreferences[$0.id].pinned == pinned }
            if !group.isEmpty {
                Section(pinned ? "收藏" : "助手") {
                    ForEach(Array(group.enumerated()), id: \.element.id) { index, bot in
                        row(bot, group, index)
                    }
                    .onMove(perform: movable ? { store.moveBots(in: group, from: $0, to: $1) } : nil)
                }
            }
        }
    }

    struct BotSidebarList<Dashboard: View, Row: View>: View {
        @EnvironmentObject private var store: AppStore
        var showsSources = true
        @ViewBuilder var dashboard: () -> Dashboard
        @ViewBuilder var row: (Bot) -> Row
        @State private var editingOrder = false
        @State private var renaming: Bot?
        @State private var draft = ""

        var body: some View {
            List {
                dashboard().sidebarBotRow()
                if editingOrder {
                    Button("完成排序") { editingOrder = false }
                }
                group(pinned: true)
                group(pinned: false)
                let sources = store.bots.filter { $0.kind == .source }
                if showsSources && !sources.isEmpty {
                    Section("消息来源") {
                        ForEach(sources) { row($0).sidebarBotRow() }
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            #if os(iOS)
                .environment(\.editMode, .constant(editingOrder ? .active : .inactive))
            #endif
            .alert("重命名", isPresented: Binding(
                get: { renaming != nil }, set: { if !$0 { renaming = nil } }
            ), presenting: renaming) { bot in
                TextField(bot.name, text: $draft)
                Button("取消", role: .cancel) {}
                Button("保存") {
                    store.botPreferences[bot.id].customName = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                }
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars.count > BotPreferences.nameLimit)
            } message: { _ in Text("名字最多 20 个字。留空恢复原名。") }
        }

        func group(pinned: Bool) -> some View {
            BotOrderGroup(pinned: pinned, movable: editingOrder) { bot, group, index in
                row(bot)
                    .sidebarBotRow()
                    #if os(iOS)
                    .contextMenu {
                        menu(bot, group: group, index: index)
                    } preview: {
                        // The preview is hosted on its own: it needs the store handed to it.
                        PhoneBotRow(bot: bot).padding(12).frame(width: 280).fixedSize(horizontal: false, vertical: true)
                            .environmentObject(store)
                    }
                    #else
                    .contextMenu { menu(bot, group: group, index: index) }
                    #endif
            }
        }

        @ViewBuilder func menu(_ bot: Bot, group: [Bot], index: Int) -> some View {
            let entry = store.botPreferences[bot.id]
            Button(entry.pinned ? "取消收藏" : "收藏", systemImage: entry.pinned ? "star.slash" : "star") {
                store.botPreferences[bot.id].pinned.toggle()
            }
            Button("重命名…", systemImage: "pencil") {
                draft = store.displayName(bot)
                renaming = bot
            }
            Divider()
            Button("上移", systemImage: "arrow.up") {
                store.moveBots(in: group, from: IndexSet(integer: index), to: index - 1)
            }
            .disabled(index == 0)
            Button("下移", systemImage: "arrow.down") {
                store.moveBots(in: group, from: IndexSet(integer: index), to: index + 2)
            }
            .disabled(index + 1 == group.count)
            Button("调整顺序…", systemImage: "arrow.up.arrow.down") { editingOrder = true }
            Divider()
            Button(entry.visible ? "不在仪表盘显示" : "在仪表盘显示", systemImage: entry.visible ? "eye.slash" : "eye") {
                store.botPreferences[bot.id].visible.toggle()
            }
        }
    }

    private extension View {
        func sidebarBotRow() -> some View {
            listRowInsets(EdgeInsets(top: 1, leading: 10, bottom: 1, trailing: 10))
                .listRowBackground(Color.clear)
                .listRowSeparator(.hidden)
        }
    }

    struct DashboardSettings: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            List {
                group(pinned: true)
                group(pinned: false)
                Button("恢复默认") { store.botPreferences.resetLayout() }
            }
            .settingsPage("仪表盘")
            #if os(iOS)
                .toolbar { EditButton() }
            #endif
        }

        func group(pinned: Bool) -> some View {
            BotOrderGroup(pinned: pinned, movable: true) { bot, _, _ in
                Toggle(isOn: Binding(
                    get: { store.botPreferences[bot.id].visible },
                    set: { store.botPreferences[bot.id].visible = $0 }
                )) {
                    HStack {
                        BotAvatar(bot: bot).frame(width: 30, height: 30)
                        VStack(alignment: .leading) {
                            Text(store.displayName(bot))
                            Text("在仪表盘显示").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .toggleStyle(.switch)
                .accessibilityLabel("\(store.displayName(bot))，在仪表盘显示")
            }
        }
    }
#endif
