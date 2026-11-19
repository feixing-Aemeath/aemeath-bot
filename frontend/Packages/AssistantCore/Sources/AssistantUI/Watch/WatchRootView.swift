#if os(watchOS)
    import AssistantCore
    import SwiftUI

    /// The watch App: four pages turned with the Digital Crown, each answering what a raise of
    /// the wrist asks: 此刻 (what now, what next), 待办 (ticking off), 课表 (the day), 状态
    /// (readiness and sleep). All of it comes from the iPhone (WatchSync).
    public struct WatchRootView: View {
        @EnvironmentObject private var store: WatchStore
        @State private var page = Self.firstPage
        @Environment(\.scenePhase) private var scenePhase

        public init() {}

        /// 此刻; in a development build `-page courses` (or `todos`, `health`) at launch opens that
        /// page, for screenshots in the simulator.
        static var firstPage: WatchPage {
            #if DEBUG
                if let name = UserDefaults.standard.string(forKey: "page"), let page = WatchPage(rawValue: name) {
                    return page
                }
            #endif
            return .now
        }

        public var body: some View {
            TabView(selection: $page) {
                NavigationStack { WatchNowView(isCurrent: page == .now) { page = $0 } }.tag(WatchPage.now)
                NavigationStack { WatchTodosView(isCurrent: page == .todos) }.tag(WatchPage.todos)
                NavigationStack { WatchCoursesView() }.tag(WatchPage.courses)
                NavigationStack { WatchHealthView() }.tag(WatchPage.health)
            }
            .tabViewStyle(.verticalPage)
            .onOpenURL { url in
                if let target = WatchPage(url: url) { page = target }
            }
            .task { store.start() }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { store.refreshIfStale() }
            }
        }
    }
#endif
