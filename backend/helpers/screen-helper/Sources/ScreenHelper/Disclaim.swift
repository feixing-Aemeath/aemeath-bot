import Darwin
import Foundation

/// Privacy permissions (Screen Recording, Accessibility) belong to the "responsible" process.
/// Started by the backend's Python, that would be Python: every script it runs would need,
/// and get, the right to read the screen. Start a copy of ourselves that disclaims the
/// parent instead, so the grant in System Settings is for this helper alone, and pass its
/// exit code on. stdin and stdout are inherited. (apple-sync does the same.)
enum Disclaim {
    static func runAsResponsibleProcess() {
        let marker = "ASSISTANT_DISCLAIMED"
        guard ProcessInfo.processInfo.environment[marker] == nil,
              let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim"),
              let path = Bundle.main.executablePath
        else { return }
        typealias SetDisclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32
        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        guard unsafeBitCast(symbol, to: SetDisclaim.self)(&attributes, 1) == 0 else { return }
        let arguments: [UnsafeMutablePointer<CChar>?] = CommandLine.arguments.map { strdup($0) } + [nil]
        let pairs = ProcessInfo.processInfo.environment.map { "\($0)=\($1)" } + ["\(marker)=1"]
        let environment: [UnsafeMutablePointer<CChar>?] = pairs.map { strdup($0) } + [nil]
        defer { (arguments + environment).forEach { free($0) } }
        var child: pid_t = 0
        guard posix_spawn(&child, path, nil, &attributes, arguments, environment) == 0 else { return }
        var status: Int32 = 0
        while waitpid(child, &status, 0) == -1 && errno == EINTR {}
        // WIFEXITED / WEXITSTATUS are macros Swift does not import.
        exit((status & 0x7f) == 0 ? (status >> 8) & 0xff : 1)
    }
}
