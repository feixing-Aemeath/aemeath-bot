import Foundation

/// A provider contributes one representation: prefer a URL over that provider's text fallback.
public struct ShareFragment: Equatable, Sendable {
    public var text: String?
    public var url: URL?
    public var title: String?
    public init(text: String? = nil, url: URL? = nil, title: String? = nil) {
        self.text = text
        self.url = url
        self.title = title
    }
}

public struct ShareContent: Equatable, Sendable {
    public var text: String
    public var truncated: Bool
    public init(fragments: [ShareFragment]) {
        var parts: [String] = []
        for fragment in fragments {
            let title = fragment.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if let url = fragment.url, ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                if !title.isEmpty, title != url.absoluteString { parts.append(title) }
                parts.append(url.absoluteString)
            } else if let text = fragment.text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
                if !title.isEmpty, title != text { parts.append(title) }
                parts.append(text)
            }
        }
        var seen = Set<String>()
        let joined = parts.filter { seen.insert($0).inserted }.joined(separator: "\n\n")
        truncated = joined.unicodeScalars.count > Self.textLimit
        text = Self.limit(joined, to: Self.textLimit)
    }
    public static let textLimit = 8000
    public static let noteLimit = 500
    /// Python's contract counts Unicode code points, rather than Swift extended graphemes.
    public static func limit(_ value: String, to count: Int) -> String {
        String(String.UnicodeScalarView(value.unicodeScalars.prefix(max(0, count))))
    }
    public static func item(text: String, note: String, source: MissedSource?, missed: Bool) -> InboxItem {
        InboxItem(
            text: limit(text.trimmingCharacters(in: .whitespacesAndNewlines), to: textLimit),
            note: note.isEmpty ? nil : limit(note, to: noteLimit),
            sourceApp: source.map { limit(Names.plugin($0.plugin), to: 40) },
            chatName: source.map { limit($0.chatName, to: 80) }, missed: missed)
    }
}

public enum ShareDeliveryStatus: Equatable, Sendable {
    case sent, queued, mock, unavailable, invalid
    public var text: String { text(assistantName: "助理") }
    public func text(assistantName: String) -> String {
        switch self {
        case .sent: return "已转给\(assistantName)"
        case .queued: return "已存下，连上 Mac 后会发出去"
        case .mock: return "App 现在用的是模拟数据"
        case .unavailable: return "没能存下，请先打开 App 检查连接设置，再试一次。"
        case .invalid: return "请分享一段文字或网页链接。"
        }
    }
    public var shouldClose: Bool { self == .sent || self == .queued }
    public static func pendingText(_ count: Int) -> String? {
        count > 0 ? "\(count) 条转发等着发，连上 Mac 后会继续。" : nil
    }
}
