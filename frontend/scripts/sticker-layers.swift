// Splits stickers into eye layers for blinking and looking around (docs/mascot.md).
//
//     swift scripts/sticker-layers.swift Design/stickers/aemeath.png [more.png ...]
//     swift scripts/sticker-layers.swift --dark-eyes Design/stickers/orange_daisy.png
//
// (about 15 s per sticker; `swiftc -O scripts/sticker-layers.swift -o /tmp/sticker-layers` first
// makes it take a second.)
//
// For each <name>.png it finds the two eyes and writes, into <name>/ next to it and all on the
// sticker's canvas:
//   base.png         the sticker with the eyes painted over in the surrounding skin
//   eyes_open.png    only the eyes; base + eyes_open looks exactly like the sticker
//   eyes_closed.png  a closed eye per eye, a soft downward arc in a darker shade of its color
//   eyes_mask.png    where the eyes may show (the face around them), so eyes that look
//                    sideways slide under the hair instead of over it
// The eyes must be flat-colored ovals on a cream face, the house style; `--dark-eyes` looks for
// near-black ones instead (a sticker drawn without outlines). The output depends only
// on the input image. CoreGraphics and ImageIO only.

import CoreGraphics
import Foundation
import ImageIO

struct Failure: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

// MARK: Colors

/// An sRGB color, channels 0...255.
struct RGB {
    var r: Double
    var g: Double
    var b: Double

    init(_ r: Double, _ g: Double, _ b: Double) {
        self.r = r
        self.g = g
        self.b = b
    }

    static let black = RGB(0, 0, 0)

    static func + (lhs: RGB, rhs: RGB) -> RGB { RGB(lhs.r + rhs.r, lhs.g + rhs.g, lhs.b + rhs.b) }
    static func - (lhs: RGB, rhs: RGB) -> RGB { RGB(lhs.r - rhs.r, lhs.g - rhs.g, lhs.b - rhs.b) }
    static func * (lhs: RGB, k: Double) -> RGB { RGB(lhs.r * k, lhs.g * k, lhs.b * k) }

    func dot(_ other: RGB) -> Double { r * other.r + g * other.g + b * other.b }
    func distance(to other: RGB) -> Double { (self - other).dot(self - other).squareRoot() }
    var luma: Double { (0.299 * r + 0.587 * g + 0.114 * b) / 255 }
    var chroma: Double { max(r, g, b) - min(r, g, b) }
    var clamped: RGB { RGB(min(max(r, 0), 255), min(max(g, 0), 255), min(max(b, 0), 255)) }

    /// Degrees, 0..<360.
    var hue: Double {
        let c = chroma
        guard c > 0 else { return 0 }
        let top = max(r, g, b)
        let sector: Double
        if top == r {
            sector = (g - b) / c
        } else if top == g {
            sector = (b - r) / c + 2
        } else {
            sector = (r - g) / c + 4
        }
        let degrees = sector * 60
        return degrees < 0 ? degrees + 360 : degrees
    }

    /// How much of `toward` is mixed into this color on top of `from`, 0...1.
    func share(of toward: RGB, over from: RGB) -> Double {
        let axis = toward - from
        let length = axis.dot(axis)
        guard length > 1 else { return 0 }
        return min(max((self - from).dot(axis) / length, 0), 1)
    }

    /// The least opacity a layer over `below` needs to show this color with channels that
    /// stay within 0...255.
    func leastOpacity(over below: RGB) -> Double {
        func channel(_ value: Double, _ under: Double) -> Double {
            if value > under { return (value - under) / max(255 - under, 1e-9) }
            if value < under { return (under - value) / max(under, 1e-9) }
            return 0
        }
        return min(max(channel(r, below.r), channel(g, below.g), channel(b, below.b)), 1)
    }

    /// The most of `top` this color can hold over something else, keeping that something
    /// within 0...255.
    func mostShare(of top: RGB) -> Double {
        func channel(_ value: Double, _ over: Double) -> Double {
            if over > value { return value / over }
            if over < value { return (255 - value) / (255 - over) }
            return 1
        }
        return min(channel(r, top.r), channel(g, top.g), channel(b, top.b), 1)
    }
}

func hueDistance(_ a: Double, _ b: Double) -> Double {
    let d = abs(a - b)
    return min(d, 360 - d)
}

// MARK: Pixels

/// Straight (not premultiplied) RGBA pixels, rows from the top.
struct Canvas {
    let width: Int
    let height: Int
    var color: [RGB]
    var alpha: [Double]

    init(width: Int, height: Int) {
        self.width = width
        self.height = height
        color = Array(repeating: .black, count: width * height)
        alpha = Array(repeating: 0, count: width * height)
    }

