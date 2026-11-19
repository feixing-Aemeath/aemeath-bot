package local.assistant.core

import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

class BotsTest {
    @Test
    fun newDraftSurvivesDelayedSendSuccessAndFailure() = runTest {
        for (success in listOf(true, false)) {
            var draft = "第一句"
            val started = CompletableDeferred<String>()
            val result = CompletableDeferred<Boolean>()
            val sending = launch {
                sendDraft(draft, { draft }, { draft = "" }) {
                    started.complete(it)
                    result.await()
                }
            }
            assertEquals("第一句", started.await())
            draft = "下一句草稿"
            result.complete(success)
            sending.join()
            assertEquals("下一句草稿", draft)
        }
    }

    @Test
    fun successfulSendClearsOnlyTheSubmittedDraftAndFailureKeepsIt() = runTest {
        var draft = "第一句"
        sendDraft(draft, { draft }, { draft = "" }) { false }
        assertEquals("第一句", draft)
        sendDraft(draft, { draft }, { draft = "" }) { true }
        assertEquals("", draft)
    }

    @Test
    fun cancelledSendKeepsDraftAndPropagatesCancellation() = runTest {
        var draft = "第一句"
        try {
            sendDraft(draft, { draft }, { draft = "" }) { throw CancellationException() }
            fail("Cancellation must propagate")
        } catch (_: CancellationException) {}
        assertEquals("第一句", draft)
    }

    @Test
    fun oldChatSnapshotsCannotEraseMessagesOrRegressReadyReplies() {
        val me = ChatMessage("m", "assistant", "me", "第一句", "ready", createdAt = testNow.toString(), updatedAt = testNow.toString())
        val pending = me.copy(id = "r", author = "bot", text = "", status = "pending")
        val ready = pending.copy(text = "回复", status = "ready", updatedAt = testNow.plusSeconds(1).toString())
        val existing = listOf(me, ready)
        assertEquals(existing, BotTimeline.mergeChat(existing, emptyList()))
        assertEquals(existing, BotTimeline.mergeChat(existing, listOf(pending, me)))
        assertEquals(existing, BotTimeline.mergeChat(existing, listOf(ready.copy(status = "pending"))))
        assertEquals(listOf(me, pending), BotTimeline.mergeChat(emptyList(), listOf(pending, me)))
    }

