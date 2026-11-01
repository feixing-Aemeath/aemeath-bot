import Darwin
import Foundation
import Photos

// This tool contains no PhotoKit change requests. PhotoKit calls its read-capable level
// `readWrite`; there is no readOnly authorization level. NSPhotoLibraryAddUsageDescription
// is deliberately absent. Limited permission only exposes the user's selected assets.
enum PhotoError: Error { case invalidInput, denied, missing, exportFailed, timeout }

@main struct PhotoExport {
    static func permission() -> String {
        switch PHPhotoLibrary.authorizationStatus(for: .readWrite) {
        case .authorized: return "authorized"
        case .limited: return "limited"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not_determined"
        @unknown default: return "unavailable"
        }
    }

    // Keep the permission on this signed helper, even when launched by Python/launchd.
    static func runDisclaimed() {
        let marker = "AEMEATH_PHOTO_DISCLAIMED"
        guard ProcessInfo.processInfo.environment[marker] == nil,
              let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim"),
              let path = Bundle.main.executablePath else { return }
        typealias Disclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32
        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        guard unsafeBitCast(symbol, to: Disclaim.self)(&attributes, 1) == 0 else { return }
        let arguments: [UnsafeMutablePointer<CChar>?] = CommandLine.arguments.map { strdup($0) } + [nil]
        let pairs = ProcessInfo.processInfo.environment.map { "\($0)=\($1)" } + ["\(marker)=1"]
        let environment: [UnsafeMutablePointer<CChar>?] = pairs.map { strdup($0) } + [nil]
        defer { (arguments + environment).forEach { free($0) } }
        var child: pid_t = 0
        guard posix_spawn(&child, path, nil, &attributes, arguments, environment) == 0 else { return }
        var status: Int32 = 0
        while waitpid(child, &status, 0) == -1 && errno == EINTR {}
        exit((status & 0x7f) == 0 ? (status >> 8) & 0xff : 1)
    }

    static func date(_ value: Any?) throws -> Date {
        guard let value = value as? String else { throw PhotoError.invalidInput }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let result = formatter.date(from: value) { return result }
        formatter.formatOptions = [.withInternetDateTime]
        guard let result = formatter.date(from: value) else { throw PhotoError.invalidInput }
        return result
    }

    static func list(_ input: [String: Any]) throws -> [[String: Any]] {
        let from = try date(input["from"])
        let to = try date(input["to"])
        guard from < to else { throw PhotoError.invalidInput }
        let options = PHFetchOptions()
        options.predicate = NSPredicate(format: "mediaType == %d AND creationDate >= %@ AND creationDate < %@", PHAssetMediaType.image.rawValue, from as NSDate, to as NSDate)
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]
        options.fetchLimit = 2000
        let assets = PHAsset.fetchAssets(with: options)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var output: [[String: Any]] = []
        assets.enumerateObjects { asset, _, _ in
            guard let created = asset.creationDate else { return }
            output.append([
                "id": asset.localIdentifier, "created_at": formatter.string(from: created),
                "is_screenshot": asset.mediaSubtypes.contains(.photoScreenshot),
                "width": asset.pixelWidth, "height": asset.pixelHeight
            ])
        }
        return output
    }

    static func export(_ input: [String: Any]) async throws -> [String: Any] {
        guard let id = input["id"] as? String, let dest = input["dest"] as? String,
              dest.hasPrefix("/") else { throw PhotoError.invalidInput }
        let assets = PHAsset.fetchAssets(withLocalIdentifiers: [id], options: nil)
        guard let asset = assets.firstObject, asset.mediaType == .image else { throw PhotoError.missing }
        let resources = PHAssetResource.assetResources(for: asset)
        guard let resource = resources.first(where: { $0.type == .photo }) else { throw PhotoError.missing }
        var target = URL(fileURLWithPath: dest)
        // A suffix-less destination is a stem: retain the original HEIC/JPEG/PNG extension.
        if target.pathExtension.isEmpty {
            let suffix = URL(fileURLWithPath: resource.originalFilename).pathExtension.lowercased()
            guard !suffix.isEmpty else { throw PhotoError.exportFailed }
            target.appendPathExtension(suffix)
        }
        guard !FileManager.default.fileExists(atPath: target.path) else { throw PhotoError.invalidInput }
        let options = PHAssetResourceRequestOptions()
        options.isNetworkAccessAllowed = true
        // Stream original bytes to disk. requestData permits cancellation on timeout,
        // unlike writeData's completion-only API. No whole-image Data accumulation.
        guard FileManager.default.createFile(atPath: target.path, contents: nil, attributes: [.posixPermissions: 0o600]) else { throw PhotoError.exportFailed }
        let handle = try FileHandle(forWritingTo: target)
        let completed = DispatchSemaphore(value: 0)
        let state = ExportState(handle)
        let manager = PHAssetResourceManager.default()
        let request = manager.requestData(for: resource, options: options, dataReceivedHandler: { data in
            state.write(data)
        }, completionHandler: { error in
            state.finish(error)
            completed.signal()
        })
        // Run the wait off the main thread so PhotoKit can deliver its callbacks.
        let success = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            DispatchQueue.global().async {
                continuation.resume(returning: completed.wait(timeout: .now() + 120) == .success)
            }
        }
        if !success { manager.cancelDataRequest(request) }
        let failed = state.close()
        if !success || failed {
            try? FileManager.default.removeItem(at: target)
            throw success ? PhotoError.exportFailed : PhotoError.timeout
        }
        let attributes = try FileManager.default.attributesOfItem(atPath: target.path)
        return ["path": target.path, "uti": resource.uniformTypeIdentifier, "bytes": attributes[.size] as? NSNumber ?? 0]
    }

    static func main() async {
        runDisclaimed()
        do {
            guard CommandLine.arguments.count == 2 else { throw PhotoError.invalidInput }
            let command = CommandLine.arguments[1]
            let output: Any
            if command == "status" {
                output = ["permission": permission()]
            } else if command == "authorize" {
                _ = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
                output = ["permission": permission()]
            } else {
                guard ["authorized", "limited"].contains(permission()) else { throw PhotoError.denied }
                let raw = FileHandle.standardInput.readDataToEndOfFile()
                guard let input = try JSONSerialization.jsonObject(with: raw) as? [String: Any] else { throw PhotoError.invalidInput }
                switch command {
                case "list": output = try list(input)
                case "export": output = try await export(input)
                default: throw PhotoError.invalidInput
                }
            }
            let data = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
        } catch {
            FileHandle.standardOutput.write(Data("{\"error\":\"photo_export_failed\"}\n".utf8))
            exit(1)
        }
    }
}

// Callbacks and timeout cancellation can race. Serialize file access and never write after
// closing a timed-out export. Errors stay local; paths and PhotoKit diagnostics are not logged.
final class ExportState: @unchecked Sendable {
    private let lock = NSLock()
    private var handle: FileHandle?
    private var failed = false
    init(_ handle: FileHandle) { self.handle = handle }
    func write(_ data: Data) {
        lock.lock(); defer { lock.unlock() }
        guard let handle else { return }
        do { try handle.write(contentsOf: data) } catch { failed = true }
    }
    func finish(_ error: Error?) {
        lock.lock(); defer { lock.unlock() }
        if error != nil { failed = true }
    }
    func close() -> Bool {
        lock.lock(); defer { lock.unlock() }
        do { try handle?.close() } catch { failed = true }
        handle = nil
        return failed
    }
}
