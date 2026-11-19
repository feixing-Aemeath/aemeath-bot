import AssistantUI
import SwiftUI
import UIKit

@main
struct AssistantApp: App {
    @Environment(\.scenePhase) private var scenePhase
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
        PhoneReach.shared.attach(store)
    }

    var body: some Scene {
        WindowGroup {
            AdaptiveRootView()
                .environmentObject(store)
                .onOpenURL { PhoneReach.shared.open($0) }
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            switch phase {
            case .active: store.start()
            case .background: store.stop()
            default: break
            }
        }
    }
}
