#if os(macOS)
    import AssistantCore
    import SwiftUI

    /// What the right side of the Mac window shows; the App reopens the last one.
    enum MacSelection: Hashable {
        case dashboard
        case bot(String)
        case settings

        static let defaultsKey = "assistant.mac.selection"

        /// `dashboard`, `settings` or a Bot's id, as kept in UserDefaults; nothing kept yet is the
        /// dashboard. The knowledge base had a page of its own, kept as `knowledge`: that is now
        /// 知识库Bot's id, whose page opens it.
        init(stored: String?) {
            switch stored {
            case "settings": self = .settings
            case DashboardPage.id, nil: self = .dashboard
            case let id?: self = .bot(id)
            }
        }

        var stored: String {
            switch self {
            case .dashboard: return DashboardPage.id
            case .bot(let id): return id
            case .settings: return "settings"
            }
        }
    }

    /// Where the mouse is in the window (SwiftUI's global space), for the Bots' eyes to follow.
    /// Only the mascots read it, so moving the mouse redraws nothing else.
    @Observable
    final class PointerTracker {
        var location: CGPoint?
    }

    /// The Mac main window: on the left 仪表盘, then the Bots (the assistant first, then the
    /// characters, then below a line 知识库Bot, then the message sources) with settings at the
    /// bottom; on the right, the dashboard or the chosen Bot's page on a lighter background.
    public struct MacRootView: View {
        @AppStorage("assistant.mac.cardsPanel") private var cardsShown = true
        @EnvironmentObject private var store: AppStore
        @State private var selection: MacSelection? = MacSelection(
            stored: UserDefaults.standard.string(forKey: MacSelection.defaultsKey))
        @State private var pointer = PointerTracker()
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        public init() {}

        public var body: some View {
            NavigationSplitView {
                MacSidebar(selection: $selection)
                    .navigationSplitViewColumnWidth(min: 210, ideal: 240)
            } detail: {
                NavigationStack {
                    detail
                        .transition(.opacity)
                        .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: selection)
                }
                .background(Palette.page)
                .overlay(alignment: .top) { TitleBand() }
                // The window's toolbar draws no band of its own, so the sidebar's beige reaches
                // the top of the window; the page draws its own (TitleBand).
                .toolbarBackground(.hidden, for: .windowToolbar)
            }
            .environment(pointer)
            .tint(Palette.clay)
            .onContinuousHover(coordinateSpace: .global) { phase in
                switch phase {
                case .active(let location): pointer.location = location
                case .ended: pointer.location = nil
                }
            }
            .task { store.start() }
            .onChange(of: selection) {
                UserDefaults.standard.set(selection?.stored, forKey: MacSelection.defaultsKey)
            }
            .alert(
                "出错了",
                isPresented: Binding(
                    get: { store.message != nil },
                    set: { if !$0 { store.message = nil } }),
                actions: { Button("好") { store.message = nil } },
                message: { Text(store.message ?? "") })
        }

        @ViewBuilder var detail: some View {
            let bots = store.bots
            switch selection {
            case .dashboard, nil:
                DashboardPage(
                    openBot: { selection = .bot($0) },
                    openCards: { cardsShown = true; selection = .bot($0) },
                    openSettings: { selection = .settings })
                    .navigationTitle(DashboardPage.title)
                    .hiddenWindowTitle()
                    .toolbar { RefreshButton() }
            case .settings:
                MacSettingsPage { id in selection = .bot(id) }
            case .bot(let id):
                // A plugin that went away shows the assistant; while the plugins still load, the
                // page waits rather than flashing the assistant.
                if let bot = bots.first(where: { $0.id == id }) ?? (store.settled ? bots.first(where: \.isAssistant) : nil) {
                    MacBotPage(bot: bot, cardsShown: $cardsShown).id(bot.id)
                } else {
                    Palette.page
                }
            }
        }
    }

    /// Behind the page's title and toolbar buttons: the page's color and a hairline, so the
    /// page scrolls beneath a fixed title bar instead of showing through it.
    struct TitleBand: View {
        var body: some View {
            GeometryReader { proxy in
                VStack(spacing: 0) {
                    Palette.page.frame(height: proxy.safeAreaInsets.top)
                    Palette.hairline.frame(height: 1)
                }
                .offset(y: -proxy.safeAreaInsets.top)
            }
            .allowsHitTesting(false)
        }
    }

    /// The sidebar, on its own beige like Claude's: the Bots, a line, 知识库Bot, then the message
    /// sources, each a row with a gray rounded background when chosen. The arrow keys move the
    /// choice.
    struct MacSidebar: View {
        @EnvironmentObject private var store: AppStore
        @Binding var selection: MacSelection?

        var body: some View {
            let bots = store.bots
            BotSidebarList {
                SidebarItem(selected: selection == .dashboard) {
                    selection = .dashboard
                } label: { DashboardRow() }
            } row: { row($0) }
            .safeAreaInset(edge: .bottom) {
                SidebarFooter(selection: $selection)
            }
            .background(Palette.sidebar)
            .focusable()
            .focusEffectDisabled()
            .onKeyPress(.upArrow) { move(-1, among: bots) }
            .onKeyPress(.downArrow) { move(1, among: bots) }
        }

        func row(_ bot: Bot) -> some View {
            SidebarItem(selected: selection == .bot(bot.id)) {
                selection = .bot(bot.id)
            } label: {
                BotRow(bot: bot)
            }
        }

        func move(_ step: Int, among bots: [Bot]) -> KeyPress.Result {
            let choices = [MacSelection.dashboard] + bots.map { MacSelection.bot($0.id) }
            guard !choices.isEmpty else { return .ignored }
            let index = selection.flatMap { choices.firstIndex(of: $0) } ?? -step
            selection = choices[min(max(index + step, 0), choices.count - 1)]
            return .handled
        }
    }

    struct SidebarHeader: View {
        let title: String

        var body: some View {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 8)
                .padding(.top, 6)
                .padding(.bottom, 2)
        }
    }

    /// The bottom of the sidebar: whether the Mac is reachable, and settings.
    struct SidebarFooter: View {
        @Binding var selection: MacSelection?

        var body: some View {
            VStack(alignment: .leading, spacing: 6) {
                ConnectionBadge()
                    .padding(.horizontal, 8)
                SidebarItem(selected: selection == .settings) {
                    selection = .settings
                } label: {
                    Label("设置", systemImage: "gearshape")
                        .padding(.vertical, 2)
                }
            }
            .padding(10)
            .background(Palette.sidebar)
        }
    }

    struct RefreshButton: View {
        @EnvironmentObject private var store: AppStore

        var body: some View {
            Button {
                Task { await store.refresh() }
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .help("重新读取")
        }
    }

#endif
