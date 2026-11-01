import Foundation

/// A line of Agent-written Markdown (reports, digests, chat replies) as the App draws it:
/// "#" headings stand apart, every other line keeps its text and inline syntax. Lists and
/// tables stay plain lines.
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