    init(contentsOf url: URL) throws {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { throw Failure("cannot read \(url.path)") }
        self.init(width: image.width, height: image.height)
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = Canvas.context(buffer.baseAddress, width: width, height: height)
            else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { throw Failure("cannot decode \(url.path)") }
        for i in 0..<(width * height) {
            let a = Double(bytes[i * 4 + 3]) / 255
            alpha[i] = a
            guard a > 0 else { continue }
            color[i] = RGB(Double(bytes[i * 4]), Double(bytes[i * 4 + 1]), Double(bytes[i * 4 + 2])) * (1 / a)
        }
    }

    static func context(_ data: UnsafeMutableRawPointer?, width: Int, height: Int) -> CGContext? {
        CGContext(
            data: data, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    }

    func image() -> CGImage? {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        for i in 0..<(width * height) {
            let a = min(max(alpha[i], 0), 1)
            let c = color[i].clamped * a
            bytes[i * 4] = UInt8((c.r).rounded())
            bytes[i * 4 + 1] = UInt8((c.g).rounded())
            bytes[i * 4 + 2] = UInt8((c.b).rounded())
            bytes[i * 4 + 3] = UInt8((a * 255).rounded())
        }
        return bytes.withUnsafeMutableBytes { buffer in
            Canvas.context(buffer.baseAddress, width: width, height: height)?.makeImage()
        }
    }

    func neighbors(of i: Int, diagonal: Bool) -> [Int] {
        let x = i % width
        let y = i / width
        var result: [Int] = []
        for dy in -1...1 {
            for dx in -1...1 where (dx != 0 || dy != 0) && (diagonal || dx == 0 || dy == 0) {
                let nx = x + dx
                let ny = y + dy
                if nx >= 0, ny >= 0, nx < width, ny < height { result.append(ny * width + nx) }
            }
        }
        return result
    }
}

func writePNG(_ image: CGImage?, to url: URL) throws {
    guard let image,
        let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)
    else { throw Failure("cannot write \(url.path)") }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { throw Failure("cannot write \(url.path)") }
}

/// Distance in pixel steps (8-connected) from a set of seeds, and which seed is nearest, for
/// pixels up to `limit` away that `allowed` lets through (-1: farther or not allowed), and
/// every pixel reached.
func spread(
    from seeds: [Int], in canvas: Canvas, limit: Int, allowed: (Int) -> Bool = { _ in true }
) -> (distance: [Int], nearest: [Int], reached: [Int]) {
    var distance = [Int](repeating: -1, count: canvas.width * canvas.height)
    var nearest = distance
    var queue = seeds
    for seed in seeds {
        distance[seed] = 0
        nearest[seed] = seed
    }
    var head = 0
    while head < queue.count {
        let i = queue[head]
        head += 1
        guard distance[i] < limit else { continue }
        for n in canvas.neighbors(of: i, diagonal: true) where distance[n] < 0 && allowed(n) {
            distance[n] = distance[i] + 1
            nearest[n] = nearest[i]
            queue.append(n)
        }
    }
    return (distance, nearest, queue)
}

// MARK: Faces

/// The cream of the face and what else belongs to it (blush, soft shading).
struct Skin {
    let color: RGB

    /// The most common light warm color: the face is the largest flat area of the sticker.
    init?(_ canvas: Canvas) {
        var bins: [Int: (count: Int, sum: RGB)] = [:]
        for i in 0..<(canvas.width * canvas.height) where canvas.alpha[i] > 0.98 {
            let p = canvas.color[i]
            guard p.r > 200, p.r >= p.g, p.g >= p.b, (6...60).contains(p.r - p.b) else { continue }
            let key = Int(p.r) / 4 << 16 | Int(p.g) / 4 << 8 | Int(p.b) / 4
            let bin = bins[key] ?? (0, .black)
            bins[key] = (bin.count + 1, bin.sum + p)
        }
        // Ties go to the smaller key so the result never depends on dictionary order.
        guard let best = bins.max(by: { ($0.value.count, $1.key) < ($1.value.count, $0.key) })
        else { return nil }
        color = best.value.sum * (1 / Double(best.value.count))
    }

    /// Close to the face color and warm: light lavender or blue-white hair is not skin.
    func isSkin(_ p: RGB) -> Bool { p.distance(to: color) < 30 && p.r - p.b >= 4 }

    /// Skin or blush; blush is warmer than pink hair (green at least as high as blue).
    func isFace(_ p: RGB) -> Bool {
        if isSkin(p) { return true }
        return p.r >= 230 && (22...85).contains(p.r - p.g) && p.g - p.b >= -4 && p.g >= 160
    }
}

/// An oval in pixel coordinates (y down): `across` points right along its short axis, `down`
/// along its long one.
struct Oval {
    var centerX: Double
    var centerY: Double
    var across: (x: Double, y: Double)
    var halfWidth: Double
    var halfHeight: Double

    var down: (x: Double, y: Double) { (-across.y, across.x) }

