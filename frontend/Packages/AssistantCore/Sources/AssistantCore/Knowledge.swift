import Foundation

/// The personal knowledge base on the Mac (docs/knowledge.md): topic folders under `知识库/`
/// the user fills, each with its own list of who may read it (the Bots and the Agents the user
/// runs), notes the nightly job writes into a topic's `整理/` folder, and the vault's other
/// folders (Bots, 每日, …), which every Bot reads. The App sees everything; it is the user's.

/// Someone who can be given a folder: a Bot character (with its sticker) or an Agent.
public struct KnowledgeReader: Codable, Hashable, Identifiable, Sendable {
    /// The character's name ("助理") or the Agent's ("Claude Code").
    public var id: String
    /// "bot" or "agent".
    public var kind: String
    public var sticker: String?

    public init(id: String, kind: String, sticker: String? = nil) {
        self.id = id
        self.kind = kind
        self.sticker = sticker
    }

    public var isBot: Bool { kind == "bot" }
}

/// A folder with one reading list: a topic in `知识库/`, or one of the vault's own folders.
public struct KnowledgeArea: Codable, Hashable, Identifiable, Sendable {
    public var path: String
    public var name: String
    /// "topic" or "system".
    public var kind: String
    public var readers: [String]
    /// Readers that cannot be taken away (every Bot, on the vault's own folders).
    public var locked: [String]
    public var notes: Int
    public var sources: Int
    public var files: Int
    public var pending: Int
    public var failed: Int
    public var updatedAt: Date?

    public var id: String { path }
    public var isTopic: Bool { kind == "topic" }

    public init(
        path: String, name: String, kind: String = "topic", readers: [String], locked: [String] = [],
        notes: Int = 0, sources: Int = 0, files: Int? = nil, pending: Int = 0, failed: Int = 0,
        updatedAt: Date? = nil
    ) {
        self.path = path
        self.name = name
        self.kind = kind
        self.readers = readers
        self.locked = locked
        self.notes = notes
        self.sources = sources
        self.files = files ?? notes + sources
        self.pending = pending
        self.failed = failed
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case path, name, kind, readers, locked, notes, sources, files, pending, failed
        case updatedAt = "updated_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        name = try c.decode(String.self, forKey: .name)
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "topic"
        readers = try c.decodeIfPresent([String].self, forKey: .readers) ?? []
        locked = try c.decodeIfPresent([String].self, forKey: .locked) ?? []
        notes = try c.decodeIfPresent(Int.self, forKey: .notes) ?? 0
        sources = try c.decodeIfPresent(Int.self, forKey: .sources) ?? 0
        files = try c.decodeIfPresent(Int.self, forKey: .files) ?? notes + sources
        pending = try c.decodeIfPresent(Int.self, forKey: .pending) ?? 0
        failed = try c.decodeIfPresent(Int.self, forKey: .failed) ?? 0
        updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt)
    }

    /// "12 篇整理 · 5 份资料", or a vault folder's "13 个文件".
    public var summary: String {
        guard isTopic else { return files == 0 ? "空" : "\(files) 个文件" }
        if notes == 0 && sources == 0 { return "空的，放点资料进来" }
        return [notes > 0 ? "\(notes) 篇整理" : nil, sources > 0 ? "\(sources) 份资料" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }
}

public struct KnowledgeOrganize: Codable, Hashable, Sendable {
    public var lastRunAt: Date?
    /// The last run's status ("succeeded", "failed", "running"), nil before the first.
    public var status: String?
    public var nextRunAt: Date?

    public init(lastRunAt: Date? = nil, status: String? = nil, nextRunAt: Date? = nil) {
        self.lastRunAt = lastRunAt
        self.status = status
        self.nextRunAt = nextRunAt
    }

    enum CodingKeys: String, CodingKey {
        case status
        case lastRunAt = "last_run_at"
        case nextRunAt = "next_run_at"
    }
}

