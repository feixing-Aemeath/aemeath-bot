package local.assistant.core

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class PresentationTest {
    @Test
    fun dueDatesDistinguishDateOnlyAndTimedDeadlines() {
        val t = testTodo().copy(dueAt = "2026-10-15T00:00:00+08:00")
        assertEquals("今天", DueText.describe(t, testNow, testZone))
        assertFalse(DueText.overdue(t, testNow, testZone))
        assertEquals("已逾期 · 今天 00:00", DueText.describe(t.copy(hasTime = true), testNow, testZone))
        assertEquals(
            "明天 17:00",
            DueText.describe(t.copy(dueAt = "2026-10-16T17:00:00+08:00", hasTime = true), testNow, testZone)
        )
        assertEquals("今天 00:00", DueText.describe(t.copy(hasTime = true, status = "completed"), testNow, testZone))
        assertEquals("无截止时间", DueText.describe(testTodo(), testNow, testZone))
    }

    @Test
    fun knowledgeStatusMatchesInputsAndOrganizing() {
        val p = PluginView(
            "goodnotes",
            "笔记",
            state = "enabled",
            enabled = true
        )
        assertEquals(
            "资料来源：GoodNotes",
            knowledgeStatusLine(
                listOf(p),
                emptyList(),
                testNow,
                testZone
            )
        )
        assertEquals(
            "资料来源都还没打开",
            knowledgeStatusLine(
                listOf(p.copy(state = "disabled")),
                emptyList(),
                testNow,
                testZone
            )
        )
        assertEquals(
            "整理知识库里的资料",
            knowledgeStatusLine(
                emptyList(),
                emptyList(),
                testNow,
                testZone
            )
        )
        assertEquals(
            "正在整理知识库，已经 3 分 00 秒",
            knowledgeStatusLine(
                listOf(p),
                listOf(
                    testRun(
                        "knowledge_organize",
                        "running",
                        180,
                        seconds = null
                    )
                ),
                testNow,
                testZone
            )
        )
    }

    private fun plugin(id: String = "qq_screen", state: String = "enabled") = PluginView(
        id,
        id,
        provides = listOf("source"),
        enabled = state == "enabled",
        state = state
    )

    @Test
    fun namesMatchThePhone() {
        assertEquals("QQ · 飞书", Names.plugins("qq_screen,feishu"))
        assertEquals("future", Names.plugin("future"))
        assertEquals("Claude Code", Names.harness("claude_code"))
        assertEquals("—", Names.harness(null))
        mapOf(
            "fetch:qq_screen" to "读取QQ",
            "plugin:health:morning_report" to "健康 · 晨间简报",
            "chat:knowledge" to "回复对话 · 知识库",
            "knowledge_organize" to "整理知识库",
            "daily_notes" to "整理每日文件夹",
            "unknown" to "unknown"
        )
            .forEach { (id, text) -> assertEquals(text, Names.job(id)) }
    }

    @Test
    fun relativeTimesUseLocalDays() {
        assertEquals("刚刚", RelativeTime.describe(testNow.minusSeconds(20), testNow, testZone))
        assertEquals("10 分钟前", RelativeTime.describe(testNow.minusSeconds(600), testNow, testZone))
        assertEquals("2 小时前", RelativeTime.describe(instant("2026-10-15T18:30:00+08:00"), testNow, testZone))
        assertEquals("昨天 21:05", RelativeTime.describe(instant("2026-10-14T21:05:00+08:00"), testNow, testZone))
        assertEquals("10月12日 08:30", RelativeTime.describe(instant("2026-10-12T08:30:00+08:00"), testNow, testZone))
    }

    @Test
    fun durationsAndRunningTime() {
        assertEquals("45 秒", RelativeTime.duration(45.0))
        assertEquals("2 分 05 秒", RelativeTime.duration(125.0))
        assertEquals("1 小时 3 分", RelativeTime.duration(3780.0))
        assertEquals("2 分 00 秒", RunText.duration(testRun(status = "running", ago = 120, seconds = null), testNow))
        assertEquals("0 秒", RelativeTime.duration(-5.0))
    }

    @Test
    fun sourcesIncludeChatSenderAndDistinctPlaces() {
        assertEquals("QQ · 软件工程2班 · 李老师", fromFixture<Todo>("todoWithSources").sourceText())
        assertEquals("QQ · 飞书", fromFixture<Todo>("todoWithoutDue").sourceText())
        val source = TodoSource(
            "a",
            "qq_screen",
            "班级群",
            "老师",
            testNow.toString()
        )
        assertEquals(
            "QQ · 班级群 · 老师 等 2 处",
            testTodo()
                .copy(
                    sources = listOf(
                        source,
                        source.copy(messageId = "b"),
                        source.copy(plugin = "feishu", chatName = "实验室")
                    )
                )
                .sourceText()
        )
        assertEquals(
            "QQ · 室友",
            testTodo().copy(sources = listOf(source.copy(chatName = "室友", sender = "室友"))).sourceText()
        )
    }

    @Test
    fun reminderSyncStates() {
        val t = testTodo().copy(syncStatus = "pending")
        assertEquals("等待同步到提醒事项", t.syncText())
        assertEquals("已同步到提醒事项", t.copy(syncStatus = "synced").syncText())
        assertEquals("同步提醒事项失败", t.copy(syncError = "apple_helper_failed").syncText())
        assertEquals("只在 App 里，不进提醒事项", t.copy(sourcePlugin = "watchdog", syncError = "x").syncText())
    }

    @Test
    fun errorsAreReadableAndUnknownOnesSurvive() {
        assertEquals("额度用完", RunText.error("quota_exhausted"))
        assertEquals("未知码", RunText.error("未知码"))
        assertNull(RunText.error(null))
        assertEquals("失败", RunText.status("failed"))
        assertEquals("还没有运行记录", RunText.last(null, testNow, testZone))
        assertEquals(
            "最近一次：提炼待办 · 失败 · 3 分钟前 · 额度用完",
            RunText.last(testRun(status = "failed", ago = 180, error = "quota_exhausted"), testNow, testZone)
        )
    }

    @Test
    fun sourceStatusCountsMergedTodosOnceAndExcludesDeletedAndEarlier() {
        val run = testRun("fetch:qq_screen", ago = 600)
        val items = listOf(
            testTodo(),
            testTodo("2", "feishu,qq_screen"),
            testTodo("3").copy(status = "deleted"),
            testTodo("4", created = testNow.minusSeconds(3600).toString()),
            testTodo("5", "feishu")
        )
        assertEquals(
            "上次运行在 9 分钟前，提取了 2 条待办",
            plugin().statusLine(
                listOf(run),
                items,
                testNow,
                testZone
            )
        )
        assertEquals(
            "还没有运行过",
            plugin("wechat_screen").statusLine(
                listOf(run),
                items,
                testNow,
                testZone
            )
        )
    }

    @Test
    fun pluginDisabledUnsupportedErrorsAndBusy() {
        assertEquals(
            "已停用",
            plugin(state = "disabled").statusLine(
                emptyList(),
                emptyList(),
                testNow,
                testZone
            )
        )
        assertEquals(
            "这台电脑上不能运行",
            plugin(state = "unsupported").statusLine(
                emptyList(),
                emptyList(),
                testNow,
                testZone
            )
        )
        assertEquals(
            "出错：插件启动失败",
            plugin(state = "error").copy(error = "plugin_setup_failed").statusLine(
                emptyList(),
                emptyList(),
                testNow,
                testZone
            )
        )
        assertEquals(
            "正在运行，已经 1 分 45 秒",
            plugin("health")
                .statusLine(
                    listOf(
                        testRun(
                            "plugin:health:morning_report",
                            "running",
                            105,
                            seconds = null
                        )
                    ),
                    emptyList(),
                    testNow,
                    testZone
                )
        )
    }

    @Test
    fun cardsPluginHasNoExtractedTodoCount() {
        assertEquals(
            "上次运行在 9 分钟前",
            plugin("grades")
                .copy(provides = listOf("cards"))
                .statusLine(
                    listOf(testRun("plugin:grades:sync")),
                    emptyList(),
                    testNow,
                    testZone
                )
        )
    }

    @Test
    fun cronTextMatchesPhoneAndKeepsComplexExpressions() {
        mapOf(
            "*/30 * * * *" to "每 30 分钟",
            "0 7-23/3 * * *" to "每天 7:00–23:00，每 3 小时",
            "15 8 * * *" to "每天 8:15",
            "5 * * * *" to "每小时的第 5 分",
            "0 */3 * * *" to "每 3 小时",
            "0 21 * * sun" to "0 21 * * sun"
        )
            .forEach { (cron, text) -> assertEquals(text, CronText.describe(cron)) }
        assertFalse(CronText.valid("0 7 * *"))
        assertTrue(CronText.describe("0 7").startsWith("格式不对"))
    }

    @Test
    fun pluginEditsPreserveOpaqueConfiguration() {
        val p = plugin()
            .copy(
                configSchema = Wire.parseToJsonElement("""{"properties":{"whitelist":{},"preview_only":{}}}""").obj(),
                config = Wire.parseToJsonElement("""{"helper":"/x/screen-helper","whitelist":["班级群"]}""").obj()
            )
        assertEquals(listOf("whitelist"), PluginConfigEdit.lists(p).map { it.first })
        assertTrue(PluginConfigEdit.hasPreview(p))
        assertTrue(PluginConfigEdit.preview(p))
        val names = PluginConfigEdit.adding("  软件工程2班 ", PluginConfigEdit.names(p, "whitelist"))
        assertEquals(listOf("班级群", "软件工程2班"), names)
        assertEquals(names, PluginConfigEdit.adding("班级群", names))
        assertEquals(names, PluginConfigEdit.adding("  ", names))
        assertEquals(
            "/x/screen-helper",
            PluginConfigEdit.setting(p, "whitelist", Wire.encodeToJsonElement(names))["helper"].text()
        )
    }

    @Test
    fun queuedPluginEditsKeepOtherChangesAndListAdditions() {
        fun config(text: String) = Wire.parseToJsonElement(text).obj()
        val before = config("""{"helper":"/x","whitelist":["一班"],"preview_only":true}""")
        val latest = config("""{"helper":"/x","whitelist":["一班","二班"],"preview_only":false}""")
        val next = PluginConfigEdit.merging(
            before,
            latest,
            config("""{"helper":"/x","whitelist":["一班","三班"],"preview_only":true}""")
        )
        assertEquals(listOf("一班", "二班", "三班"), next["whitelist"].array().map { it.text() })
        assertEquals(false, next["preview_only"].flag())
        assertEquals("/x", next["helper"].text())
    }

    @Test
    fun digestsGroupByPluginAndBackendDay() {
        val ds = listOf(
            Digest(
                "1",
                "2026-10-15",
                "qq_screen",
                "交表\n\n停水通知",
                testNow.toString()
            ),
            Digest(
                "2",
                "2026-10-14",
                "qq_screen",
                "讲座",
                testNow.toString()
            ),
            Digest(
                "3",
                "2026-10-15",
                "feishu",
                "材料",
                testNow.toString()
            )
        )
        val runs = listOf(testRun("fetch:qq_screen", ago = 3600), testRun("fetch:qq_screen", "failed", 7200))
        val days = digestDays(
            "qq_screen",
            ds,
            listOf(testTodo(), testTodo("old", created = "2026-10-14T23:40:00+08:00")),
            runs,
            testZone
        )
        assertEquals(listOf("2026-10-15", "2026-10-14"), days.map { it.date })
        assertEquals(listOf("交表", "停水通知"), days[0].notes)
        assertEquals(listOf("failed", "succeeded"), days[0].reads.map { it.status })
        assertEquals("old", days[1].todos.single().id)
    }
}