    /// 0 at the center, 1 on the outline.
    func radius(_ x: Double, _ y: Double) -> Double {
        let dx = x - centerX
        let dy = y - centerY
        let u = (dx * across.x + dy * across.y) / halfWidth
        let v = (dx * down.x + dy * down.y) / halfHeight
        return (u * u + v * v).squareRoot()
    }

    func radius(of i: Int, width: Int) -> Double {
        radius(Double(i % width) + 0.5, Double(i / width) + 0.5)
    }

    func contains(_ x: Double, _ y: Double) -> Bool { radius(x, y) <= 1 }

    /// Share of pixel `i` inside the oval, sampled 4 × 4 for soft edges.
    func coverage(of i: Int, width: Int) -> Double {
        let x = Double(i % width)
        let y = Double(i / width)
        var inside = 0
        for sy in 0..<4 {
            for sx in 0..<4 where contains(x + (Double(sx) + 0.5) / 4, y + (Double(sy) + 0.5) / 4) {
                inside += 1
            }
        }
        return Double(inside) / 16
    }

    /// The same oval moved to sit best on `points`, searching around `start`.
    func moved(toFit points: [(x: Double, y: Double)], from start: Oval) -> Oval {
        guard !points.isEmpty else { return self }
        var best = self
        best.centerX = start.centerX
        best.centerY = start.centerY
        func cost(_ oval: Oval) -> Double {
            points.reduce(0) { sum, point in
                let off = oval.radius(point.x, point.y) - 1
                return sum + off * off
            }
        }
        var bestCost = cost(best)
        for step in [4.0, 1.0, 0.25] {
            let around = best
            for dy in -8...8 {
                for dx in -8...8 {
                    var oval = around
                    oval.centerX += Double(dx) * step
                    oval.centerY += Double(dy) * step
                    let value = cost(oval)
                    if value < bestCost {
                        bestCost = value
                        best = oval
                    }
                }
            }
        }
        return best
    }

    func point(across a: Double, down d: Double) -> CGPoint {
        CGPoint(x: centerX + across.x * a + down.x * d, y: centerY + across.y * a + down.y * d)
    }

    /// The best oval through edge points, fitted again without the points far off it (corners
    /// where hair meets the eye), nil when they do not describe a sensible ellipse.
    static func fit(_ points: [(x: Double, y: Double)]) -> Oval? {
        var kept = points
        var best: Oval?
        for _ in 0..<4 {
            guard let oval = fitOnce(kept) else { break }
            best = oval
            let close = points.filter { abs(oval.radius($0.x, $0.y) - 1) < 0.1 }
            if close.count < 12 || close.count == kept.count { break }
            kept = close
        }
        return best
    }

    /// Least squares on a general conic.
    static func fitOnce(_ points: [(x: Double, y: Double)]) -> Oval? {
        guard points.count >= 12 else { return nil }
        let meanX = points.map(\.x).reduce(0, +) / Double(points.count)
        let meanY = points.map(\.y).reduce(0, +) / Double(points.count)
        let scale = points.map { abs($0.x - meanX) + abs($0.y - meanY) }.reduce(0, +) / Double(points.count)
        guard scale > 0 else { return nil }
        // A x² + B xy + C y² + D x + E y = 1 in centered, scaled coordinates.
        var matrix = [[Double]](repeating: [Double](repeating: 0, count: 6), count: 5)
        for point in points {
            let x = (point.x - meanX) / scale
            let y = (point.y - meanY) / scale
            let f = [x * x, x * y, y * y, x, y]
            for row in 0..<5 {
                for column in 0..<5 { matrix[row][column] += f[row] * f[column] }
                matrix[row][5] += f[row]
            }
        }
        guard let solution = solve(matrix) else { return nil }
        let (a, b, c, d, e) = (solution[0], solution[1], solution[2], solution[3], solution[4])
        guard a > 0, 4 * a * c - b * b > 0 else { return nil }
        let determinant = 4 * a * c - b * b
        let x0 = (b * e - 2 * c * d) / determinant
        let y0 = (b * d - 2 * a * e) / determinant
        let k = 1 - (a * x0 * x0 + b * x0 * y0 + c * y0 * y0 + d * x0 + e * y0)
        guard k > 0 else { return nil }
        let mean = (a + c) / 2
        let spread = (((a - c) / 2) * ((a - c) / 2) + (b / 2) * (b / 2)).squareRoot()
        let steep = mean + spread  // along the short axis
        let flat = mean - spread
        guard flat > 0 else { return nil }
        var angle = 0.5 * atan2(b, a - c)
        if cos(angle) < 0 { angle += Double.pi }
        return Oval(
            centerX: meanX + x0 * scale, centerY: meanY + y0 * scale,
            across: (cos(angle), sin(angle)),
            halfWidth: (k / steep).squareRoot() * scale, halfHeight: (k / flat).squareRoot() * scale)
    }
}

