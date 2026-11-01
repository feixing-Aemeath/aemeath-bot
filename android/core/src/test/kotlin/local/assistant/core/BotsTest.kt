package local.assistant.core

import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

class BotsTest {
    private val catalog = StickerCatalog(
        setOf("aemeath", "orange_daisy", "moning"),
        mapOf("assistant" to PluginMascot("aemeath"), "knowledge" to PluginMascot("orange_daisy")),
        mapOf("aemeath" to "助理Bot", "orange_daisy" to "知识库Bot", "moning" to "作业Bot"),
        emptySet()
    )

    private fun plugin(id: String, sticker: String = "moning", source: Boolean = false) = PluginView(
        id,
        id,
        provides = if (source) listOf("source") else listOf("cards"),
        mascot = PluginMascot(sticker),
        enabled = true,
        state = "enabled"
    )

    private fun bot(id: String = "homework") = Bot(
        id,
        "作业Bot",
        "作业",
        BotKind.CHARACTER,
        PluginMascot("moning")
    )

    @Test
    fun orderIsAssistantCharactersKnowledgeSources() {
        val bots = Bot.build(listOf(plugin("qq_screen", source = true), plugin("homework")), catalog)
        assertEquals(
            listOf(
                "assistant",
                "homework",
                "knowledge",
                "qq_screen"
            ),
            bots.map { it.id }
        )
        assertEquals("作业Bot", bots[1].name)
        assertEquals(BotKind.SOURCE, bots.last().kind)
    }

    @Test
    fun sharedCharactersMergeAndKeepDuties() {
        val bots = Bot.build(listOf(plugin("homework"), plugin("timetable")), catalog)
        val b = bots[1]
        assertEquals(listOf("homework", "timetable"), b.pluginIds)
        assertEquals("作业 · 课表", b.tag)
        assertEquals("“作业”与“课表”", b.intro)
    }

    @Test
    fun weatherAndKnowledgeInputsJoinBuiltIns() {
        val bots = Bot.build(
            listOf(
                plugin("weather", "aemeath"),
                plugin("goodnotes", "orange_daisy"),
                plugin("class_photos", "orange_daisy"),
                plugin("video_notes", "orange_daisy")
            ),
            catalog
        )
        assertEquals(2, bots.size)
        assertEquals(listOf("weather"), bots[0].joined)
        assertEquals(listOf("goodnotes", "class_photos", "video_notes"), bots[1].joined)
        assertEquals(listOf("知识库", "课堂笔记"), bots[1].duties)
    }

    @Test
    fun unknownStickersDoNotMergeUnrelatedPlugins() {
        val bots = Bot.build(listOf(plugin("x", "missing"), plugin("y", "missing")), catalog)
        assertEquals(4, bots.size)
    }

    @Test
    fun enabledPluginsSortBeforeDisabledOnes() {
        val bots = Bot.build(listOf(plugin("old").copy(state = "disabled", enabled = false), plugin("new")), catalog)
        assertEquals("new", bots[1].id)
    }

    @Test
    fun pluginDigestsAreDailyBubblesWithTheirTodos() {
        val ds = listOf(
            Digest(
                "d",
                "2026-10-15",
                "homework",
                "交作业\n通知",
                testNow.toString()
            ),
            Digest(
                "x",
                "2026-10-15",
                "other",
                "不显示",
                testNow.toString()
            )
        )
        val messages = BotTimeline.messages(
            bot(),
            null,
            ds,
            listOf(testTodo(plugin = "homework")),
            emptyList(),
            emptyList(),
            emptyList(),
            testNow,
            testZone
        )
        assertEquals(1, messages.size)
        assertEquals("交作业\n通知", messages.single().text)
        assertEquals(1, messages.single().todos.size)
    }

    @Test
    fun healthReportsIncludePendingReadyAndFailures() {
        val reports = listOf(
            HealthReport(
                "1",
                "2026-10-15",
                "morning",
                "ready",
                "睡眠",
                "正文",
                createdAt = testNow.toString(),
                updatedAt = testNow.toString()
            ),
            HealthReport(
                "2",
                "2026-10-15",
                "weekly",
                "failed",
                error = "report_not_written",
                createdAt = testNow.toString(),
                updatedAt = testNow.toString()
            )
        )
        val messages = BotTimeline.messages(
            bot("health"),
            null,
            emptyList(),
            emptyList(),
            emptyList(),
            reports,
            emptyList(),
            testNow,
            testZone
        )
        assertEquals("晨间简报 · 睡眠", messages[0].title)
        assertEquals("正文", messages[0].text)
        assertEquals("没有生成：简报没有写出来", messages[1].text)
        assertTrue(messages[1].failed)
    }

