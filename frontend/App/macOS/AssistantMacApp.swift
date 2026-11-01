import AssistantUI
import SwiftUI

@main
struct AssistantMacApp: App {
    @StateObject private var store = AppStore.standard()

    var body: some Scene {
        WindowGroup("Aemeath Bot", id: "main") {
            MacRootView()
                .environmentObject(store)
                .frame(minWidth: 900, minHeight: 600)
        }
        // Today's remaining todos and the assistant's state, always visible.
        MenuBarExtra {
            MacMenuBarContent()
                .environmentObject(store)
        } label: {
            MacMenuBarLabel(store: store)
        }
        .menuBarExtraStyle(.window)
    }
}