/// Gaussian elimination on an augmented n × (n + 1) matrix.
func solve(_ input: [[Double]]) -> [Double]? {
    var m = input
    let n = m.count
    for column in 0..<n {
        guard let pivot = (column..<n).max(by: { abs(m[$0][column]) < abs(m[$1][column]) }),
            abs(m[pivot][column]) > 1e-12
        else { return nil }
        m.swapAt(column, pivot)
        for row in 0..<n where row != column {
            let factor = m[row][column] / m[column][column]
            for k in column...n { m[row][k] -= factor * m[column][k] }
        }
    }
    return (0..<n).map { m[$0][n] / m[$0][$0] }
}

// MARK: Eyes

/// One eye: its visible pixels, average color, how much of what surrounds it is face, and the
/// whole oval, including any part under the hair.
struct Eye {
    var pixels: [Int]
    var color: RGB
    var minX: Int
    var maxX: Int
    var minY: Int
    var maxY: Int
    var faceShare: Double
    var oval: Oval

    var area: Int { pixels.count }
    var width: Int { maxX - minX + 1 }
    var height: Int { maxY - minY + 1 }
    var centerX: Double { Double(minX + maxX) / 2 }
    var centerY: Double { Double(minY + maxY) / 2 }
}

/// Set by `--dark-eyes`: the eyes are near-black instead of colored.
nonisolated(unsafe) var darkEyes = false

func isEyeColored(_ p: RGB) -> Bool { darkEyes ? p.luma <= 0.2 : p.chroma >= 40 && p.luma <= 0.78 }

/// Same eye as `seed`: close in color, hue and saturation, so a flood fill stops at the skin,
/// at outlines and at hair of a similar hue but different saturation.
func sameEye(_ p: RGB, as seed: RGB) -> Bool {
    if darkEyes { return p.luma <= 0.3 && p.distance(to: seed) <= 55 }
    return p.chroma >= 35 && p.distance(to: seed) <= 55 && hueDistance(p.hue, seed.hue) <= 20
        && abs(p.chroma - seed.chroma) <= 25
}

struct EyeFinder {
    let canvas: Canvas
    let skin: Skin

    /// How far an eye may reach from any of its pixels; drawn close up, dark eyes are taller.
    var reach: Int { Int(Double(canvas.width) * (darkEyes ? 0.2 : 0.14)) }

    /// The bounding box of the largest connected patch of skin.
    func faceBox() -> (minX: Int, maxX: Int, minY: Int, maxY: Int)? {
        let count = canvas.width * canvas.height
        var seen = [Bool](repeating: false, count: count)
        var best: (size: Int, box: (Int, Int, Int, Int))?
        for start in 0..<count
        where !seen[start] && canvas.alpha[start] > 0.98 && skin.isSkin(canvas.color[start]) {
            var queue = [start]
            seen[start] = true
            var head = 0
            var box = (Int.max, Int.min, Int.max, Int.min)
            while head < queue.count {
                let i = queue[head]
                head += 1
                let x = i % canvas.width
                let y = i / canvas.width
                box = (min(box.0, x), max(box.1, x), min(box.2, y), max(box.3, y))
                for n in canvas.neighbors(of: i, diagonal: false)
                where !seen[n] && canvas.alpha[n] > 0.98 && skin.isSkin(canvas.color[n]) {
                    seen[n] = true
                    queue.append(n)
                }
            }
            if queue.count > (best?.size ?? 0) { best = (queue.count, box) }
        }
        guard let best else { return nil }
        return (best.box.0, best.box.1, best.box.2, best.box.3)
    }

    /// An eye-colored pixel well inside a patch of its color, not on an anti-aliased edge.
    func isInterior(_ i: Int) -> Bool {
        let x = i % canvas.width
        let y = i / canvas.width
        guard x >= 2, y >= 2, x < canvas.width - 2, y < canvas.height - 2 else { return false }
        let p = canvas.color[i]
        for dy in -2...2 {
            for dx in -2...2 {
                let n = (y + dy) * canvas.width + x + dx
                if canvas.alpha[n] < 0.98 || canvas.color[n].distance(to: p) > 25 { return false }
            }
        }
        return true
    }

    /// Flood fill from `seed`, never further than `reach` from it; nil when it runs into the
    /// limit, which means it found hair rather than an eye. Every pixel it reaches is marked
    /// `tried` so it is not used as a seed again, but other floods may still pass through it.
    func blob(from seed: Int, tried: inout [Bool], visits: inout [Int], round: Int) -> [Int]? {
        let seedColor = canvas.color[seed]
        let sx = seed % canvas.width
        let sy = seed / canvas.width
        var queue = [seed]
        visits[seed] = round
        tried[seed] = true
        var head = 0
        var leaked = false
        while head < queue.count {
            let i = queue[head]
            head += 1
            for n in canvas.neighbors(of: i, diagonal: false)
            where visits[n] != round && canvas.alpha[n] > 0.98 && sameEye(canvas.color[n], as: seedColor) {
                visits[n] = round
                let x = n % canvas.width
                let y = n / canvas.width
                if abs(x - sx) >= reach || abs(y - sy) >= reach {
                    leaked = true
                    continue
                }
                tried[n] = true
                queue.append(n)
            }
        }
        return leaked ? nil : queue
    }

