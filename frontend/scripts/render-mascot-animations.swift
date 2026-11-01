import AppKit
import AssistantCore
import SwiftUI

/// Compiled with the two mascot UI sources by render-mascot-animations.py, so exports use
/// exactly the same Canvas, eye composition and placement as the running App.
@main
@MainActor
struct RenderMascotAnimations {
    static let moments = [0.03, 0.30, 0.60, 0.88]
    static let background = Color(red: 0.98, green: 0.97, blue: 0.95)

    static func main() throws {
        guard CommandLine.arguments.count == 3 else {
            fatalError("Usage: render-mascot-animations <stickers folder> <output folder>")
        }
        _ = NSApplication.shared
        let stickers = URL(fileURLWithPath: CommandLine.arguments[1])
        let output = URL(fileURLWithPath: CommandLine.arguments[2])
        let catalog = StickerCatalog.load(folder: stickers)
        var count = 0
        for name in catalog.sorted where !MascotAnimation.all(for: name).isEmpty {
            func piece(_ layer: StickerLayer) throws -> LayeredImages.Piece {
                let url = stickers.appendingPathComponent(name).appendingPathComponent(layer.rawValue + ".png")
                guard let image = LayeredImages.decode(url, maxPixels: 1024) else {
                    throw NSError(domain: "Missing layer: \(url.path)", code: 1)
                }
                return image
            }
            let artwork = try LayeredImages(base: piece(.base), open: piece(.eyesOpen),
                                            closed: piece(.eyesClosed), mask: piece(.eyesMask))
            let directory = output.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let acts = MascotAnimation.all(for: name)
            for act in acts {
                for (index, fraction) in moments.enumerated() {
                    let content = frame(act, at: fraction, artwork: artwork, side: 240)
                        .background(background)
                    try save(content, to: directory.appendingPathComponent("\(act.rawValue)-\(index + 1).png"), scale: 2)
                    count += 1
                }
            }
            let sheet = VStack(alignment: .leading, spacing: 12) {
                Text(catalog.characters[name] ?? name).font(.system(size: 24, weight: .bold))
                ForEach(acts, id: \.self) { act in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(act.title).font(.system(size: 15, weight: .semibold))
                        HStack(spacing: 8) {
                            ForEach(Array(moments.enumerated()), id: \.offset) { _, fraction in
                                VStack(spacing: 2) {
                                    frame(act, at: fraction, artwork: artwork, side: 160)
                                    Text(String(format: "%.2f s", fraction * act.duration)).font(.system(size: 11))
                                }
                            }
                        }
                    }
                }
            }
            .foregroundStyle(Color(red: 0.19, green: 0.16, blue: 0.25))
            .padding(20)
            .background(background)
            try save(sheet, to: output.appendingPathComponent("\(name)-contact.png"), scale: 1)
        }
        print("Exported \(count) frames and 6 contact sheets to \(output.path)")
    }

    static func frame(_ animation: MascotAnimation, at fraction: Double, artwork: LayeredImages, side: CGFloat) -> some View {
        let elapsed = fraction * animation.duration
        let basic = MascotMotion.frame(at: 100 + elapsed, state: .idle, since: 100,
                                       seed: MascotMotion.seed(for: animation.sticker))
        return MascotAnimationScene(name: animation.sticker, frame: basic,
                                    playback: MascotAnimationPlayback(animation, elapsed: elapsed),
                                    side: side, artwork: artwork)
    }

    static func save<V: View>(_ view: V, to url: URL, scale: CGFloat) throws {
        let renderer = ImageRenderer(content: view.environment(\.colorScheme, .light))
        renderer.scale = scale
        guard let cgImage = renderer.cgImage else {
            throw NSError(domain: "ImageRenderer failed: \(url.path)", code: 2)
        }
        let bitmap = NSBitmapImageRep(cgImage: cgImage)
        guard let data = bitmap.representation(using: .png, properties: [:]) else {
            throw NSError(domain: "PNG encoding failed", code: 3)
        }
        try data.write(to: url)
    }
}
