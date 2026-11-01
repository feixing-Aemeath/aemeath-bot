import CoreGraphics
import Foundation
import ScreenCaptureKit

/// A browser window showing a page the user keeps open, such as the school timetable, found
/// by its title (a browser window is titled after its active tab). Only that window is
/// captured, also where other windows cover it; the browser is not activated, clicked or
/// scrolled, and nothing is typed into it. A minimized window has no picture to capture.
enum PageShot {
    static func take(of app: App, title: String) async throws -> CGImage {
        guard app.running != nil else { throw HelperError.appNotRunning }
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        } catch {
            throw HelperError.screenRecordingDenied
        }
        let windows = content.windows.filter {
            $0.owningApplication?.bundleIdentifier == app.bundleID && $0.windowLayer == 0
                && ($0.title ?? "").contains(title)
        }
        guard let window = windows.max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height })
        else { throw HelperError.pageNotOpen }
        return try await WindowShot.capture(window)
    }
}