    func eye(from pixels: [Int]) -> Eye {
        var sum = RGB.black
        var box = (Int.max, Int.min, Int.max, Int.min)
        for i in pixels {
            sum = sum + canvas.color[i]
            let x = i % canvas.width
            let y = i / canvas.width
            box = (min(box.0, x), max(box.1, x), min(box.2, y), max(box.3, y))
        }
        // How much of the surroundings, 4–5 pixels out, is face rather than hair.
        let around = spread(from: pixels, in: canvas, limit: 5)
        var ring = 0
        var face = 0
        for i in around.reached where around.distance[i] >= 4 {
            ring += 1
            if canvas.alpha[i] > 0.98 && skin.isFace(canvas.color[i]) { face += 1 }
        }
        let oval = Oval(
            centerX: Double(box.0 + box.1 + 1) / 2, centerY: Double(box.2 + box.3 + 1) / 2,
            across: (1, 0), halfWidth: Double(box.1 - box.0 + 1) / 2,
            halfHeight: Double(box.3 - box.2 + 1) / 2)
        return Eye(
            pixels: pixels, color: sum * (1 / Double(max(pixels.count, 1))), minX: box.0, maxX: box.1,
            minY: box.2, maxY: box.3, faceShare: ring > 0 ? Double(face) / Double(ring) : 0, oval: oval)
    }

    /// Eye-sized, oval-ish blobs of one color, mostly surrounded by face.
    func candidates() -> [Eye] {
        guard let box = faceBox() else { return [] }
        let margin = canvas.width / 20
        let total = Double(canvas.width * canvas.height)
        var tried = [Bool](repeating: false, count: canvas.width * canvas.height)
        var visits = [Int](repeating: -1, count: canvas.width * canvas.height)
        var round = 0
        var found: [Eye] = []
        for y in max(0, box.minY - margin)...min(canvas.height - 1, box.maxY + margin) {
            for x in max(0, box.minX - margin)...min(canvas.width - 1, box.maxX + margin) {
                let i = y * canvas.width + x
                guard !tried[i], canvas.alpha[i] > 0.98, isEyeColored(canvas.color[i]), isInterior(i) else {
                    continue
                }
                round += 1
                guard let pixels = blob(from: i, tried: &tried, visits: &visits, round: round),
                    (0.0008...0.02).contains(Double(pixels.count) / total)
                else { continue }
                let eye = eye(from: pixels)
                let fill = Double(eye.area) / Double(eye.width * eye.height)
                let aspect = Double(eye.height) / Double(eye.width)
                if eye.faceShare >= 0.35, fill >= 0.5, (0.7...2.6).contains(aspect) { found.append(eye) }
            }
        }
        return found
    }

    /// The two eyes: the pair of candidates most alike in color and size, side by side.
    func eyes() -> (left: Eye, right: Eye)? {
        let found = candidates()
        var best: (score: Double, pair: (Eye, Eye))?
        for i in found.indices {
            for j in found.indices where j > i {
                let a = found[i]
                let b = found[j]
                let colorGap = a.color.distance(to: b.color)
                let sizeRatio = Double(max(a.area, b.area)) / Double(min(a.area, b.area))
                let apart = abs(a.centerX - b.centerX)
                guard colorGap < 45, sizeRatio <= 3, apart >= 1.2 * Double(max(a.width, b.width)),
                    apart <= 0.6 * Double(canvas.width),
                    abs(a.centerY - b.centerY) <= 1.2 * Double(max(a.height, b.height))
                else { continue }
                let score = a.faceShare + b.faceShare - colorGap / 90 - 0.3 * log(sizeRatio)
                if score > (best?.score ?? -.infinity) { best = (score, (a, b)) }
            }
        }
        guard let pair = best?.pair else { return nil }
        return pair.0.centerX < pair.1.centerX ? (pair.0, pair.1) : (pair.1, pair.0)
    }
}

// MARK: Layers

struct Layers {
    var base: Canvas
    var open: Canvas
    var mask: Canvas
    var eyes: [Eye]
    /// Largest difference (0...255) between base + eyes_open and the sticker.
    var error: Double
}

/// Width of the anti-aliased rim around an eye that still holds some of its color.
let rim = 3

/// What a pixel near an eye is.
enum Part {
    /// The visible eye.
    case eye
    /// The soft edge of the eye against the face: part eye, part skin.
    case edge
    /// Eye under something that stays put when the eye moves (hair, an outline).
    case covered
    /// Eye completely hidden under hair, shown only when the eye moves out from under it.
    case hidden
    case other
}

