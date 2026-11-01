package local.assistant.desktop

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonPrimitive
import local.assistant.core.*
import org.junit.Assert.*
import org.junit.Test

class PresentationTest {
    @Test
    fun narrowWindowKeepsOneReadableColumn() {
        listOf(-30.0, 0.0, 150.0, 240.0, 491.9).forEach { assertEquals(1, DesktopLayout.columns(it)) }
    }

    @Test
    fun secondColumnIncludesTheGap() {
        assertEquals(2, DesktopLayout.columns(492.0))
        assertEquals(2, DesktopLayout.columns(743.9))
    }

    @Test
    fun thirdColumnStartsAtItsFullWidth() {
        assertEquals(3, DesktopLayout.columns(744.0))
        assertEquals(4, DesktopLayout.columns(996.0))
    }

    @Test
    fun invalidMeasurementsFallBackAndLargeWindowsKeepTheMacColumnRule() {
        assertEquals(1, DesktopLayout.columns(Double.NaN))
        assertEquals(1, DesktopLayout.columns(Double.POSITIVE_INFINITY))
        assertEquals(39, DesktopLayout.columns(10_000.0))
    }

    @Test
    fun trayCountsOnlyPendingTodayTodos() {
        val today = Today("2026-11-01", "", listOf(
            Todo("a", "待办"), Todo("b", "完成", status = "completed"), Todo("c", "删除", status = "deleted"),
        ))
        val text = TrayText.from(today, true, MascotState.WORKING)
        assertEquals(1, text.remaining)
        assertEquals("今日剩余 1 件待办", text.headline)
        assertTrue(text.tooltip.contains("正在运行"))
    }

    @Test
    fun trayExplainsOfflineStateWithoutLosingCachedCount() {
        val text = TrayText.from(Today("2026-11-01", "", listOf(Todo("a", "待办"))), false, MascotState.IDLE)
        assertEquals(1, text.remaining)
        assertTrue(text.tooltip.contains("连不上 Mac"))
    }

    @Test
    fun trayHasUsefulTextBeforeInitialRefresh() {
        assertEquals("今日剩余 0 件待办", TrayText.from(null, false, MascotState.OFFLINE).headline)
    }

    @Test
    fun idleRefreshesEveryThirtySeconds() {
        assertEquals(30_000L, RefreshPolicy.interval(emptyList(), emptyList()))
    }

    @Test
    fun runningJobRefreshesEveryTwoSeconds() {
        val run = Run("run", "extract_todos", startedAt = "2026-11-01T00:00:00Z", status = "running")
        assertEquals(2_000L, RefreshPolicy.interval(listOf(run), emptyList()))
    }

    @Test
    fun pendingReplyUsesFastRefreshEvenBeforeTheJobIsVisible() {
        val message = ChatMessage("reply", "assistant", "bot", "", "pending", createdAt = "2026-11-01T00:00:00Z", updatedAt = "2026-11-01T00:00:00Z")
        assertEquals(2_000L, RefreshPolicy.interval(emptyList(), listOf(listOf(message))))
        assertEquals(2_000L, RefreshPolicy.interval(emptyList(), emptyList(), sending = true))
    }

    @Test
    fun completedAndFailedJobsReturnToNormalRefresh() {
        val run = Run("run", "extract_todos", startedAt = "2026-11-01T00:00:00Z", status = "failed")
        assertEquals(30_000L, RefreshPolicy.interval(listOf(run), emptyList()))
    }

    @Test
    fun settingsHomeMatchesTheMacNavigationOrder() {
        assertEquals(listOf("连接", "插件", "消息来源", "运行时间表", "运行记录", "卡片风格", "贴图预览"), settingsLinks(true, true, emptyList()).map { it.title })
    }

    @Test
    fun settingsCountsEnabledPluginsAndSourcesSeparately() {
        val plugins = listOf(
            PluginView("qq_screen", "QQ", enabled = true, provides = listOf("source")),
            PluginView("health", "健康", enabled = true),
            PluginView("feishu", "飞书", provides = listOf("source")),
        )
        val links = settingsLinks(false, false, plugins).associateBy { it.id }
        assertEquals("2 个已启用", links.getValue("plugins").detail)
        assertEquals("2 个", links.getValue("sources").detail)
        assertEquals("连不上", links.getValue("connection").detail)
        assertEquals("模拟数据", settingsLinks(true, false, plugins).first().detail)
    }

    @Test
    fun unchangedScheduleSendsNoFields() {
        val original = RuntimeSettings()
        assertTrue(ScheduleDraft.from(original).patch(original).isEmpty())
    }

    @Test
    fun preferencePreservesTheOtherFallbackAgents() {
        val original = RuntimeSettings(harnessOrder = listOf("claude_code", "deepseek", "codex"))
        val patch = ScheduleDraft.from(original).copy(preferred = "codex").patch(original)
        assertEquals(listOf("codex", "claude_code", "deepseek"), patch["harness_order"].array().map { it.jsonPrimitive.content })
        assertEquals(setOf("harness_order"), patch.keys)
    }

    @Test
    fun invalidThresholdOrIncompleteCronCannotBeSaved() {
        val draft = ScheduleDraft.from(RuntimeSettings())
        assertFalse(draft.copy(threshold = Double.NaN).valid)
        assertFalse(draft.copy(threshold = 1.1).valid)
        assertFalse(draft.copy(extraction = "0 7").valid)
        assertFalse(draft.copy(preferred = "unknown").valid)
    }

    @Test
    fun scheduleEditsNeverOverwriteUnrelatedBackendSettings() {
        val original = RuntimeSettings(timezone = "UTC", agentTimeout = 600)
        val patch = ScheduleDraft.from(original).copy(source = "*/15 * * * *", threshold = 0.75).patch(original)
        assertEquals(setOf("source_cron", "confidence_threshold"), patch.keys)
        assertNotEquals(JsonNull, patch["confidence_threshold"])
    }

    @Test
    fun gridGroupsSimilarHeightsAndKeepsPriorityWithinEachGroup() {
        assertEquals(listOf(listOf(1, 3), listOf(0, 2)), DesktopLayout.rows(listOf(100.0, 300.0, 90.0, 250.0), 2))
    }

    @Test
    fun sizeGroupAlwaysComparesWithItsTallestCard() {
        assertEquals(listOf(listOf(0, 1), listOf(2)), DesktopLayout.rows(listOf(400.0, 310.0, 240.0), 3))
    }

    @Test
    fun incompleteGridRowsContainOnlyTheirCardsAndCanFillTheWidth() {
        assertEquals(listOf(listOf(0, 1), listOf(2)), DesktopLayout.rows(listOf(100.0, 100.0, 100.0), 2))
    }

    @Test
    fun emptyGridAndInvalidColumnCountRemainSafe() {
        assertTrue(DesktopLayout.rows(emptyList(), 3).isEmpty())
        assertEquals(listOf(listOf(0), listOf(1)), DesktopLayout.rows(listOf(100.0, 90.0), 0))
    }
}