    @Test
    fun assistantIncludesTodaysRunsAndSummary() {
        val b = Bot.build(emptyList(), catalog).first()
        val runs = listOf(testRun(), testRun(ago = 90000))
        val messages = BotTimeline.messages(
            b,
            Today("2026-10-15", "今天的事情", emptyList()),
            emptyList(),
            emptyList(),
            runs,
            emptyList(),
            emptyList(),
            testNow,
            testZone
        )
        assertEquals(2, messages.size)
        assertTrue(messages[0].text.contains("提炼完一次待办"))
        assertEquals("今天的总结", messages[1].title)
    }

    @Test
    fun pendingRepliesDoNotBecomeEmptyBubbles() {
        fun line(
            id: String,
            author: String,
            status: String,
            error: String? = null
        ) = ChatMessage(
            id,
            "assistant",
            author,
            "文字",
            status,
            error,
            testNow.toString(),
            testNow.toString()
        )
        val messages = BotTimeline.chat(
            listOf(
                line("1", "me", "ready"),
                line("2", "bot", "pending"),
                line("3", "bot", "ready"),
                line(
                    "4",
                    "bot",
                    "failed",
                    "interrupted"
                )
            )
        )
        assertEquals(listOf("me", "bot", "app"), messages.map { it.author })
        assertTrue(messages.last().text.contains("后端重启"))
        assertTrue(messages.last().failed)
    }

    @Test
    fun chatStartsWithLastTwoDaysThatActuallyHaveMessages() {
        val messages = listOf(
            0L,
            2L,
            5L,
            5L,
            7L
        )
            .mapIndexed { i, day ->
                BotMessage(
                    "$i",
                    "bot",
                    testNow.minusSeconds(day * 86400),
                    "文字"
                )
            }
            .sortedBy { it.at }
        val(visible, older) = BotTimeline.recent(messages, 2, testZone)
        assertEquals(2, visible.size)
        assertEquals(2, older)
        assertEquals(messages, BotTimeline.recent(messages, 0, testZone).first)
    }

    @Test
    fun unreadCountsOnlyNewBotMessagesAndSkipsInitialHistory() {
        val messages = listOf(
            BotMessage(
                "1",
                "bot",
                testNow.minusSeconds(1),
                "旧"
            ),
            BotMessage(
                "2",
                "me",
                testNow.plusSeconds(1),
                "我"
            ),
            BotMessage(
                "3",
                "app",
                testNow.plusSeconds(1),
                "失败"
            ),
            BotMessage(
                "4",
                "bot",
                testNow.plusSeconds(1),
                "新"
            )
        )
        assertEquals(1, BotTimeline.unread(messages, null, testNow))
        assertEquals(0, BotTimeline.unread(messages, testNow.plusSeconds(2), testNow))
    }

    @Test
    fun dayDividersAndPreviewText() {
        assertEquals("今天", BotTimeline.day(testNow, testNow, testZone))
        assertEquals("昨天", BotTimeline.day(testNow.minusSeconds(86400), testNow, testZone))
        assertEquals("10月12日", BotTimeline.day(testNow.minusSeconds(3 * 86400), testNow, testZone))
        assertEquals(
            "标题",
            BotMessage(
                "x",
                "bot",
                testNow,
                "**标题**\n正文"
            ).preview
        )
    }

    @Test
    fun mockRepliesAfterADelay() = runTest {
        var now = testNow
        val api = MockBackend({ now }, testZone)
        val accepted = api.sendChat(
            "assistant",
            "你好",
            "助理Bot",
            ChatChoice()
        )
        assertEquals("pending", accepted.reply.status)
        assertTrue(api.chat("assistant").items.any { it.status == "pending" })
        now = now.plusSeconds(3)
        assertTrue(api.chat("assistant").items.none { it.status == "pending" })
        assertTrue(api.chat("assistant").items.first().text.contains("模拟数据"))
    }
}