/// `GET /api/v1/knowledge`.
public struct KnowledgeOverview: Codable, Hashable, Sendable {
    public var readers: [KnowledgeReader]
    public var defaultReaders: [String]
    public var areas: [KnowledgeArea]
    /// Files right in `知识库/`, in no topic: nobody but the user can read them.
    public var looseFiles: Int
    public var organize: KnowledgeOrganize

    public init(
        readers: [KnowledgeReader], defaultReaders: [String], areas: [KnowledgeArea], looseFiles: Int = 0,
        organize: KnowledgeOrganize = KnowledgeOrganize()
    ) {
        self.readers = readers
        self.defaultReaders = defaultReaders
        self.areas = areas
        self.looseFiles = looseFiles
        self.organize = organize
    }

    enum CodingKeys: String, CodingKey {
        case readers, areas, organize
        case defaultReaders = "default_readers"
        case looseFiles = "loose_files"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        readers = try c.decode([KnowledgeReader].self, forKey: .readers)
        defaultReaders = try c.decodeIfPresent([String].self, forKey: .defaultReaders) ?? []
        areas = try c.decode([KnowledgeArea].self, forKey: .areas)
        looseFiles = try c.decodeIfPresent(Int.self, forKey: .looseFiles) ?? 0
        organize = try c.decodeIfPresent(KnowledgeOrganize.self, forKey: .organize) ?? KnowledgeOrganize()
    }

    public var topics: [KnowledgeArea] { areas.filter(\.isTopic) }
    public var vaultFolders: [KnowledgeArea] { areas.filter { !$0.isTopic } }
    public var bots: [KnowledgeReader] { readers.filter(\.isBot) }
    public var agents: [KnowledgeReader] { readers.filter { !$0.isBot } }
    public var pending: Int { topics.reduce(0) { $0 + $1.pending } }

    /// The readers in the order the backend lists them, keeping only those in `names`.
    public func ordered(_ names: [String]) -> [KnowledgeReader] {
        let chosen = Set(names)
        return readers.filter { chosen.contains($0.id) }
    }
}

/// A file in an area: a note the nightly job wrote, a source the user put there, or a file
/// of the vault's own folders.
public struct KnowledgeEntry: Codable, Hashable, Identifiable, Sendable {
    public var path: String
    public var name: String
    public var title: String
    /// "note", "source" or "file".
    public var kind: String
    /// "md", "txt", "pdf", "image", "docx" or "other".
    public var format: String
    public var size: Int
    public var modifiedAt: Date?
    /// Sources only: "pending", "organized", "failed", "skipped" or "unreadable".
    public var status: String?
    public var error: String?

    public var id: String { path }

    public init(
        path: String, name: String, title: String, kind: String, format: String, size: Int = 0,
        modifiedAt: Date? = nil, status: String? = nil, error: String? = nil
    ) {
        self.path = path
        self.name = name
        self.title = title
        self.kind = kind
        self.format = format
        self.size = size
        self.modifiedAt = modifiedAt
        self.status = status
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case path, name, title, kind, format, size, status, error
        case modifiedAt = "modified_at"
    }

    public var symbol: String { Knowledge.symbol(kind: kind, format: format) }
    public var statusText: String? { status.flatMap(Knowledge.statusText) }
}

/// `GET /api/v1/knowledge/files`.
public struct KnowledgeFiles: Codable, Hashable, Sendable {
    public var path: String
    public var area: String?
    public var readers: [String]
    public var entries: [KnowledgeEntry]

    public init(path: String, area: String?, readers: [String], entries: [KnowledgeEntry]) {
        self.path = path
        self.area = area
        self.readers = readers
        self.entries = entries
    }

    public var notes: [KnowledgeEntry] { entries.filter { $0.kind == "note" } }
    public var sources: [KnowledgeEntry] { entries.filter { $0.kind != "note" } }
}

public struct KnowledgeSourceRef: Codable, Hashable, Sendable {
    public var path: String
    /// Where in it, e.g. "第 18 页".
    public var label: String?