func makeLayers(_ canvas: Canvas, skin: Skin, eyes found: [Eye]) -> Layers {
    let count = canvas.width * canvas.height
    let width = canvas.width
    var eyes = found
    var owner = [Int](repeating: -1, count: count)
    for (index, eye) in eyes.enumerated() { for i in eye.pixels { owner[i] = index } }
    let core = eyes.flatMap(\.pixels)
    let near = spread(from: core, in: canvas, limit: max(rim + 1, width / 10))
    // Colors to continue an eye with come from well inside it, not from its soft edge.
    let depth = spread(from: near.reached.filter { near.distance[$0] == 1 }, in: canvas, limit: 4) {
        owner[$0] >= 0
    }
    let solid = spread(from: core.filter { depth.distance[$0] < 0 }, in: canvas, limit: width / 8)
    func eyeColor(at i: Int) -> RGB {
        canvas.color[solid.nearest[i] >= 0 ? solid.nearest[i] : near.nearest[i]]
    }
    let rimPixels = near.reached.filter { near.distance[$0] >= 1 && near.distance[$0] <= rim }
    func isFace(_ i: Int) -> Bool { canvas.alpha[i] > 0.98 && skin.isFace(canvas.color[i]) }

    // Whether face or something else (hair, an outline) lies just beyond each rim pixel, by
    // majority of the pixels a little further out, and the color of the nearest something else.
    var overFace = [Bool](repeating: false, count: count)
    var cover = canvas.color
    for i in rimPixels {
        let x = i % width
        let y = i / width
        var faces = 0
        var others = 0
        var nearest = Int.max
        for dy in -5...5 {
            for dx in -5...5 {
                let nx = x + dx
                let ny = y + dy
                guard nx >= 0, ny >= 0, nx < width, ny < canvas.height else { continue }
                let n = ny * width + nx
                guard ((rim + 1)...(rim + 3)).contains(near.distance[n]) else { continue }
                if isFace(n) {
                    faces += 1
                } else {
                    others += 1
                    if dx * dx + dy * dy < nearest {
                        nearest = dx * dx + dy * dy
                        cover[i] = canvas.color[n]
                    }
                }
            }
        }
        overFace[i] = faces >= others
    }

    // The whole oval of each eye, fitted to where it meets the face.
    var edges: [[(x: Double, y: Double)]] = []
    var seen: [Double] = []
    for index in eyes.indices {
        var edge: [(x: Double, y: Double)] = []
        var outline = 0
        for i in eyes[index].pixels {
            let around = canvas.neighbors(of: i, diagonal: false).filter { owner[$0] < 0 }
            guard !around.isEmpty else { continue }
            outline += 1
            if around.contains(where: { near.distance[$0] == 1 && overFace[$0] }) {
                edge.append((Double(i % width) + 0.5, Double(i / width) + 0.5))
            }
        }
        edges.append(edge)
        seen.append(Double(edge.count) / Double(max(outline, 1)))
        let box = eyes[index].oval
        if let oval = Oval.fit(edge), (0.4...1.6).contains(oval.halfWidth / box.halfWidth),
            (0.5...2.2).contains(oval.halfHeight / box.halfHeight),
            abs(oval.centerX - box.centerX) < box.halfWidth,
            abs(oval.centerY - box.centerY) < box.halfHeight * 1.2
        {
            eyes[index].oval = oval
        } else {
            print("warning: no oval fits the eye at (\(Int(box.centerX)), \(Int(box.centerY))); using its box")
        }
    }
    // An eye mostly under hair takes the shape of the other, better visible one.
    if eyes.count == 2, let clear = seen.indices.max(by: { seen[$0] < seen[$1] }), seen[clear] >= 0.8 {
        let hidden = 1 - clear
        let borrowed = eyes[clear].oval.moved(toFit: edges[hidden], from: eyes[hidden].oval)
        let inside = eyes[hidden].pixels.filter { borrowed.radius(of: $0, width: width) <= 1.04 }.count
        if seen[hidden] < 0.7, Double(inside) >= 0.95 * Double(eyes[hidden].area) {
            eyes[hidden].oval = borrowed
        }
    }

    var part = [Part](repeating: .other, count: count)
    for i in core { part[i] = .eye }
    for i in near.reached where part[i] == .other {
        if near.distance[i] <= rim {
            part[i] = overFace[i] ? .edge : .covered
        } else if !isFace(i), eyes[owner[near.nearest[i]]].oval.radius(of: i, width: width) <= 1 {
            part[i] = .hidden
        }
    }

    // Paint the eyes over with skin: a smooth fill from the face around them.
    var base = canvas
    let painted = near.reached.filter { part[$0] == .eye || part[$0] == .edge }
    var known = [Bool](repeating: false, count: count)
    for i in near.reached where part[i] == .other && isFace(i) { known[i] = true }
    for i in near.reached where part[i] == .other {
        for n in canvas.neighbors(of: i, diagonal: true) where isFace(n) && part[n] == .other {
            known[n] = true
        }
    }
    var filled = Set<Int>()
    var frontier = painted.filter { canvas.neighbors(of: $0, diagonal: true).contains { known[$0] } }
    while !frontier.isEmpty {
        var next: [(Int, RGB)] = []
        for i in frontier where !filled.contains(i) {
            let sources = canvas.neighbors(of: i, diagonal: true).filter { known[$0] || filled.contains($0) }
            next.append((i, sources.map { base.color[$0] }.reduce(.black, +) * (1 / Double(sources.count))))
        }
        for (i, color) in next {
            base.color[i] = color
            filled.insert(i)
        }
        var seen = Set<Int>()
        frontier = next.flatMap { canvas.neighbors(of: $0.0, diagonal: true) }.filter {
            (part[$0] == .eye || part[$0] == .edge) && !filled.contains($0) && seen.insert($0).inserted
        }.sorted()
    }
    for i in painted where !filled.contains(i) { base.color[i] = skin.color }
    // Relax towards a smooth surface so the fill shows no streaks.
    for _ in 0..<300 {
        for i in painted {
            let sources = canvas.neighbors(of: i, diagonal: false).filter {
                known[$0] || part[$0] == .eye || part[$0] == .edge
            }
            guard !sources.isEmpty else { continue }
            let average = sources.map { base.color[$0] }.reduce(.black, +) * (1 / Double(sources.count))
            base.color[i] = base.color[i] + (average - base.color[i]) * 1.6
        }
    }

    var open = Canvas(width: canvas.width, height: canvas.height)
    var mask = Canvas(width: canvas.width, height: canvas.height)
    for i in near.reached {
        let p = canvas.color[i]
        let eyeColor = eyeColor(at: i)
        switch part[i] {
        case .eye:
            open.color[i] = p
            open.alpha[i] = 1
            mask.alpha[i] = 1
        case .edge:
            let below = base.color[i].clamped
            base.color[i] = below
            // Light halos around some eyes are not a mix of eye and skin; they need more opacity.
            let share = max(p.share(of: eyeColor, over: below), p.leastOpacity(over: below))
            mask.alpha[i] = 1
            if share < 0.03 {
                base.color[i] = p
            } else {
                // Exactly what is needed on top of the painted skin to give the sticker back.
                open.color[i] = ((p - below * (1 - share)) * (1 / share)).clamped
                open.alpha[i] = share
            }
        case .covered:
            let behindColor = cover[i]
            let share = min(p.share(of: eyeColor, over: behindColor), p.mostShare(of: eyeColor))
            mask.alpha[i] = share
            open.color[i] = eyeColor
            open.alpha[i] = 1
            base.color[i] = share < 0.999 ? ((p - eyeColor * share) * (1 / (1 - share))).clamped : behindColor
        case .hidden:
            open.color[i] = eyeColor
            open.alpha[i] = eyes[owner[near.nearest[i]]].oval.coverage(of: i, width: width)
        case .other:
            break
        }
    }

    // The face around the eyes, where they may move; it ends at outlines and hair.
    let margin = width / 16
    let bounds = eyes.reduce((Int.max, Int.min, Int.max, Int.min)) {
        (
            max(0, min($0.0, $1.minX - margin)), min(width - 1, max($0.1, $1.maxX + margin)),
            max(0, min($0.2, $1.minY - margin)), min(canvas.height - 1, max($0.3, $1.maxY + margin))
        )
    }
    func inBounds(_ i: Int) -> Bool {
        let x = i % width
        let y = i / width
        return x >= bounds.0 && x <= bounds.1 && y >= bounds.2 && y <= bounds.3
    }
    var face = [Bool](repeating: false, count: count)
    var queue = near.reached.filter { part[$0] == .eye || part[$0] == .edge }
    for i in queue { face[i] = true }
    var head = 0
    while head < queue.count {
        let i = queue[head]
        head += 1
        for n in canvas.neighbors(of: i, diagonal: false)
        where !face[n] && part[n] == .other && isFace(n) && inBounds(n) {
            face[n] = true
            queue.append(n)
        }
    }
    // Specks in the face that are off-color (compression noise) count as face, so an eye
    // moving over them shows no holes.
    var checked = [Bool](repeating: false, count: count)
    for start in queue {
        for first in canvas.neighbors(of: start, diagonal: false) where !face[first] && !checked[first] {
            var speck = [first]
            checked[first] = true
            var index = 0
            var enclosed = true
            while index < speck.count, enclosed {
                let i = speck[index]
                index += 1
                for n in canvas.neighbors(of: i, diagonal: false) where !face[n] && !checked[n] {
                    guard part[n] == .other, inBounds(n), speck.count < 64 else {
                        enclosed = false
                        break
                    }
                    checked[n] = true
                    speck.append(n)
                }
            }
            if enclosed && part[first] == .other && inBounds(first) {
                for i in speck { face[i] = true }
                queue.append(contentsOf: speck)
            }
        }
    }
    for i in queue where part[i] == .other {
        // Soft edge: a face pixel next to hair is only partly face.
        let around = canvas.neighbors(of: i, diagonal: true)
        let inside = around.filter { face[$0] || part[$0] == .covered }.count
        mask.alpha[i] = Double(inside + 1) / Double(around.count + 1)
    }
    for i in 0..<count where mask.alpha[i] > 0 { mask.color[i] = RGB(255, 255, 255) }

    var error = 0.0
    for i in near.reached {
        let share = open.alpha[i] * mask.alpha[i]
        let shown = base.color[i] * (1 - share) + open.color[i] * share
        let gap = shown - canvas.color[i]
        error = max(error, abs(gap.r), abs(gap.g), abs(gap.b))
    }
    return Layers(base: base, open: open, mask: mask, eyes: eyes, error: error)
}

