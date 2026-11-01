import AssistantCore
import ImageIO
import SwiftUI

#if canImport(UIKit)
    import UIKit
#elseif canImport(AppKit)
    import AppKit
#endif

extension MascotColor {
    public var color: Color { Color(red: red, green: green, blue: blue) }
}

/// Decoded sticker images, loaded once from the App bundle's "stickers" folder.
final class StickerImages: @unchecked Sendable {
    static let shared = StickerImages()
    private var cache: [String: Image] = [:]
    private let lock = NSLock()

    func image(named name: String) -> Image? {
        lock.lock()
        defer { lock.unlock() }
        if let cached = cache[name] { return cached }
        guard let url = StickerCatalog.url(of: name), let image = Self.decode(url) else {
            return nil
        }
        cache[name] = image
        return image
    }

    static func decode(_ url: URL) -> Image? {
        #if canImport(UIKit)
            guard let image = UIImage(contentsOfFile: url.path) else { return nil }
            return Image(uiImage: image)
        #elseif canImport(AppKit)
            guard let image = NSImage(contentsOf: url) else { return nil }
            return Image(nsImage: image)
        #else
            return nil
        #endif
    }
}

/// One sticker's eye layers (StickerLayer), each cropped to where it has something, with its
/// place on the sticker in fractions of the side.
struct LayeredImages: @unchecked Sendable {
    struct Piece {
        let image: Image
        let rect: CGRect
    }

    let base: Piece
    let open: Piece
    let closed: Piece
    let mask: Piece?

    /// Between the eyes, where the face looks from.
    var eyeCenter: CGPoint { CGPoint(x: closed.rect.midX, y: closed.rect.midY) }

    static func load(_ name: String, maxPixels: Int, bundle: Bundle = .main) -> LayeredImages? {
        func piece(_ layer: StickerLayer) -> Piece? {
            StickerCatalog.url(of: name, layer: layer, in: bundle).flatMap {
                decode($0, maxPixels: maxPixels)
            }
        }
        guard let base = piece(.base), let open = piece(.eyesOpen), let closed = piece(.eyesClosed) else {
            return nil
        }
        return LayeredImages(base: base, open: open, closed: closed, mask: piece(.eyesMask))
    }

    /// A layer at most `maxPixels` wide, cropped to its pixels that are not fully transparent.
    static func decode(_ url: URL, maxPixels: Int) -> Piece? {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixels,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
            let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary),
            let bounds = visibleBounds(of: image), let cropped = image.cropping(to: bounds)
        else { return nil }
        let width = CGFloat(image.width)
        let height = CGFloat(image.height)
        return Piece(
            image: Image(decorative: cropped, scale: 1),
            rect: CGRect(
                x: bounds.minX / width, y: bounds.minY / height, width: bounds.width / width,
                height: bounds.height / height))
    }

    /// The smallest rectangle (from the top left) holding every pixel that shows.
    static func visibleBounds(of image: CGImage) -> CGRect? {
        let width = image.width
        let height = image.height
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard
                let context = CGContext(
                    data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                    bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
            else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        var minX = width
        var minY = height
        var maxX = -1
        var maxY = -1
        for y in 0..<height {
            for x in 0..<width where pixels[(y * width + x) * 4 + 3] > 0 {
                minX = min(minX, x)
                maxX = max(maxX, x)
                minY = min(minY, y)
                maxY = max(maxY, y)
            }
        }
        guard maxX >= minX else { return nil }
        return CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
    }
}

/// Eye layers, decoded once per sticker in the background; until they are ready the whole
/// sticker is drawn, which looks the same. Decoded only as large as the biggest mascot needs.
final class StickerLayers: @unchecked Sendable {
    static let shared = StickerLayers()
    private var cache: [String: LayeredImages] = [:]
    private var started: Set<String> = []
    private var boundsCache: [String: CGRect] = [:]
    private var eyesCache: [String: CGRect?] = [:]
    private let lock = NSLock()

    #if os(watchOS)
        static let maxPixels = 256
    #else
        static let maxPixels = 768
    #endif

    /// A tiny synchronous measurement also gives motion-reduced/background views the right
    /// placement before the full eye layers arrive; those views have no timeline to redraw.
    func bounds(named name: String) -> CGRect {
        lock.lock()
        defer { lock.unlock() }
        if let bounds = boundsCache[name] { return bounds }
        let bounds = StickerCatalog.url(of: name).flatMap { LayeredImages.decode($0, maxPixels: 128)?.rect }
            ?? CGRect(x: 0, y: 0, width: 1, height: 1)
        boundsCache[name] = bounds
        return bounds
    }

    /// Where the open eyes are, measured the same way; nil for a sticker without eye layers.
    func eyes(named name: String) -> CGRect? {
        lock.lock()
        defer { lock.unlock() }
        if let eyes = eyesCache[name] { return eyes }
        let eyes = StickerCatalog.url(of: name, layer: .eyesOpen).flatMap {
            LayeredImages.decode($0, maxPixels: 128)?.rect
        }
        eyesCache[name] = eyes
        return eyes
    }

    /// The layers when they are loaded; otherwise starts loading them and returns nil.
    func layers(named name: String) -> LayeredImages? {
        lock.lock()
        defer { lock.unlock() }
        if let cached = cache[name] { return cached }
        guard !started.contains(name) else { return nil }
        started.insert(name)
        DispatchQueue.global(qos: .userInitiated).async {
            guard let layers = LayeredImages.load(name, maxPixels: Self.maxPixels) else { return }
            self.lock.lock()
            self.cache[name] = layers
            self.lock.unlock()
        }
        return nil
    }
}