    public init(path: String, label: String? = nil) {
        self.path = path
        self.label = label
    }

    public var name: String { Knowledge.fileName(path) }
}

/// `GET /api/v1/knowledge/file`: one file with its text (a PDF's or a picture's as the Mac
/// read it), a note's sources and a source's notes.
public struct KnowledgeFile: Codable, Hashable, Sendable {
    public var path: String
    public var name: String
    public var title: String
    public var kind: String
    public var format: String
    public var size: Int
    public var modifiedAt: Date?
    public var area: String?
    public var readers: [String]
    public var content: String
    /// The content is text read out of a PDF, a picture or a Word file.
    public var extracted: Bool
    public var truncated: Bool
    public var sources: [KnowledgeSourceRef]
    public var notes: [String]
    public var organizedBy: String?
    public var organizedAt: Date?
    public var status: String?
    public var error: String?

    public init(
        path: String, name: String, title: String, kind: String, format: String, size: Int = 0,
        modifiedAt: Date? = nil, area: String?, readers: [String], content: String, extracted: Bool = false,
        truncated: Bool = false, sources: [KnowledgeSourceRef] = [], notes: [String] = [],
        organizedBy: String? = nil, organizedAt: Date? = nil, status: String? = nil, error: String? = nil
    ) {
        self.path = path
        self.name = name
        self.title = title
        self.kind = kind
        self.format = format
        self.size = size
        self.modifiedAt = modifiedAt
        self.area = area
        self.readers = readers
        self.content = content
        self.extracted = extracted
        self.truncated = truncated
        self.sources = sources
        self.notes = notes
        self.organizedBy = organizedBy
        self.organizedAt = organizedAt
        self.status = status
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case path, name, title, kind, format, size, area, readers, content, extracted, truncated
        case sources, notes, status, error
        case modifiedAt = "modified_at"
        case organizedBy = "organized_by"
        case organizedAt = "organized_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        name = try c.decode(String.self, forKey: .name)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? name
        kind = try c.decode(String.self, forKey: .kind)
        format = try c.decodeIfPresent(String.self, forKey: .format) ?? "other"
        size = try c.decodeIfPresent(Int.self, forKey: .size) ?? 0
        modifiedAt = try c.decodeIfPresent(Date.self, forKey: .modifiedAt)
        area = try c.decodeIfPresent(String.self, forKey: .area)
        readers = try c.decodeIfPresent([String].self, forKey: .readers) ?? []
        content = try c.decodeIfPresent(String.self, forKey: .content) ?? ""
        extracted = try c.decodeIfPresent(Bool.self, forKey: .extracted) ?? false
        truncated = try c.decodeIfPresent(Bool.self, forKey: .truncated) ?? false
        sources = try c.decodeIfPresent([KnowledgeSourceRef].self, forKey: .sources) ?? []
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
        organizedBy = try c.decodeIfPresent(String.self, forKey: .organizedBy)
        organizedAt = try c.decodeIfPresent(Date.self, forKey: .organizedAt)
        status = try c.decodeIfPresent(String.self, forKey: .status)
        error = try c.decodeIfPresent(String.self, forKey: .error)
    }

    /// The text as the App shows it: a note's 「## 来源」 goes (the App lists the sources as
    /// rows to open), and Obsidian links read as their names.
    public var displayText: String {
        Knowledge.readable(kind == "note" ? Knowledge.dropSection("来源", from: content) : content)
    }

    /// The Mac keeps the original, which the system can show (a PDF, a picture, a Word file).
    public var hasOriginal: Bool { ["pdf", "image", "docx"].contains(format) }
    public var statusText: String? { status.flatMap(Knowledge.statusText) }
}

public struct KnowledgeSearchResult: Codable, Hashable, Identifiable, Sendable {
    public var path: String
    public var name: String
    public var title: String
    public var kind: String
    public var area: String?
    public var excerpt: String

    public var id: String { path }

