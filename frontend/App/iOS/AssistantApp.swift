import AssistantUI
import SwiftUI
import UIKit

@main
struct AssistantApp: App {
    @StateObject private var store: AppStore

    init() {
        let store = AppStore.standard()
        _store = StateObject(wrappedValue: store)
        // Also runs when iOS starts the App in the background for a watch request, or for new
        // health samples (HealthKit background delivery).
        if UIDevice.current.userInterfaceIdiom != .pad {
            PhoneWatchBridge.shared.attach(store)
            HealthSync.shared.attach(store)
        }
    }

    var body: some Scene {
        WindowGroup {
            AdaptiveRootView()
                .environmentObject(store)
        }
    }
}
