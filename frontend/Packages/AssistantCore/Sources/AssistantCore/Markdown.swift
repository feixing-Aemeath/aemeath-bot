import Foundation

/// A line of Agent-written Markdown (reports, digests, chat replies) as the App draws it:
/// "#" headings stand apart, every other line keeps its text and inline syntax. Lists and
/// tables stay plain lines here; RichLine reads them.
public enum MarkdownLine: Equatable, Sendable {
    case heading(String)
    case text(String)

    public static func lines(_ source: String) -> [MarkdownLine] {
        var fenced = false
        // "\r\n" is one Character in Swift, so split on any newline rather than on "\n".
        let lines = source.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline)
        return lines.map { line in
            if line.drop(while: { $0 == " " }).hasPrefix("```") {
                fenced.toggle()
                return .text(String(line))
            }
            if !fenced, let title = headingTitle(line) {
                return .heading(title)
            }
            return .text(String(line))
        }
    }

    /// The title of a section in the Bots' fixed format: a line that is all bold, as
    /// 「**新待办**」 or 「**通知**：」. A line that only starts bold (「**天气** 阵雨」) is text.
    public static func sectionTitle(_ line: String) -> String? {
        var text = line.trimmingCharacters(in: .whitespaces)
        if text.hasSuffix("：") || text.hasSuffix(":") { text.removeLast() }
        guard text.count > 4, text.hasPrefix("**"), text.hasSuffix("**") else { return nil }
        let inner = text.dropFirst(2).dropLast(2)
        guard !inner.isEmpty, !inner.contains("*") else { return nil }
        return String(inner).trimmingCharacters(in: .whitespaces)
    }

    /// The title of an ATX heading: up to three spaces, one to six "#", then a space or the
    /// end of the line, and an optional closing run of "#". "#话题" is not a heading.
    static func headingTitle(_ line: Substring) -> String? {
        let indented = line.drop(while: { $0 == " " })
        guard line.count - indented.count <= 3 else { return nil }
        let marks = indented.prefix(while: { $0 == "#" })
        guard (1...6).contains(marks.count) else { return nil }
        let rest = indented.dropFirst(marks.count)
        guard rest.isEmpty || rest.first == " " || rest.first == "\t" else { return nil }
        var title = rest.trimmingCharacters(in: .whitespaces)
        if let closing = title.range(of: #"(^|\s)#+$"#, options: .regularExpression) {
            title = title[..<closing.lowerBound].trimmingCharacters(in: .whitespaces)
        }
        return title
    }
}

/// A GitHub-style table in a Bot's message: a line of column names, a line of dashes (colons
/// set a column's alignment), then a row per line until a line without a bar.
public struct MarkdownTable: Equatable, Sendable {
    public enum Alignment: Equatable, Sendable { case leading, center, trailing }

    public var header: [String]
    public var alignments: [Alignment]
    /// Each as long as the header: missing cells are empty, extra ones dropped.
    public var rows: [[String]]

    public init(header: [String], alignments: [Alignment], rows: [[String]]) {
        self.header = header
        self.alignments = alignments
        self.rows = rows
    }

    /// The cells of a line with at least one bar, "| a | b |" or "a | b"; "\|" is a bar inside
    /// a cell.
    static func cells(_ line: String) -> [String]? {
        var text = line.trimmingCharacters(in: .whitespaces)
        guard text.contains("|") else { return nil }
        if text.hasPrefix("|") { text.removeFirst() }
        if text.hasSuffix("|"), !text.hasSuffix("\\|") { text.removeLast() }
        var cells: [String] = []
        var current = ""
        var escaped = false
        for character in text {
            if escaped {
                if character != "|" { current.append("\\") }
                current.append(character)
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "|" {
                cells.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            } else {
                current.append(character)
            }
        }
        if escaped { current.append("\\") }
        cells.append(current.trimmingCharacters(in: .whitespaces))
        return cells
    }

    /// The line under the column names: every cell dashes, with a colon at either end or both.
    static func alignments(_ cells: [String]) -> [Alignment]? {
        var result: [Alignment] = []
        for cell in cells {
            guard cell.wholeMatch(of: #/:?-+:?/#) != nil else { return nil }
            switch (cell.hasPrefix(":"), cell.hasSuffix(":")) {
            case (true, true): result.append(.center)
            case (false, true): result.append(.trailing)
            default: result.append(.leading)
            }
        }
        return result
    }

    /// The table that starts at `start`, and the index of the first line after it.
    static func starting(at start: Int, in lines: [MarkdownLine]) -> (table: MarkdownTable, next: Int)? {
        guard start + 1 < lines.count,
            case .text(let first) = lines[start], case .text(let second) = lines[start + 1],
            let header = cells(first), let marks = cells(second), marks.count == header.count,
            let alignments = alignments(marks)
        else { return nil }
        var rows: [[String]] = []
        var next = start + 2
        while next < lines.count, case .text(let raw) = lines[next],
            !raw.trimmingCharacters(in: .whitespaces).hasPrefix("```"), let row = cells(raw)
        {
            let padded = row + Array(repeating: "", count: max(0, header.count - row.count))
            rows.append(Array(padded.prefix(header.count)))
            next += 1
        }
        return (MarkdownTable(header: header, alignments: alignments, rows: rows), next)
    }
}

/// A line of a Bot's message in the fixed format (bold section names, one fact per line) as
/// the App lays it out: a section name small in the Bot's color, an item with a dot and its
/// wrapped lines indented under the text, a numbered item, a heading, a table, a gap, or plain
/// text.
public enum RichLine: Equatable, Sendable {
    case heading(String)
    case section(String)
    case bullet(String)
    case numbered(String, String)
    case table(MarkdownTable)
    case blank
    case text(String)

    public static func lines(_ source: String) -> [RichLine] {
        var fenced = false
        let marked = MarkdownLine.lines(source)
        var result: [RichLine] = []
        var index = 0
        while index < marked.count {
            let line = marked[index]
            index += 1
            switch line {
            case .heading(let title):
                result.append(.heading(title))
            case .text(let raw):
                let text = raw.trimmingCharacters(in: .whitespaces)
                if text.hasPrefix("```") { fenced.toggle() }
                if fenced || text.hasPrefix("```") {
                    result.append(.text(raw))
                } else if let found = MarkdownTable.starting(at: index - 1, in: marked) {
                    result.append(.table(found.table))
                    index = found.next
                } else {
                    result.append(item(raw, text))
                }
            }
        }
        return result
    }

    /// A line outside code and tables: `text` is `raw` without the spaces around it.
    static func item(_ raw: String, _ text: String) -> RichLine {
        if text.isEmpty { return .blank }
        for marker in ["- ", "* ", "• "] where text.hasPrefix(marker) {
            return .bullet(String(text.dropFirst(marker.count)).trimmingCharacters(in: .whitespaces))
        }
        if let match = text.firstMatch(of: #/^(\d{1,2})(?:[.)]\s+|[、）]\s*)(.+)$/#) {
            return .numbered("\(match.1).", String(match.2))
        }
        if let title = MarkdownLine.sectionTitle(text) { return .section(title) }
        return .text(raw)
    }

    /// Plain text all through: drawn as one block that can be selected at once.
    public static func isPlain(_ lines: [RichLine]) -> Bool {
        lines.allSatisfy {
            switch $0 {
            case .text, .blank: return true
            default: return false
            }
        }
    }
}
