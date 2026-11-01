#if os(macOS)
    import AssistantCore
    import SwiftUI

    /// 设置 on the Mac: the same short menu as on the iPhone (SettingsHome), each topic opening
    /// its own page in the window.
    struct MacSettingsPage: View {
        /// Shows a Bot's page, from 消息来源.
        let openBot: (String) -> Void

        var body: some View {
            SettingsHome(openBot: openBot)
        }
    }
#endif
