import AppKit
import Foundation
import PDFKit
import Vision

// No screen, Accessibility or network APIs. Only the path supplied on stdin is read.
enum ExtractionError: Error { case invalidInput, unreadable, encrypted, renderFailed }

func recognize(_ handler: VNImageRequestHandler) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    try handler.perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

func extractPDF(_ url: URL) throws -> [[String: Any]] {
    guard let document = PDFDocument(url: url) else { throw ExtractionError.unreadable }
    guard !document.isLocked else { throw ExtractionError.encrypted }
    var pages: [[String: Any]] = []
    for index in 0..<document.pageCount {
        let result: [String: Any] = try autoreleasepool {
            guard let page = document.page(at: index) else { throw ExtractionError.unreadable }
            let native = page.string ?? ""
            if native.trimmingCharacters(in: .whitespacesAndNewlines).count >= 20 {
                return ["page": index + 1, "text": native, "ocr": false]
            }
            let box = page.bounds(for: .mediaBox)
            guard box.width > 0, box.height > 0 else { throw ExtractionError.renderFailed }
            let scale = min(2.5, 3000 / max(box.width, box.height))
            let image = page.thumbnail(of: NSSize(width: box.width * scale, height: box.height * scale), for: .mediaBox)
            guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
                throw ExtractionError.renderFailed
            }
            return ["page": index + 1, "text": try recognize(VNImageRequestHandler(cgImage: cgImage)), "ocr": true]
        }
        pages.append(result)
    }
    return pages
}

func main() throws {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard CommandLine.arguments.count == 2,
          let request = try JSONSerialization.jsonObject(with: input) as? [String: String],
          let path = request["path"], path.hasPrefix("/") else { throw ExtractionError.invalidInput }
    let url = URL(fileURLWithPath: path)
    let attributes = try FileManager.default.attributesOfItem(atPath: path)
    // PDFs are read page by page, so a big notebook is fine; an image is decoded whole.
    let limit = CommandLine.arguments[1] == "pdf" ? 500 : 50
    guard let size = attributes[.size] as? NSNumber, size.intValue <= limit * 1024 * 1024 else {
        throw ExtractionError.invalidInput
    }
    let output: [String: Any]
    switch CommandLine.arguments[1] {
    case "pdf": output = ["pages": try extractPDF(url)]
    case "image": output = ["text": try recognize(VNImageRequestHandler(url: url))]
    default: throw ExtractionError.invalidInput
    }
    let data = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

do { try main() }
catch {
    // Fixed errors only: never print the filename, document text or parser diagnostics.
    FileHandle.standardOutput.write(Data("{\"error\":\"document_unreadable\"}\n".utf8))
    exit(1)
}
