@file:OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)

package local.assistant.core

import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class KnowledgeTest {
    @Test
    fun documentedOverviewAndOldAreaDecode() {
        val library = fromFixture<KnowledgeOverview>("knowledge-overview")
        assertTrue(library.readers.isNotEmpty())
        assertTrue(library.topics.isNotEmpty())
        assertTrue(library.vaultFolders.isNotEmpty())
        assertEquals(library.topics.sumOf { it.pending }, library.pending)
        val old = Wire.decodeFromString<KnowledgeArea>("""{"path":"知识库/数学","name":"数学"}""")
        assertTrue(old.isTopic)
        assertEquals("空的，放点资料进来", old.summary)
        assertEquals(0, old.files)
    }

    @Test
    fun notesHideSourceSectionButKeepOtherSections() {
        val note = fromFixture<KnowledgeFile>("knowledge-note")
        assertFalse(note.displayText.contains("## 来源"))
        assertTrue(note.sources.isNotEmpty())
        assertEquals(
            "# 标题\n正文\n## 我的批注\n保留",
            Knowledge.dropSection("来源", "# 标题\n正文\n## 来源\n删除\n### 子节\n删除\n## 我的批注\n保留")
        )
    }

    @Test
    fun obsidianLinksHaveReadableLabels() {
        assertEquals("资料.pdf 和 别名", Knowledge.readable("[[知识库/资料.pdf]] 和 [[path/note|别名]]"))
        assertEquals("照片.png", Knowledge.readable("![[folder/照片.png]]"))
    }

    @Test
    fun topicNamesAndUploadTypesMatchPhone() {
        listOf("数据结构", "  数学  ", "a".repeat(40)).forEach { assertTrue(Knowledge.validTopic(it)) }
        listOf(
            "",
            " ",
            ".hidden",
            "整理",
            "a/b",
            "a\\b",
            "a:b",
            "a".repeat(41)
        ).forEach { assertFalse(Knowledge.validTopic(it)) }
        assertEquals("application/pdf", Knowledge.contentType("资料.PDF"))
        assertEquals("image/heic", Knowledge.contentType("照片.heic"))
        assertNull(Knowledge.contentType("压缩.zip"))
    }

    @Test
    fun fileStatusAndSummariesAreReadable() {
        assertEquals("待整理", Knowledge.statusText("pending"))
        assertEquals("不整理：这个主题谁都不能读", Knowledge.statusText("skipped"))
        assertNull(Knowledge.statusText("future"))
        assertEquals(
            "2 篇整理 · 3 份资料",
            KnowledgeArea(
                "x",
                "x",
                notes = 2,
                sources = 3
            ).summary
        )
        assertEquals(
            "4 个文件",
            KnowledgeArea(
                "x",
                "x",
                kind = "system",
                files = 4
            ).summary
        )
    }

    @Test
    fun rapidPermissionRevocationsSaveInOrder() = runTest {
        val selection = ReaderSelection(listOf("a", "b", "c"))
        val writes = mutableListOf<List<String>>()
        val first = launch {
            selection.set("a", false) {
                delay(100)
                writes += it
                it
            }
        }
        runCurrent()
        val second = launch {
            selection.set("b", false) {
                writes += it
                it
            }
        }
        runCurrent()
        assertEquals(listOf("c"), selection.chosen)
        advanceUntilIdle()
        first.join()
        second.join()
        assertEquals(listOf(listOf("b", "c"), listOf("c")), writes)
        assertEquals(listOf("c"), selection.chosen)
    }

    @Test
    fun failedSaveDoesNotDiscardNewerEditAndLaterFailureRollsBack() = runTest {
        val selection = ReaderSelection(listOf("a", "b"))
        val first = launch {
            runCatching {
                selection.set("a", false) {
                    delay(100)
                    throw APIError("server")
                }
            }
        }
        runCurrent()
        val second = launch { selection.set("b", false) { it } }
        runCurrent()
        assertTrue(selection.chosen.isEmpty())
        advanceUntilIdle()
        first.join()
        second.join()
        assertTrue(selection.chosen.isEmpty())
        runCatching { selection.set("a", true) { throw APIError("transport") } }
        assertTrue(selection.chosen.isEmpty())
    }

    @Test
    fun mockKeepsLockedReadersAndRejectsUnknownNames() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val library = api.knowledge()
        val locked = library.areas.first { it.locked.isNotEmpty() }
        assertTrue(api.setReaders(locked.path, emptyList()).readers.containsAll(locked.locked))
        expectError("invalid_request") { api.setReaders(locked.path, listOf("not-a-reader")) }
    }

    @Test
    fun mockCreatesTopicsUploadsSearchesAndRejectsOverwrite() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val area = api.createTopic("安卓测试")
        val path = "${area.path}/笔记.md"
        api.uploadKnowledge(path, "安卓测试资料".toByteArray(), "text/markdown")
        assertEquals(path, api.knowledgeFiles(area.path).entries.single().path)
        assertEquals("安卓测试资料", api.knowledgeFile(path).content)
        assertTrue(api.searchKnowledge("安卓测试资料").any { it.path == path })
        expectError("conflict") { api.uploadKnowledge(path, byteArrayOf(), "text/markdown") }
        expectError("invalid_request") { api.createTopic("../private") }
        expectError("conflict") { api.createTopic("安卓测试") }
    }

    @Test
    fun privateTopicSkipsOrganizingAndDoesNotInheritFutureDefaults() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val area = api.createTopic("私密", emptyList())
        val entry = api.uploadKnowledge("${area.path}/日记.txt", "个人内容".toByteArray(), "text/plain")
        assertEquals("skipped", entry.status)
        val reader = api.knowledge().readers.first().id
        api.updateKnowledge(patch("default_readers" to JsonArray(listOf(JsonPrimitive(reader)))))
        assertTrue(api.knowledge().areas.first { it.path == area.path }.readers.isEmpty())
    }
}
