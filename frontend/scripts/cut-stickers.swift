import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

// Cuts a character sheet with a transparent background into stickers (docs/mascot.md): a square
// 1024 PNG per character, centered with 4% margins left, right and top, its bottom on the
// bottom edge. Vision only says which character a pixel belongs to; transparency comes from the
// sheet. Names are given in Vision's order of the characters; each one's box in the sheet is
// printed to check it.
//
//   swift frontend/scripts/cut-stickers.swift <sheet.png|webp> <out dir> <name> <name> ...
let args = CommandLine.arguments
let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: args[1]) as CFURL, nil)!
let image = CGImageSourceCreateImageAtIndex(source, 0, nil)!
let outDir = URL(fileURLWithPath: args[2])
let names = Array(args.dropFirst(3))
let width = image.width, height = image.height
let space = CGColorSpace(name: CGColorSpace.sRGB)!
let info = CGImageAlphaInfo.premultipliedLast.rawValue

var pixels = [UInt8](repeating: 0, count: width * height * 4)
CGContext(
    data: &pixels, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
    space: space, bitmapInfo: info)!.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))

let request = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(cgImage: image)
try handler.perform([request])
let result = request.results!.first!
let instances = result.allInstances.sorted()
var masks: [[Float]] = []
for index in instances {
    let buffer = try result.generateScaledMaskForImage(forInstances: [index], from: handler)
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    let row = CVPixelBufferGetBytesPerRow(buffer) / 4
    let base = CVPixelBufferGetBaseAddress(buffer)!.assumingMemoryBound(to: Float32.self)
    var mask = [Float](repeating: 0, count: width * height)
    for y in 0..<height { for x in 0..<width { mask[y * width + x] = base[y * row + x] } }
    masks.append(mask)
}
// Pixels a mask is sure about belong to that character; the rest (thin parts like a halo, soft
// edges) to the character whose sure pixels they touch first, spreading outward. Specks that
// touch no character are dropped.
var owner = [Int8](repeating: -1, count: width * height)
var queue: [Int] = []
for p in 0..<(width * height) where pixels[p * 4 + 3] > 0 {
    var best: Float = 0.3, k = -1
    for m in masks.indices where masks[m][p] > best { best = masks[m][p]; k = m }
    if k >= 0 { owner[p] = Int8(k); queue.append(p) }
}
var head = 0
while head < queue.count {
    let p = queue[head]; head += 1
    let x = p % width, y = p / width
    for (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
        let nx = x + dx, ny = y + dy
        guard nx >= 0, ny >= 0, nx < width, ny < height else { continue }
        let q = ny * width + nx
        if owner[q] < 0 && pixels[q * 4 + 3] > 0 { owner[q] = owner[p]; queue.append(q) }
    }
}

var outputs = [[UInt8]](repeating: [UInt8](repeating: 0, count: width * height * 4), count: masks.count)
var boxes = [(Int, Int, Int, Int)](repeating: (width, height, 0, 0), count: masks.count)
for y in 0..<height {
    for x in 0..<width {
        let i = (y * width + x) * 4
        let alpha = Int(pixels[i + 3])
        guard alpha > 0, owner[y * width + x] >= 0 else { continue }
        let owner = Int(owner[y * width + x])
        // Lossy alpha leaves the inside at ~252: make it solid.
        let solid = alpha >= 246 ? 255 : alpha
        for c in 0..<3 { outputs[owner][i + c] = UInt8(min(255, Int(pixels[i + c]) * solid / alpha)) }
        outputs[owner][i + 3] = UInt8(solid)
        if alpha > 8 {
            let b = boxes[owner]
            boxes[owner] = (min(b.0, x), min(b.1, y), max(b.2, x), max(b.3, y))
        }
    }
}

for (k, name) in names.enumerated() where k < outputs.count {
    let (minX, minY, maxX, maxY) = boxes[k]
    let box = CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
    let character = outputs[k].withUnsafeMutableBytes { bytes in
        CGContext(
            data: bytes.baseAddress, width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: width * 4, space: space, bitmapInfo: info)!.makeImage()!
    }.cropping(to: box)!
    let side = 1024.0
    let scale = min(side * 0.92 / box.width, side * 0.96 / box.height)
    let drawn = CGSize(width: box.width * scale, height: box.height * scale)
    let canvas = CGContext(
        data: nil, width: Int(side), height: Int(side), bitsPerComponent: 8, bytesPerRow: 0,
        space: space, bitmapInfo: info)!
    canvas.interpolationQuality = .high
    canvas.draw(character, in: CGRect(x: (side - drawn.width) / 2, y: 0, width: drawn.width, height: drawn.height))
    let url = outDir.appendingPathComponent("\(name).png")
    let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(destination, canvas.makeImage()!, nil)
    CGImageDestinationFinalize(destination)
    print(name, "box", box, "scale", String(format: "%.3f", scale))
}
