#if os(iOS)
    import AssistantCore
    import SwiftUI
    import UIKit

    /// The iPhone App, after the user's sketch: one Bot's page fills the screen (its face on top,
    /// its cards, the wavy line, then the chat). Swiping right, or the button at the top left,
    /// slides the page aside to show every Bot, with settings at the bottom, like the sidebar of
    /// Claude's iPhone app. The App reopens the Bot that was open last.
    public struct PhoneRootView: View {
        @EnvironmentObject private var store: AppStore
        @AppStorage("assistant.phone.bot") private var chosen = Bot.assistantID
        @State private var open = false
        /// How far a swipe has moved the page so far.
        @State private var drag: CGFloat = 0
        @State private var swiping = false
        @State private var showingSettings = false
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        public init(botStorageKey: String = "assistant.phone.bot") {
            _chosen = AppStorage(wrappedValue: Bot.assistantID, botStorageKey)
        }

        /// A plugin that went away shows the assistant; nil while the plugins are still loading,
        /// so the App does not flash the assistant before the Bot that was open last.
        var bot: Bot? {
            let bots = store.bots
            if let bot = bots.first(where: { $0.id == chosen }) { return bot }
            return store.settled ? bots[0] : nil
        }

        public var body: some View {
            GeometryReader { proxy in
                let width = min(proxy.size.width * 0.82, 340)
                let shift = min(max((open ? width : 0) + drag, 0), width)
                ZStack(alignment: .leading) {
                    PhoneDrawer(chosen: bot?.id ?? chosen, choose: choose) {
                        showingSettings = true
                    }
                    .frame(width: width)
                    // Comes in a little slower than the page moves away.
                    .offset(x: (shift - width) * 0.2)
                    .ignoresSafeArea(.keyboard)
                    NavigationStack {
                        if let bot {
                            PhoneBotPage(bot: bot) { setOpen(true) }
                                .toolbar {
                                    if UIDevice.current.userInterfaceIdiom == .pad {
                                        PadToolbar { showingSettings = true }
                                    }
                                }
                        } else {
                            Palette.page.ignoresSafeArea()
                        }
                    }
                    .id(bot?.id)
                    .overlay {
                        // The page while the Bots show: dimmed, a tap closes the list.
                        Color.black.opacity(0.18 * shift / width)
                            .ignoresSafeArea()
                            .allowsHitTesting(shift > 0)
                            .onTapGesture { setOpen(false) }
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
            .task { store.start() }
            .sheet(isPresented: $showingSettings) {
                PhoneSettings { id in
                    showingSettings = false
                    choose(id)
                }
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
        /// page scrolling and is left alone.
        func swipe(width: CGFloat) -> some Gesture {
            DragGesture(minimumDistance: 14)
                .onChanged { value in
                    let dx = value.translation.width, dy = value.translation.height
                    if !swiping {
                        guard abs(dx) > abs(dy) * 1.5, open ? dx < 0 : dx > 0 else { return }
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
