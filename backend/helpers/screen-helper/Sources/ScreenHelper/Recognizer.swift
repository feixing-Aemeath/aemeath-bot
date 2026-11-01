import CoreGraphics
import Foundation
import Vision

/// One recognized line: text and its box as fractions of the image, origin at the top left
/// (Vision's boxes start at the bottom left; y = 1 - vision_y - vision_h).
struct Block {
    let text: String
    let x: Double, y: Double, w: Double, h: Double
    let confidence: Double

    var json: [String: Any] {
        ["text": text, "x": x, "y": y, "w": w, "h": h, "confidence": confidence]
    }

    var midY: Double { y + h / 2 }
    var right: Double { x + w }
}

enum Recognizer {
    static func blocks(in url: URL) throws -> [Block] {
        try blocks(VNImageRequestHandler(url: url))
    }

    static func blocks(in image: CGImage) throws -> [Block] {
        try blocks(VNImageRequestHandler(cgImage: image))
    }

    private static func blocks(_ handler: VNImageRequestHandler) throws -> [Block] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        try handler.perform([request])
        return (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            func clamp(_ value: CGFloat) -> Double { min(max(Double(value), 0), 1) }
            let x = clamp(box.minX), y = clamp(1 - box.maxY)
            let w = min(clamp(box.width), 1 - x), h = min(clamp(box.height), 1 - y)
            guard w > 0, h > 0, !candidate.string.isEmpty else { return nil }
            return Block(text: candidate.string, x: x, y: y, w: w, h: h, confidence: Double(candidate.confidence))
        }
    }
}

extension String {
    /// For comparing a chat name with what OCR read: width, case, spaces and punctuation do
    /// not count.
    var matchKey: String {
        precomposedStringWithCompatibilityMapping.lowercased().unicodeScalars
            .filter { !CharacterSet.whitespacesAndNewlines.contains($0) && !CharacterSet.punctuationCharacters.contains($0) && !CharacterSet.symbols.contains($0) }
            .map(String.init).joined()
    }
}
