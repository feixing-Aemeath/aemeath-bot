// Makes the App icon from 助理's sticker as an Icon Composer icon (App/AppIcon.icon), the
// layered kind iOS 26, watchOS 26 and macOS 26 draw with Liquid Glass: a white background the
// system turns into glass in the clear and tinted looks, and her head as its own layer on top,
// a little below the middle. Xcode makes the flat images older systems need from it.
//
//   swift scripts/make-app-icon.swift Design/stickers/aemeath.png App/AppIcon.icon
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let arguments = CommandLine.arguments
guard arguments.count == 3,
    let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: arguments[1]) as CFURL, nil),
    let sticker = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    FileHandle.standardError.write(Data("usage: make-app-icon.swift <sticker.png> <AppIcon.icon>\n".utf8))
    exit(1)
}
let icon = URL(fileURLWithPath: arguments[2], isDirectory: true)

/// The character inside the sticker's square canvas, in unit coordinates from the top left.
func characterBounds(_ image: CGImage) -> CGRect {
    let side = 256
    var pixels = [UInt8](repeating: 0, count: side * side * 4)
    let context = CGContext(
        data: &pixels, width: side, height: side, bitsPerComponent: 8, bytesPerRow: side * 4,
        space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    context.draw(image, in: CGRect(x: 0, y: 0, width: side, height: side))
    var minX = side, minY = side, maxX = 0, maxY = 0
    for y in 0..<side {
        for x in 0..<side where pixels[(y * side + x) * 4 + 3] > 24 {
            minX = min(minX, x)
            maxX = max(maxX, x)
            minY = min(minY, y)
            maxY = max(maxY, y)
        }
    }
    let unit = 1 / CGFloat(side)
    return CGRect(
        x: CGFloat(minX) * unit, y: CGFloat(minY) * unit, width: CGFloat(maxX - minX + 1) * unit,
        height: CGFloat(maxY - minY + 1) * unit)
}

// The head layer: transparent 1024 canvas (the icon's own size), the head 80 % wide with its
// middle at 56 % of the height.
let side: CGFloat = 1024
let character = characterBounds(sticker)
let drawn = side * 0.80 / character.width
let context = CGContext(
    data: nil, width: Int(side), height: Int(side), bitsPerComponent: 8, bytesPerRow: 0,
    space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
// Top-left origin, like the measurements; CGContext draws images bottom up.
context.translateBy(x: 0, y: side)
context.scaleBy(x: 1, y: -1)
context.translateBy(x: side * 0.5 - character.midX * drawn, y: side * 0.56 - character.midY * drawn + drawn)
context.scaleBy(x: 1, y: -1)
context.interpolationQuality = .high
context.draw(sticker, in: CGRect(x: 0, y: 0, width: drawn, height: drawn))

let assets = icon.appendingPathComponent("Assets", isDirectory: true)
try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
let destination = CGImageDestinationCreateWithURL(
    assets.appendingPathComponent("aemeath.png") as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(destination, context.makeImage()!, nil)
guard CGImageDestinationFinalize(destination) else { fatalError("could not write the head layer") }

// White behind her (glass in the clear and tinted looks); the group gives the head its depth.
let document = """
    {
      "fill" : {
        "solid" : "srgb:1.00000,1.00000,1.00000,1.00000"
      },
      "groups" : [
        {
          "layers" : [
            {
              "image-name" : "aemeath.png",
              "name" : "aemeath"
            }
          ],
          "shadow" : {
            "kind" : "neutral",
            "opacity" : 0.5
          },
          "translucency" : {
            "enabled" : true,
            "value" : 0.4
          }
        }
      ],
      "supported-platforms" : {
        "circles" : [
          "watchOS"
        ],
        "squares" : "shared"
      }
    }

    """
try document.write(to: icon.appendingPathComponent("icon.json"), atomically: true, encoding: .utf8)
print("wrote \(icon.path)")
