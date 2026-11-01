package local.assistant.core

data class MarkdownLine(val text: String, val heading: Boolean)

object Markdown {
    fun heading(line: String): String? {
        val match = Regex("^ {0,3}#{1,6}(?:[ \\t]+(.*)|$)").matchEntire(line) ?: return null
        return match.groupValues[1].trim().replace(Regex("(^|\\s)#+$"), "").trim()
    }

    fun lines(source: String): List<MarkdownLine> {
        var fenced = false
        return source.lines()
            .map { line ->
                if (line.trimStart().startsWith("```")) {
                    fenced = !fenced
                    MarkdownLine(line, false)
                } else {
                    val title = if (!fenced) heading(line) else null
                    MarkdownLine(title ?: line, title != null)
                }
            }
    }
}