/// A closed eye per eye: a soft downward arc, thickest in the middle, a little below the
/// middle of the open eye.
func closedEyes(_ eyes: [Eye], width: Int, height: Int) -> CGImage? {
    guard let context = Canvas.context(nil, width: width, height: height) else { return nil }
    context.translateBy(x: 0, y: CGFloat(height))
    context.scaleBy(x: 1, y: -1)
    context.setLineCap(.round)
    context.setLineJoin(.round)
    for eye in eyes {
        let oval = eye.oval
        let shade = eye.color * 0.68
        let color = CGColor(
            srgbRed: shade.r / 255, green: shade.g / 255, blue: shade.b / 255, alpha: 1)
        let span = oval.halfWidth * 0.95
        let lid = oval.halfHeight * 0.28
        let sag = oval.halfWidth * 0.42
        let thickness = oval.halfWidth * 0.34
        let start = oval.point(across: -span, down: lid)
        let end = oval.point(across: span, down: lid)
        let outer = oval.point(across: 0, down: lid + 2 * (sag + thickness / 2))
        let inner = oval.point(across: 0, down: lid + 2 * (sag - thickness / 2))
        let path = CGMutablePath()
        path.move(to: start)
        path.addQuadCurve(to: end, control: outer)
        path.addQuadCurve(to: start, control: inner)
        path.closeSubpath()
        context.setFillColor(color)
        context.setStrokeColor(color)
        context.setLineWidth(thickness * 0.35)
        context.addPath(path)
        context.drawPath(using: .fillStroke)
    }
    return context.makeImage()
}

