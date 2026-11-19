import CoreGraphics
import XCTest
@testable import ScreenHelper

final class WindowShotTests: XCTestCase {
    func image(_ data: [UInt8], width: Int = 128, height: Int = 128) -> CGImage {
        CGImage(
            width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 8,
            bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
            provider: CGDataProvider(data: Data(data) as CFData)!, decode: nil,
            shouldInterpolate: false, intent: .defaultIntent)!
    }

    func testHistoryTopFingerprintSeesContentBetweenSamplePoints() {
        let blank = [UInt8](repeating: 240, count: 128 * 128)
        var changed = blank
        // The old summary sampled every fourth pixel; narrow content could fall between them.
        for y in 20..<40 { changed[y * 128 + 1] = 0 }
        XCTAssertNotEqual(Pixels(image(blank)).fingerprint, Pixels(image(changed)).fingerprint)
    }

    func testResizingTheWindowDoesNotClaimAnUnchangedPicture() {
        let blank = [UInt8](repeating: 240, count: 128 * 128)
        XCTAssertNotEqual(
            Pixels(image(blank)).fingerprint,
            Pixels(image(blank, width: 256, height: 64)).fingerprint)
    }
}

extension WindowShotTests {
    func testInputRequiresTopmostCapturedWindowAtItsOriginalFrame() {
        let frame = CGRect(x: 20, y: 30, width: 800, height: 600)
        func window(_ id: UInt32, _ bounds: CGRect) -> [String: Any] {
            [kCGWindowNumber as String: id, kCGWindowBounds as String: bounds.dictionaryRepresentation,
             kCGWindowAlpha as String: 1.0]
        }
        let own = window(42, frame), other = window(43, frame)
        let point = CGPoint(x: 100, y: 100)
        XCTAssertTrue(WindowShot.inputTarget(windows: [own, other], at: point, windowID: 42, frame: frame))
        XCTAssertFalse(WindowShot.inputTarget(windows: [other, own], at: point, windowID: 42, frame: frame))
        XCTAssertFalse(WindowShot.inputTarget(windows: [own], at: point, windowID: 42, frame: frame.offsetBy(dx: 1, dy: 0)))
        XCTAssertFalse(WindowShot.inputTarget(windows: [own], at: .zero, windowID: 42, frame: frame))
    }

    func testTheDocksOverlayIsNotCover() {
        let frame = CGRect(x: 20, y: 30, width: 800, height: 600)
        let screen = CGRect(x: 0, y: 0, width: 3200, height: 1800)
        func window(_ id: UInt32, _ bounds: CGRect, pid: Int32, layer: Int) -> [String: Any] {
            [kCGWindowNumber as String: id, kCGWindowBounds as String: bounds.dictionaryRepresentation,
             kCGWindowAlpha as String: 1.0, kCGWindowOwnerPID as String: NSNumber(value: pid),
             kCGWindowLayer as String: layer]
        }
        let own = window(42, frame, pid: 500, layer: 0)
        let dock = window(7, screen, pid: 300, layer: 20)
        let point = CGPoint(x: 100, y: 100)
        // As the window list really is: the Dock's full-screen overlay in front of everything.
        XCTAssertFalse(WindowShot.inputTarget(windows: [dock, own], at: point, windowID: 42, frame: frame))
        XCTAssertTrue(
            WindowShot.inputTarget(windows: [dock, own], at: point, windowID: 42, frame: frame, passThrough: [300]))
        // Only above the normal layer: a normal window of that process covers like any other.
        let finder = window(8, screen, pid: 300, layer: 0)
        XCTAssertFalse(
            WindowShot.inputTarget(windows: [finder, own], at: point, windowID: 42, frame: frame, passThrough: [300]))
        // Another process's overlay (a banner) still covers.
        let banner = window(9, CGRect(x: 0, y: 0, width: 400, height: 200), pid: 301, layer: 23)
        XCTAssertFalse(
            WindowShot.inputTarget(windows: [banner, own], at: point, windowID: 42, frame: frame, passThrough: [300]))
        // And another app's window in front, under the overlay, is still found.
        let other = window(43, frame, pid: 501, layer: 0)
        XCTAssertFalse(
            WindowShot.inputTarget(windows: [dock, other, own], at: point, windowID: 42, frame: frame, passThrough: [300]))
    }
}