    @Test
    fun aHiddenPasswordReplacesTheCachedText() {
        val cached = ChatMessage("m", "assistant", "me", "密码是合成密码", "ready", createdAt = testNow.toString(), updatedAt = testNow.toString())
        val hidden = cached.copy(text = "密码是（密码已隐藏）")
        assertEquals(listOf(hidden), BotTimeline.mergeChat(listOf(cached), listOf(hidden)))
    }

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
            listOf(testTodo(plugin = "homework"), testTodo("cancelled", "homework").copy(status = "cancelled")),
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
        assertEquals("今天提炼了 1 次待办，最近一次 20:50 · Claude Code", messages[0].text)
        assertEquals("今天的总结", messages[1].title)
    }

    @Test
    fun successfulExtractionsCollapseByFinishTimeAndKeepProblemsVisible() {
        val b = Bot.build(emptyList(), catalog).first()
        val latest = testRun(ago = 3600, seconds = 3500).copy(id = "latest", harness = "codex")
        val runs = listOf(
            latest,
            testRun(ago = 90000).copy(id = "yesterday"),
            testRun(job = "backup").copy(id = "backup"),
            testRun(ago = 600).copy(id = "other"),
            testRun(status = "failed", ago = 90, error = "quota_exhausted").copy(id = "failed", attempt = 2),
            testRun(status = "cancelled", ago = 60).copy(id = "cancelled"),
            testRun(status = "running", ago = 10, seconds = null).copy(id = "running")
        )
        val messages = BotTimeline.messages(
            b, null, emptyList(), emptyList(), runs, emptyList(), emptyList(), testNow, testZone
        )
        assertEquals(listOf("runs-2026-10-15", "run-failed", "run-cancelled", "run-running"), messages.map { it.id })
        assertEquals("今天提炼了 2 次待办，最近一次 20:58 · Codex", messages[0].text)
        assertEquals(instant(latest.finishedAt!!), messages[0].at)
        assertEquals("提炼待办失败：额度用完 · Claude Code（第 2 次）", messages[1].text)
        assertTrue(messages[1].failed)
        assertTrue(messages[2].text.startsWith("提炼待办被取消了"))
        assertTrue(messages[3].text.startsWith("正在提炼待办"))
    }

    @Test
    fun failedExtractionsDoNotProduceASuccessSummary() {
        val messages = BotTimeline.messages(
            Bot.build(emptyList(), catalog).first(), null, emptyList(), emptyList(),
            listOf(testRun(status = "failed")), emptyList(), emptyList(), testNow, testZone
        )
        assertEquals(1, messages.size)
        assertTrue(messages.single().failed)
    }

    @Test
    fun extractionDayUsesLocalStartDateAndMissingFinishFallsBackToStart() {
        val runs = listOf(
            Run("today", "extract_todos", "codex", "2026-10-15T00:10:00+08:00", null, "succeeded"),
            Run("yesterday", "extract_todos", "claude_code", "2026-10-14T23:50:00+08:00", "2026-10-15T00:05:00+08:00", "succeeded")
        )
        val messages = BotTimeline.messages(
            Bot.build(emptyList(), catalog).first(), null, emptyList(), emptyList(),
            runs, emptyList(), emptyList(), testNow, testZone
        )
        assertEquals("今天提炼了 1 次待办，最近一次 00:10 · Codex", messages.single().text)
        assertEquals(instant(runs[0].startedAt), messages.single().at)
    }

    @Test
    fun digestSectionsKeepBlankLinesBeforeBoldHeadings() {
        val messages = BotTimeline.messages(
            bot(), null,
            listOf(Digest("d", "2026-10-15", "homework", "**新待办**\n- 交作业\n\n**通知**\n- 周一排球课", testNow.toString())),
            emptyList(), emptyList(), emptyList(), emptyList(), testNow, testZone
        )
        assertEquals("**新待办**\n- 交作业\n\n**通知**\n- 周一排球课", messages.single().text)
        assertEquals("一行\n两行", BotTimeline.spaced(listOf("一行", "两行")))
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
        assertFalse(messages.last().text.contains("Mac"))
        assertTrue(messages.last().failed)
    }

    @Test
    fun chatStartsWithTodayAndRevealsOneMessageDayPerTap() {
        val messages = listOf(
            "2026-10-24T08:00:00+08:00", "2026-10-25T08:00:00+08:00",
            "2026-10-27T23:50:00+08:00", "2026-10-28T00:10:00+08:00", "2026-10-28T09:00:00+08:00"
        ).mapIndexed { i, at -> BotMessage("$i", "bot", instant(at), "文字") }
        val now = instant("2026-10-28T12:00:00+08:00")
        val (opened, hidden) = BotTimeline.recent(messages, 0, now, testZone)
        assertEquals(listOf("3", "4"), opened.map { it.id })
        assertEquals(listOf("2026-10-27", "2026-10-25", "2026-10-24"), hidden.map { it.toString() })
        assertEquals("看昨天的消息（更早还有 2 天）", BotTimeline.earlierLabel(hidden, now, testZone))
        val (once, remaining) = BotTimeline.recent(messages, 1, now, testZone)
        assertEquals(listOf("2", "3", "4"), once.map { it.id })
        assertEquals("看10月25日的消息（更早还有 1 天）", BotTimeline.earlierLabel(remaining, now, testZone))
        val all = BotTimeline.recent(messages, 9, now, testZone)
        assertEquals(messages, all.first)
        assertTrue(all.second.isEmpty())
        assertNull(BotTimeline.earlierLabel(all.second, now, testZone))
        assertEquals(opened, BotTimeline.recent(messages, -1, now, testZone).first)
    }

    @Test
    fun noMessagesTodayDoesNotAutomaticallyRevealYesterday() {
        val messages = listOf(BotMessage("yesterday", "bot", instant("2026-10-28T23:59:00+08:00"), "旧消息"))
        val now = instant("2026-10-29T00:01:00+08:00")
        val (visible, hidden) = BotTimeline.recent(messages, 0, now, testZone)
        assertTrue(visible.isEmpty())
        assertEquals("看昨天的消息", BotTimeline.earlierLabel(hidden, now, testZone))
        assertEquals(messages, BotTimeline.recent(messages, 1, now, testZone).first)
        assertEquals(emptyList<BotMessage>() to emptyList<java.time.LocalDate>(), BotTimeline.recent(emptyList(), 0, now, testZone))
    }

    @Test
    fun recentUsesCalendarDaysAcrossDaylightSavingAndKeepsFutureMessages() {
        val zone = java.time.ZoneId.of("America/New_York")
        val now = instant("2026-11-01T12:00:00-05:00")
        val messages = listOf(
            BotMessage("yesterday", "bot", instant("2026-10-31T23:59:00-04:00"), "旧"),
            BotMessage("first-hour", "me", instant("2026-11-01T01:15:00-04:00"), "我"),
            BotMessage("second-hour", "bot", instant("2026-11-01T01:15:00-05:00"), "回复"),
            BotMessage("future", "bot", instant("2026-11-02T00:01:00-05:00"), "以后")
        )
        assertEquals(listOf("first-hour", "second-hour", "future"), BotTimeline.recent(messages, 0, now, zone).first.map { it.id })
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
