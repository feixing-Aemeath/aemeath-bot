#if os(iOS)
    import AssistantCore
    import SwiftUI
    import UIKit

    /// The iPhone App, after the user's sketch: one page fills the screen, 仪表盘 (every Bot's
    /// cards) or a Bot's chat (its face on top, the wavy line, then the chat). Swiping right, or
    /// the button at the top left, slides the page aside to show 仪表盘 and every Bot, with
    /// settings at the bottom, like the sidebar of Claude's iPhone app. The App reopens the page
    /// that was open last, or 仪表盘 when it has been away for more than an hour.
    public struct PhoneRootView: View {
        @State private var cardsShown = false
        @EnvironmentObject private var store: AppStore
        @AppStorage("assistant.phone.bot") private var chosen = Bot.assistantID
        @State private var open = false
        /// How far a swipe has moved the page so far.
        @State private var drag: CGFloat = 0
        @State private var swiping = false
        @State private var showingSettings = false
        /// Rows on the page that scroll sideways themselves (仪表盘's 今天一览).
        @State private var sideways = SidewaysZones()
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @Environment(\.scenePhase) private var scenePhase

        public init(botStorageKey: String = "assistant.phone.bot") {
            // Started after more than an hour away (or for the first time): 仪表盘.
            AwayClock.openIfAway(DashboardPage.id, key: botStorageKey)
            _chosen = AppStorage(wrappedValue: Bot.assistantID, botStorageKey)
        }

        var showsDashboard: Bool { chosen == DashboardPage.id }

        /// A plugin that went away shows the assistant; nil while the plugins are still loading,
        /// so the App does not flash the assistant before the Bot that was open last.
        var bot: Bot? {
            guard !showsDashboard else { return nil }
            let bots = store.bots
            if let bot = bots.first(where: { $0.id == chosen }) { return bot }
            return store.settled ? bots.first(where: \.isAssistant) : nil
        }

        public var body: some View {
            GeometryReader { proxy in
                let width = min(proxy.size.width * 0.82, 340)
                let shift = min(max((open ? width : 0) + drag, 0), width)
                ZStack(alignment: .leading) {
                    PhoneDrawer(chosen: showsDashboard ? chosen : bot?.id ?? chosen, choose: choose) {
                        showingSettings = true
                    }
                    .frame(width: width)
                    // Comes in a little slower than the page moves away.
                    .offset(x: (shift - width) * 0.2)
                    .ignoresSafeArea(.keyboard)
                    NavigationStack {
                        if showsDashboard {
                            DashboardPage(
                                openBot: choose, openCards: { id in choose(id); cardsShown = true },
                                openSettings: { showingSettings = true }, compact: true)
                                // Inline: a title item in the bar otherwise reserves a large title's height.
                                .navigationBarTitleDisplayMode(.inline)
                                .toolbarBackground(Palette.page, for: .navigationBar)
                                .toolbar {
                                    ToolbarItem(placement: .topBarLeading) {
                                        Button { setOpen(true) } label: {
                                            Image(systemName: "line.3.horizontal")
                                        }
                                        .tint(.primary)
                                        .accessibilityLabel("所有 Bot")
                                    }
                                    if UIDevice.current.userInterfaceIdiom == .pad {
                                        PadToolbar { showingSettings = true }
                                    }
                                }
                        } else if let bot {
                            PhoneBotPage(bot: bot, cardsShown: $cardsShown) { setOpen(true) }
                                .toolbar {
                                    if UIDevice.current.userInterfaceIdiom == .pad {
                                        PadToolbar { showingSettings = true }
                                    }
                                }
                        } else {
                            Palette.page.ignoresSafeArea()
                        }
                    }
                    .id(showsDashboard ? DashboardPage.id : bot?.id)
                    .environment(\.sidewaysZones, sideways)
                    // While the Bots show, nothing on the page takes a tap: a menu in its chat box
                    // (drawn by UIKit where the page was) caught taps meant for the list.
                    .allowsHitTesting(shift == 0)
                    .overlay {
                        // The page while the Bots show: dimmed a little, a tap closes the list.
                        Color.black.opacity(0.1 * shift / width)
                            .ignoresSafeArea()
                            .allowsHitTesting(shift > 0)
                            .onTapGesture { setOpen(false) }
                    }
                    .overlay {
                        // The page's edge, which the dark page and list otherwise blur together.
                        RoundedRectangle(cornerRadius: 28)
                            .strokeBorder(Palette.hairline.opacity(shift > 0 ? 1 : 0))
                            .ignoresSafeArea()
                            .allowsHitTesting(false)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: shift > 0 ? 28 : 0))
                    .shadow(color: .black.opacity(shift > 0 ? 0.12 : 0), radius: 16)
                    .offset(x: shift)
                    // The page reaches under the status bar and the home indicator, so its color
                    // goes all the way (the list underneath showed there as a darker band); the
                    // navigation bar still keeps clear of the status bar.
                    .ignoresSafeArea(.container, edges: .vertical)
                }
                .simultaneousGesture(swipe(width: width))
            }
            .background(Palette.sidebar.ignoresSafeArea())
            // One accent for the App's own controls (settings, buttons); a Bot's page uses its
            // color only for its cards.
            .tint(Palette.clay)
            // Back from the background: up to date at once, not at the next poll.
            .onChange(of: scenePhase) { _, phase in
                switch phase {
                case .active:
                    // Back after more than an hour: 仪表盘 first, as when the App starts.
                    if AwayClock.wasAway(), !showsDashboard {
                        chosen = DashboardPage.id
                        setOpen(false)
                    }
                    AwayClock.mark()
                case .background:
                    AwayClock.mark()
                default:
                    break
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: PhoneReach.routeNotification)) { note in
                guard let id = note.object as? String else { return }
                showingSettings = false
                choose(id)
            }
            .sheet(isPresented: $showingSettings) {
                PhoneSettings { id in
                    showingSettings = false
                    choose(id)
                }
                .tint(Palette.clay)
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
            cardsShown = false
            chosen = id
            setOpen(false)
        }

        func setOpen(_ value: Bool) {
            if value { hideKeyboard() }
            withAnimation(reduceMotion ? nil : .snappy(duration: 0.3)) {
                open = value
                drag = 0
            }
        }

        /// A sideways swipe anywhere moves the page with the finger; one mostly up or down is the
        /// page scrolling and is left alone, and so is one on a row that scrolls sideways itself.
        func swipe(width: CGFloat) -> some Gesture {
            DragGesture(minimumDistance: 14, coordinateSpace: .global)
                .onChanged { value in
                    let dx = value.translation.width, dy = value.translation.height
                    if !swiping {
                        guard abs(dx) > abs(dy) * 1.5, open ? dx < 0 : dx > 0 else { return }
                        guard open || !sideways.contains(value.startLocation) else { return }
                        swiping = true
                        if !open { hideKeyboard() }
                    }
                    drag = dx
                }
                .onEnded { value in
                    guard swiping else { return }
                    swiping = false
                    let landing = (open ? width : 0) + value.predictedEndTranslation.width
                    setOpen(landing > width / 2)
                }
        }
    }

    /// 设置 in a sheet from the gear under the Bots.
    struct PhoneSettings: View {
        @Environment(\.dismiss) private var dismiss
        let openBot: (String) -> Void

        var body: some View {
            NavigationStack {
                SettingsHome(openBot: openBot)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("完成") { dismiss() }
                        }
                    }
            }
        }
    }

    /// 知识库 in a sheet from 知识库Bot's page.
    struct PhoneKnowledge: View {
        @Environment(\.dismiss) private var dismiss

        var body: some View {
            NavigationStack {
                KnowledgeHome()
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("完成") { dismiss() }
                        }
                    }
            }
        }
    }

    /// The chat box's keyboard would cover the list of Bots.
    @MainActor func hideKeyboard() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }

#endif
