package local.assistant.desktop

data class InlineStyle(
    val bold: Boolean = false,
    val italic: Boolean = false,
    val code: Boolean = false,
    val link: String? = null,
)

data class InlineRun(val text: String, val style: InlineStyle = InlineStyle())

object InlineMarkdown {
    fun parse(text: String, style: InlineStyle = InlineStyle()): List<InlineRun> {
        val result = mutableListOf<InlineRun>()
        val plain = StringBuilder()
        fun flush() {
            if (plain.isNotEmpty()) {
                result.add(InlineRun(plain.toString(), style))
                plain.clear()
            }
        }
        fun closing(marker: String, from: Int): Int {
            var at = text.indexOf(marker, from)
            while (at > 0 && text[at - 1] == '\\') at = text.indexOf(marker, at + marker.length)
            return at
        }
        var index = 0
        while (index < text.length) {
            val character = text[index]
            if (character == '\\' && index + 1 < text.length && text[index + 1] in "\\`*_[]()") {
                plain.append(text[index + 1])
                index += 2
                continue
            }
            if (character == '`') {
                val end = text.indexOf('`', index + 1)
                if (end > index + 1) {
                    flush()
                    result.add(InlineRun(text.substring(index + 1, end), style.copy(code = true)))
                    index = end + 1
                    continue
                }
            }
            if (character == '[') {
                val labelEnd = closing("](", index + 1)
                val urlEnd = if (labelEnd >= 0) text.indexOf(')', labelEnd + 2) else -1
                if (labelEnd >= 0 && urlEnd > labelEnd + 2) {
                    flush()
                    result.addAll(parse(text.substring(index + 1, labelEnd), style.copy(link = text.substring(labelEnd + 2, urlEnd))))
                    index = urlEnd + 1
                    continue
                }
            }
            val marker = when {
                text.startsWith("***", index) || text.startsWith("___", index) -> text.substring(index, index + 3)
                text.startsWith("**", index) || text.startsWith("__", index) -> text.substring(index, index + 2)
                character == '*' || character == '_' -> character.toString()
                else -> null
            }
            val inWord = character == '_' && index > 0 && text[index - 1].isLetterOrDigit()
            if (marker != null && !inWord) {
                val end = closing(marker, index + marker.length)
                if (end > index + marker.length) {
                    flush()
                    val next = style.copy(bold = style.bold || marker.length >= 2, italic = style.italic || marker.length != 2)
                    result.addAll(parse(text.substring(index + marker.length, end), next))
                    index = end + marker.length
                    continue
                }
            }
            plain.append(character)
            index++
        }
        flush()
        return result
    }
}
