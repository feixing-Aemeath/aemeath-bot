#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    struct UnsureRow: View {
        @EnvironmentObject private var store: AppStore
        @State private var expanded = false
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { expanded.toggle() }
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "questionmark.circle")
                        Text("还有 \(store.visibleUnsure.count) 条没看清，要你确认")
                        Spacer(minLength: 0)
                        Image(systemName: expanded ? "chevron.up" : "chevron.down")
                    }
                    .font(CardFont.note.weight(.semibold)).foregroundStyle(.orange)
                    .padding(.vertical, 6).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(expanded ? "收起还不确定的条目" : "展开 \(store.visibleUnsure.count) 条还不确定的条目")
                if expanded { UnsureItems(expanded: false) }
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color.orange.opacity(0.08)))
        }
    }

    struct UnsureItems: View {
        @EnvironmentObject private var store: AppStore
        var expanded: Bool
        @State private var adding: UnsureItem?
        var body: some View {
            VStack(alignment: .leading, spacing: 12) {
                if expanded { Text("还不确定").font(ChatFont.title).foregroundStyle(.orange) }
                ForEach(store.visibleUnsure) { item in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(item.text).font(CardFont.body).fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: 16) {
                            if item.canOpen || store.openingUnsure[item.id] != nil {
                                Button(store.openingUnsure[item.id] == nil ? "读全文" : "正在读…") {
                                    Task { await store.openUnsure(item) }
                                }
                                .disabled(store.openingUnsure[item.id] != nil)
                                .accessibilityLabel("读全文：" + item.text)
                            }
                            Button("记成待办") { adding = item }.accessibilityLabel("记成待办：" + item.text)
                            Button("不用管") { store.dismissUnsure(item) }.accessibilityLabel(
                                "今天不用管：" + item.text)
                        }
                        .font(CardFont.note).buttonStyle(.borderless)
                        if let failure = store.openFailures[item.id] {
                            Text(failure).font(CardFont.note).foregroundStyle(.orange)
                        }
                    }
                }
            }
            .sheet(item: $adding) { item in ManualEntryComposer(title: item.text) }
        }
    }
#endif
