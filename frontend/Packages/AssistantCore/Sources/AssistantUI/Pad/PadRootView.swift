#if os(iOS)
    import AssistantCore
    import SwiftUI

    /// 仪表盘 and the Bots on the left, the dashboard, a Bot's page or settings on the right. The
    /// system sidebar button brings the list back when a portrait window hides it.
    public struct PadRootView: View {
        @AppStorage("assistant.pad.cardsPanel") private var cardsShown = true
        @EnvironmentObject private var store: AppStore
        @AppStorage(PadPresentation.botStorageKey) private var chosen = Bot.assistantID
        @State private var showingSettings = false
        @State private var navigationID = UUID()
        @State private var visibility = NavigationSplitViewVisibility.automatic

        public init() {
            AwayClock.openIfAway(DashboardPage.id, key: PadPresentation.botStorageKey)
        }

        var showsDashboard: Bool { chosen == DashboardPage.id }

        var bot: Bot? {
            guard !showsDashboard else { return nil }
            return PadPresentation.selectedBot(storedID: chosen, bots: store.bots, settled: store.settled)
        }

        public var body: some View {
            NavigationSplitView(columnVisibility: $visibility) {
                PadSidebar(
                    chosen: showingSettings ? nil : (showsDashboard ? chosen : bot?.id ?? chosen), choose: choose,
                    openSettings: openSettings)
                    .navigationTitle("Aemeath Bot")
                    .navigationBarTitleDisplayMode(.inline)
                    .navigationSplitViewColumnWidth(min: 240, ideal: 280, max: 320)
            } detail: {
                NavigationStack {
                    Group {
                        if showingSettings {
                            SettingsHome(openBot: choose)
                        } else if showsDashboard {
                            DashboardPage(
                                openBot: choose, openCards: { cardsShown = true; choose($0) }, openSettings: openSettings)
                        } else if let bot {
                            PadBotPage(bot: bot, cardsShown: $cardsShown).id(bot.id)
                        } else {
                            Palette.page
                        }
                    }
                    .toolbarBackground(Palette.page, for: .navigationBar)
                    .toolbar { PadToolbar(openSettings: openSettings) }
                }
                // A new choice starts at its own root, even from deep inside knowledge/settings.
                .id(navigationID)
                .background(Palette.page)
            }
            .navigationSplitViewStyle(.balanced)
            .tint(Palette.clay)
            .onReceive(NotificationCenter.default.publisher(for: PhoneReach.routeNotification)) { note in
                if let id = note.object as? String { choose(id) }
            }
            .alert(
                "出错了",
                isPresented: Binding(
                    get: { store.message != nil },
                    set: { if !$0 { store.message = nil } }),
                actions: { Button("好") { store.message = nil } },
                message: { Text(store.message ?? "") })
        }

        func choose(_ id: String) {
            chosen = id
            showingSettings = false
            navigationID = UUID()
        }

        func openSettings() {
            showingSettings = true
            navigationID = UUID()
        }
    }

    /// Also used by the iPad's narrow phone layout, so its keyboard shortcuts stay available.
    struct PadToolbar: ToolbarContent {
        @EnvironmentObject private var store: AppStore
        let openSettings: () -> Void

        var body: some ToolbarContent {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    Task { await store.refresh() }
                } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }
                .keyboardShortcut("r", modifiers: .command)
                .hoverEffect(.highlight)
                Button(action: openSettings) {
                    Label("设置", systemImage: "gearshape")
                }
                .keyboardShortcut(",", modifiers: .command)
                .hoverEffect(.highlight)
            }
        }
    }

    struct PadSidebar: View {
        @EnvironmentObject private var store: AppStore
        let chosen: String?
        let choose: (String) -> Void
        let openSettings: () -> Void

        var body: some View {
            BotSidebarList {
                Button {
                    choose(DashboardPage.id)
                } label: {
                    DashboardRow(side: 42)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                        .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
                        .background(
                            RoundedRectangle(cornerRadius: 12)
                                .fill(chosen == DashboardPage.id ? Palette.selection : .clear))
                        .contentShape(RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .hoverEffect(.highlight)
                .accessibilityAddTraits(chosen == DashboardPage.id ? .isSelected : [])
            } row: { row($0) }
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 8) {
                    Divider().overlay(Palette.hairline)
                    ConnectionBadge().padding(.horizontal, 10)
                    Button(action: openSettings) {
                        Label("设置", systemImage: "gearshape")
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .padding(.horizontal, 10)
                            .background(RoundedRectangle(cornerRadius: 12).fill(chosen == nil ? Palette.selection : .clear))
                            .contentShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .buttonStyle(.plain)
                    .hoverEffect(.highlight)
                    .accessibilityAddTraits(chosen == nil ? .isSelected : [])
                }
                .padding(10)
                .background(Palette.sidebar)
            }
            .background(Palette.sidebar)
        }

        func row(_ bot: Bot) -> some View {
            Button {
                choose(bot.id)
            } label: {
                PhoneBotRow(bot: bot)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 12).fill(chosen == bot.id ? Palette.selection : .clear))
                    .contentShape(RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .hoverEffect(.highlight)
            .accessibilityAddTraits(chosen == bot.id ? .isSelected : [])
        }
    }

    #Preview("iPad · 模拟数据", traits: .landscapeLeft) {
        PadRootView().environmentObject(AppStore(backend: MockBackend()))
    }
#endif
