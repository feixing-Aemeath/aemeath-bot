package local.assistant.desktop

import org.junit.Assert.*
import org.junit.Test

class InlineMarkdownTest {
    @Test
    fun nestedEmphasisPreservesTheVisibleText() {
        val runs = InlineMarkdown.parse("今天 **交 *作业* 和笔记**。")
        assertEquals("今天 交 作业 和笔记。", runs.joinToString("") { it.text })
        assertTrue(runs.first { it.text == "作业" }.style.bold)
        assertTrue(runs.first { it.text == "作业" }.style.italic)
    }

    @Test
    fun inlineCodeDoesNotParseItsContentsAsInstructionsOrFormatting() {
        val runs = InlineMarkdown.parse("运行 `**a_b**` 即可")
        val code = runs.single { it.style.code }
        assertEquals("**a_b**", code.text)
        assertFalse(code.style.bold)
    }

    @Test
    fun malformedAndEscapedMarkersRemainReadable() {
        assertEquals("**未结束", InlineMarkdown.parse("**未结束").joinToString("") { it.text })
        assertEquals("*普通文字*", InlineMarkdown.parse("\\*普通文字\\*").joinToString("") { it.text })
        assertEquals("source_plugin", InlineMarkdown.parse("source_plugin").joinToString("") { it.text })
    }

    @Test
    fun linksKeepTheirLabelAndTargetSeparately() {
        val runs = InlineMarkdown.parse("见 [**资料**](https://example.invalid/notes)。")
        val link = runs.single { it.style.link != null }
        assertEquals("资料", link.text)
        assertTrue(link.style.bold)
        assertEquals("https://example.invalid/notes", link.style.link)
    }

    @Test
    fun combinedEmphasisAndEmptyInputAreSafe() {
        assertTrue(InlineMarkdown.parse("").isEmpty())
        val run = InlineMarkdown.parse("***重要***").single()
        assertEquals("重要", run.text)
        assertTrue(run.style.bold && run.style.italic)
    }
}