/// One sticker image, or a drawn stand-in when the App bundle has no stickers (previews).
public struct StickerImage: View {
    let name: String

    public init(name: String) {
        self.name = name
    }

    public var body: some View {
        if let image = StickerImages.shared.image(named: name) {
            image.resizable().interpolation(.high).scaledToFit()
        } else {
            DrawnPlaceholder()
        }
    }
}

struct DrawnPlaceholder: View {
    var body: some View {
        GeometryReader { proxy in
            let side = min(proxy.size.width, proxy.size.height)
            ZStack {
                Circle().fill(Color(red: 0.79, green: 0.8, blue: 0.85))
                Circle().stroke(Color(red: 0.19, green: 0.16, blue: 0.25), lineWidth: max(1, side * 0.04))
                Image(systemName: "questionmark")
                    .resizable()
                    .scaledToFit()
                    .foregroundStyle(Color.white)
                    .padding(side * 0.28)
            }
            .frame(width: side, height: side)
        }
    }
}

/// A sticker drawn from its eye layers: the face, then the eyes, moved and blinking, kept on
/// the face by the mask so that eyes looking sideways slide under the hair.
struct LayeredSticker: View {
    let layers: LayeredImages
    let eyes: MascotEyes
    let side: CGFloat

    var body: some View {
        ZStack(alignment: .topLeading) {
            place(layers.base)
            ZStack(alignment: .topLeading) {
                if eyes.closure < 1 {
                    place(layers.open).opacity(1 - eyes.closure)
                }
                if eyes.closure > 0 {
                    place(layers.closed).opacity(eyes.closure)
                }
            }
            .frame(width: side, height: side, alignment: .topLeading)
            .offset(x: eyes.offsetX * side, y: eyes.offsetY * side)
            .mask(alignment: .topLeading) {
                if let mask = layers.mask {
                    place(mask).frame(width: side, height: side, alignment: .topLeading)
                } else {
                    Rectangle()
                }
            }
        }
        .frame(width: side, height: side, alignment: .topLeading)
    }

    private func place(_ piece: LayeredImages.Piece) -> some View {
        piece.image
            .resizable()
            .interpolation(.high)
            .frame(width: piece.rect.width * side, height: piece.rect.height * side)
            .offset(x: piece.rect.minX * side, y: piece.rect.minY * side)
    }
}

/// One frame: the sticker transformed around its bottom center, overlays on top. With `eyes`
/// a sticker with eye layers is drawn from them once they are loaded; without, or until then,
/// the whole image, which looks the same as its layers with the eyes open and ahead.
struct MascotFigure: View {
    let name: String
    let pose: MascotPose
    let side: CGFloat
    var eyes: MascotEyes?
    var artwork: LayeredImages?

    var anchor: UnitPoint { UnitPoint(x: MascotPose.anchor.x, y: MascotPose.anchor.y) }

    var body: some View {
        ZStack {
            sticker
                .frame(width: side, height: side)
                .saturation(pose.saturation)
                .scaleEffect(x: pose.scaleX, y: pose.scaleY, anchor: anchor)
                .rotationEffect(.degrees(pose.rotation), anchor: anchor)
                .offset(x: pose.offsetX * side, y: pose.offsetY * side)
            if pose.effects.contains(.sweat) {
                Image(systemName: "drop.fill")
                    .resizable()
                    .scaledToFit()
                    .foregroundStyle(MascotPalette.sweat.color)
                    .frame(width: side * 0.1, height: side * 0.14)
                    .position(x: side * 0.8, y: side * (0.22 + 0.14 * pose.effectPhase))
                    .opacity(1 - 0.5 * pose.effectPhase)
            }
            if pose.effects.contains(.zzz) {
                Image(systemName: "zzz")
                    .resizable()
                    .scaledToFit()
                    .foregroundStyle(MascotPalette.sleep.color)
                    .frame(width: side * 0.24, height: side * 0.2)
                    .position(x: side * 0.84, y: side * (0.2 - 0.1 * pose.effectPhase))
                    .opacity(1 - 0.6 * pose.effectPhase)
            }
        }
        .frame(width: side, height: side)
    }

    @ViewBuilder private var sticker: some View {
        if let eyes, let layers = artwork ?? StickerLayers.shared.layers(named: name) {
            LayeredSticker(layers: layers, eyes: eyes, side: side)
        } else {
            StickerImage(name: name)
        }
    }
}

/// The hearts and sparkles of a poke.
struct MascotBurst: View {
    let particles: [MascotParticle]
    let side: CGFloat

    var body: some View {
        ZStack {
            ForEach(particles.indices, id: \.self) { index in
                let particle = particles[index]
                let heart = particle.kind == .heart
                Image(systemName: heart ? "heart.fill" : "sparkle")
                    .resizable()
                    .scaledToFit()
                    .foregroundStyle((heart ? MascotPalette.heart : MascotPalette.sparkle).color)
                    .frame(width: particle.size * side, height: particle.size * side)
                    .rotationEffect(.degrees(particle.rotation))
                    .opacity(particle.opacity)
                    .position(x: particle.x * side, y: particle.y * side)
            }
        }
        .frame(width: side, height: side)
        .allowsHitTesting(false)
    }
}

