#if os(macOS)
    import AppKit
    import AssistantCore
    import SwiftUI

    /// What a Bot page keeps about its scrolling. A class, so the many updates while scrolling
    /// redraw nothing.
    final class PageScroll {
        var fold = CardsFold()
        weak var view: NSScrollView?
        var cardsHeight: CGFloat = 0
        /// A fold is on its way; the next scroll updates must not start another.
        var folding = false
    }

    /// Where the page's content starts, where its cards end and how tall they are, in the
    /// scroll view's space. Each part is reported by the view it is about.
    struct PageGeometry: Equatable {
        var top: CGFloat?
        var cardsEnd: CGFloat?
        var cardsHeight: CGFloat?
    }

    struct PageGeometryKey: PreferenceKey {
        static let defaultValue = PageGeometry()

        static func reduce(value: inout PageGeometry, nextValue: () -> PageGeometry) {
            let next = nextValue()
            value.top = next.top ?? value.top
            value.cardsEnd = next.cardsEnd ?? value.cardsEnd
            value.cardsHeight = next.cardsHeight ?? value.cardsHeight
        }
    }

    extension View {
        func reportGeometry(_ part: @escaping (GeometryProxy) -> PageGeometry) -> some View {
            background(
                GeometryReader { proxy in
                    Color.clear.preference(key: PageGeometryKey.self, value: part(proxy))
                })
        }
    }

    /// Hands over the AppKit scroll view behind a SwiftUI ScrollView, which SwiftUI cannot
    /// scroll to an exact offset on macOS 14.
    struct ScrollViewFinder: NSViewRepresentable {
        let found: (NSScrollView) -> Void

        func makeNSView(context: Context) -> NSView { Probe(found: found) }

        func updateNSView(_ nsView: NSView, context: Context) {}

        final class Probe: NSView {
            let found: (NSScrollView) -> Void

            init(found: @escaping (NSScrollView) -> Void) {
                self.found = found
                super.init(frame: .zero)
            }

            required init?(coder: NSCoder) { nil }

            override func viewDidMoveToWindow() {
                super.viewDidMoveToWindow()
                if let scrollView = enclosingScrollView { found(scrollView) }
            }
        }
    }
#endif
