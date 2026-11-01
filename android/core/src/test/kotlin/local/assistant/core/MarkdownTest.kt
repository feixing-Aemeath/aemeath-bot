package local.assistant.core

import org.junit.Assert.*
import org.junit.Test

class MarkdownTest {
    @Test
    fun reportsBecomeHeadings() {
        assertEquals(
            listOf(MarkdownLine("晨间简报", true), MarkdownLine("正文", false), MarkdownLine("睡眠", true)),
            Markdown.lines("# 晨间简报\n正文\n## 睡眠")
        )
    }

    @Test
    fun onlyRealHeadingsArePromoted() {
        assertNull(Markdown.heading("#标签"))
        assertNull(Markdown.heading("    # 代码"))
        assertNull(Markdown.heading("####### 多了"))
        assertEquals("标题", Markdown.heading("  ## 标题 ###"))
        assertEquals("", Markdown.heading("#"))
    }

    @Test
    fun fencesAndLineEndingsArePreserved() {
        val lines = Markdown.lines("# 标题\r\n```text\r\n## 代码\r\n```\r\n## 章节")
        assertEquals(
            listOf(
                true,
                false,
                false,
                false,
                true
            ),
            lines.map { it.heading }
        )
        assertEquals("## 代码", lines[2].text)
    }
}
