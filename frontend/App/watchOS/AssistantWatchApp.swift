import AssistantUI
import SwiftUI

@main
struct AssistantWatchApp: App {
    /// Data comes from the iPhone over WatchConnectivity; the watch never contacts the Mac.
    @StateObject private var store = WatchStore()

    var body: some Scene {
        WindowGroup {
            WatchRootView()
                .environmentObject(store)
        }
    }
}
