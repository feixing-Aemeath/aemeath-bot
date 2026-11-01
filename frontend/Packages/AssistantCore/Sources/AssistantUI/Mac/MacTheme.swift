#if os(macOS)
    import AppKit
    import SwiftUI

    /// A row of the sidebar: a gray rounded background when chosen, a lighter one under the
    /// mouse, the whole row clickable.
    struct SidebarItem<Label: View>: View {
        let selected: Bool
        let action: () -> Void
        @ViewBuilder let label: Label
        @State private var hovering = false

        var body: some View {
            Button(action: action) {
                label
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(selected ? Palette.selection : hovering ? Palette.hover : .clear))
                    .contentShape(RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .pointerHover($hovering)
            .animation(.easeOut(duration: 0.12), value: hovering)
            .accessibilityAddTraits(selected ? .isSelected : [])
        }
    }

    /// Whether the mouse is over a view, from where the window says the pointer is
    /// (PointerTracker) instead of onHover: onHover can miss the exit when a dialog takes the
    /// focus, and leave a sidebar row lit (and its face moving) until the mouse comes back.
    struct PointerHover: ViewModifier {
        @Environment(PointerTracker.self) private var pointer: PointerTracker?
        @Environment(\.controlActiveState) private var active
        @Binding var hovering: Bool
        @State private var frame = CGRect.zero

        func body(content: Content) -> some View {
            content
                .background(
                    GeometryReader { proxy in
                        Color.clear
                            .onAppear { frame = proxy.frame(in: .global) }
                            .onChange(of: proxy.frame(in: .global)) { _, new in frame = new }
                    }
                )
                .onChange(of: pointer?.location) { _, location in update(location) }
                .onChange(of: active) { _, state in
                    if state == .inactive { hovering = false }
                }
                .onDisappear { hovering = false }
        }

        func update(_ location: CGPoint?) {
            let inside = active != .inactive && location.map(frame.contains) == true
            if inside != hovering { hovering = inside }
        }
    }

    extension View {
        func pointerHover(_ hovering: Binding<Bool>) -> some View {
            modifier(PointerHover(hovering: hovering))
        }
    }
#endif