// MARK: Main

func split(_ url: URL) throws {
    let name = url.deletingPathExtension().lastPathComponent
    let canvas = try Canvas(contentsOf: url)
    guard let skin = Skin(canvas) else { throw Failure("\(name): no face found") }
    guard let pair = EyeFinder(canvas: canvas, skin: skin).eyes() else {
        throw Failure("\(name): could not find two eyes")
    }
    let layers = makeLayers(canvas, skin: skin, eyes: [pair.left, pair.right])
    guard layers.error <= 3 else {
        throw Failure("\(name): base + eyes_open differs from the sticker by \(Int(layers.error))")
    }
    let folder = url.deletingLastPathComponent().appendingPathComponent(name)
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    try writePNG(layers.base.image(), to: folder.appendingPathComponent("base.png"))
    try writePNG(layers.open.image(), to: folder.appendingPathComponent("eyes_open.png"))
    try writePNG(
        closedEyes(layers.eyes, width: canvas.width, height: canvas.height),
        to: folder.appendingPathComponent("eyes_closed.png"))
    try writePNG(layers.mask.image(), to: folder.appendingPathComponent("eyes_mask.png"))
    let described = layers.eyes.map { eye in
        String(
            format: "(%.0f, %.0f) %.0f×%.0f #%02X%02X%02X", eye.oval.centerX, eye.oval.centerY,
            eye.oval.halfWidth * 2, eye.oval.halfHeight * 2, Int(eye.color.r), Int(eye.color.g), Int(eye.color.b))
    }
    print("\(name): eyes \(described.joined(separator: ", ")), max error \(Int(layers.error))")
}

darkEyes = CommandLine.arguments.contains("--dark-eyes")
let paths = CommandLine.arguments.dropFirst().filter { $0 != "--dark-eyes" }
if paths.isEmpty {
    print("usage: swift scripts/sticker-layers.swift Design/stickers/<name>.png ...")
    exit(2)
}
var failed = false
for path in paths {
    do {
        try split(URL(fileURLWithPath: path))
    } catch {
        print("error: \(error)")
        failed = true
    }
}
exit(failed ? 1 : 0)
