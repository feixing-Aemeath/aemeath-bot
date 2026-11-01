#if os(iOS)
    import AssistantCore
    import SwiftUI
    import UIKit

    /// The iPhone keeps its drawer. An iPad uses the sidebar when there is room, including
    /// after a split-screen resize; both iPad layouts remember the same Bot on this device.
    /// It also starts loading Songti, for titles in the Mac's serif Chinese.
    public struct AdaptiveRootView: View {
        @Environment(\.horizontalSizeClass) private var sizeClass

        public init() {}

        public var body: some View {
            layout.task { ChineseSerif.shared.load() }
        }

        @ViewBuilder private var layout: some View {
            if UIDevice.current.userInterfaceIdiom == .pad {
                if sizeClass == .regular {
                    PadRootView()
                } else {
                    PhoneRootView(botStorageKey: PadPresentation.botStorageKey)
                }
            } else {
                PhoneRootView()
            }
        }
    }
#endif
