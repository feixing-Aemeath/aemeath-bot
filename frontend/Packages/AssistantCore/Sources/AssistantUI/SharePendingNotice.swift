#if !os(watchOS)
import AssistantCore
import SwiftUI

struct SharePendingNotice: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        if let text = ShareDeliveryStatus.pendingText(store.pendingShareCount) {
            Label(text, systemImage: "paperplane")
                .font(.footnote).foregroundStyle(.secondary)
                .padding(.vertical, 6).accessibilityLabel(text)
        }
    }
}
#endif
