import AssistantUI
import SwiftUI

#if DEBUG
    import AssistantCore
#endif

@main
struct AssistantWatchApp: App {
    /// Data comes from the iPhone over WatchConnectivity; the watch never contacts the Mac.
    @StateObject private var store = WatchStore()

    var body: some Scene {
        WindowGroup {
            root.environmentObject(store)
        }
    }

    @ViewBuilder var root: some View {
        #if DEBUG
            let gallery = UserDefaults.standard.integer(forKey: "watch.gallery")
            if gallery > 0 {
                ComplicationGallery(entry: SnapshotEntry(date: store.now(), snapshot: store.snapshot), set: gallery)
            } else {
                WatchRootView()
            }
        #else
            WatchRootView()
        #endif
    }
}
