#if os(macOS)
    import AppKit
    import AssistantCore
    import SwiftUI

    /// The menu bar item: the assistant's sticker in its current state, and how many todos
    /// are left today. The menu bar cannot animate, so it shows the still pose of the state
    /// (tilted, grayed out, sweat or zzz); the window that opens below it is animated.
    public struct MacMenuBarLabel: View {
        @ObservedObject var store: AppStore

        public init(store: AppStore) {
            self.store = store
        }

        public var body: some View {
            HStack(spacing: 3) {
                Image(nsImage: StickerThumbnail.image(look: StickerCatalog.main.assistant, state: store.assistantState))
                Text("\(store.remainingToday)")
            }
            .onAppear { store.start() }
            .accessibilityLabel("今日剩余 \(store.remainingToday) 件待办，\(MascotView.label(for: store.assistantState))")
        }
    }

    /// Small still images of a sticker per state, for the menu bar: cropped to the character, so
    /// a wide head gets a wider image instead of shrinking to a speck in a square.
    @MainActor
    enum StickerThumbnail {
        static var cache: [String: NSImage] = [:]

        static func image(look: StickerLook, state: MascotState, height: CGFloat = 18) -> NSImage {
            let name = look.resolved(in: .main)
            let key = "\(name)|\(state.rawValue)|\(height)"
            if let cached = cache[key] { return cached }
            let bounds = StickerBounds.of(name)
            let width = min(height * bounds.width / bounds.height, height * 1.8)
            let side = height / bounds.height
            let figure = MascotFigure(
                name: name, pose: MascotPose.at(0, state: state, reduceMotion: true), side: side)
            let cropped = figure
                .frame(width: side, height: side)
                .offset(x: (width - bounds.width * side) / 2 - bounds.minX * side, y: -bounds.minY * side)
                .frame(width: width, height: height, alignment: .topLeading)
                .clipped()
            let renderer = ImageRenderer(content: cropped)
            renderer.scale = 2
            let image = renderer.nsImage ?? NSImage(size: NSSize(width: width, height: height))
            image.size = NSSize(width: width, height: height)
            cache[key] = image
            return image
        }
    }

    /// The window under the menu bar item: the animated sticker, today's todos to tick off,
    /// and shortcuts.
    public struct MacMenuBarContent: View {
        @EnvironmentObject private var store: AppStore
        @Environment(\.openWindow) private var openWindow

        public init() {}

        public var body: some View {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    MascotView(
                        look: StickerCatalog.main.assistant, state: store.assistantState,
                        since: store.stateSince
                    )
                    .frame(width: 56, height: 56)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("今日剩余 \(store.remainingToday) 件待办").font(.headline)
                        Text(store.online ? MascotView.label(for: store.assistantState) : "连不上 Mac")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Divider()
                let todos = Array((store.today?.todos ?? []).prefix(6))
                if todos.isEmpty {
                    Text("今天没有要做的事").foregroundStyle(.secondary)
                }
                ForEach(todos) { todo in
                    Button {
                        Task { await store.toggleDone(todo) }
                    } label: {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: todo.status == .completed ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(todo.status == .completed ? Color.green : Color.secondary)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(todo.title)
                                    .strikethrough(todo.status == .completed)
                                    .lineLimit(2)
                                Text(DueText.describe(todo)).font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer()
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                Divider()
                HStack {
                    Button("立即提炼一次") { Task { await store.runNow() } }
                        .disabled(!store.online || store.isJobRunning)
                    Spacer()
                    Button("打开主窗口") {
                        openWindow(id: "main")
                        NSApp.activate(ignoringOtherApps: true)
                    }
                    Button("退出") { NSApp.terminate(nil) }
                }
            }
            .padding(14)
            .frame(width: 320)
        }
    }
#endif