    public init(path: String, name: String, title: String, kind: String, area: String?, excerpt: String) {
        self.path = path
        self.name = name
        self.title = title
        self.kind = kind
        self.area = area
        self.excerpt = excerpt
    }
}

struct KnowledgeSearchPage: Codable, Sendable {
    var results: [KnowledgeSearchResult]
}

/// `PATCH /api/v1/knowledge`.
public struct KnowledgePatch: Codable, Hashable, Sendable {
    public var defaultReaders: [String]?
    public var agents: [String]?

    public init(defaultReaders: [String]? = nil, agents: [String]? = nil) {
        self.defaultReaders = defaultReaders
        self.agents = agents
    }

    enum CodingKeys: String, CodingKey {
        case agents
        case defaultReaders = "default_readers"
    }
}

struct KnowledgeAccess: Codable, Sendable {
    var path: String
    var readers: [String]
}

struct KnowledgeTopic: Codable, Sendable {
    var name: String
    var readers: [String]?
}

public enum Knowledge {
    /// The job that turns new sources into notes, for `runJob`.
    public static let organizeJob = "knowledge_organize"
    /// Files the backend can read text out of; others upload but stay unread.
    public static let uploadTypes: [String: String] = [
        "md": "text/markdown", "txt": "text/plain", "pdf": "application/pdf", "png": "image/png",
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "heic": "image/heic",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]

    public static func contentType(for name: String) -> String? {
        uploadTypes[(name as NSString).pathExtension.lowercased()]
    }

    public static func fileName(_ path: String) -> String {
        path.split(separator: "/").last.map(String.init) ?? path
    }

    /// A topic name the backend takes: 1–40 characters, no slash, colon or leading dot, and
    /// not the notes folder's name.
    public static func validTopic(_ name: String) -> Bool {
        let name = name.trimmingCharacters(in: .whitespaces)
        return (1...40).contains(name.count) && !name.hasPrefix(".") && name != "整理"
            && !name.contains(where: { "/\\:".contains($0) })
    }

    static func statusText(_ status: String) -> String? {
        switch status {
        case "pending": return "待整理"
        case "organized": return "已整理"
        case "failed": return "整理失败，下次再试"
        case "skipped": return "不整理：这个主题谁都不能读"
        case "unreadable": return "读不出文字"
        default: return nil
        }
    }

    static func symbol(kind: String, format: String) -> String {
        if kind == "note" { return "text.book.closed" }
        switch format {
        case "pdf": return "doc.richtext"
        case "image": return "photo"
        case "docx": return "doc.text"
        case "md", "txt": return "doc.plaintext"
        default: return "doc"
        }
    }

    /// Removes a "## <title>" section (up to the next heading of level two or above).
    static func dropSection(_ title: String, from text: String) -> String {
        var kept: [Substring] = []
        var dropping = false
        for line in text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline) {
            if let heading = MarkdownLine.headingTitle(line), line.drop(while: { $0 == " " }).hasPrefix("#") {
                let level = line.drop(while: { $0 == " " }).prefix(while: { $0 == "#" }).count
                if level <= 2 { dropping = level == 2 && heading == title }
            }
            if !dropping { kept.append(line) }
        }
        return kept.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Obsidian links read as what Obsidian shows: `[[知识库/课程/第5章 图.pdf]]` as 「第5章 图.pdf」,
    /// `[[最短路径|Dijkstra]]` as 「Dijkstra」.
    static func readable(_ text: String) -> String {
        guard let pattern = try? NSRegularExpression(pattern: #"!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]"#) else {
            return text
        }
        var result = ""
        var last = text.startIndex
        for match in pattern.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let whole = Range(match.range, in: text), let target = Range(match.range(at: 1), in: text)
            else { continue }
            result += text[last..<whole.lowerBound]
            if let alias = Range(match.range(at: 2), in: text) {
                result += text[alias]
            } else {
                result += fileName(String(text[target]).trimmingCharacters(in: .whitespaces))
            }
            last = whole.upperBound
        }
        result += text[last...]
        return result
    }
}
