package local.assistant.desktop

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.encodeToJsonElement
import local.assistant.core.*
import java.time.Instant
import kotlin.math.floor

object DesktopLayout {
    const val sidebarWidth = 240
    const val minimumCardWidth = 240
    const val cardSpacing = 12

    fun columns(width: Double): Int {
        if (!width.isFinite()) return 1
        return floor((width.coerceAtLeast(0.0) + cardSpacing) / (minimumCardWidth + cardSpacing))
            .toInt().coerceAtLeast(1)
    }

    // Same size groups and stable within-group order as the Mac CardPacking implementation.
    fun rows(heights: List<Double>, columns: Int): List<List<Int>> {
        val ordered = heights.indices.sortedWith(compareByDescending<Int> { heights[it] }.thenBy { it })
        val groups = mutableListOf<MutableList<Int>>()
        for (index in ordered) {
            val tallest = groups.lastOrNull()?.firstOrNull()
            if (tallest != null && heights[index] >= heights[tallest] * 0.75) {
                groups.last().add(index)
            } else {
                groups.add(mutableListOf(index))
            }
        }
        return groups.flatMap { it.sorted().chunked(columns.coerceAtLeast(1)) }
    }
}

data class TrayText(val remaining: Int, val headline: String, val tooltip: String) {
    companion object {
        fun from(today: Today?, online: Boolean, state: MascotState): TrayText {
            val remaining = today?.todos.orEmpty().count { it.status == "pending" }
            val headline = "今日剩余 $remaining 件待办"
            val status = if (online) state.headline else "连不上 Mac"
            return TrayText(remaining, headline, "Aemeath Bot · $headline · $status")
        }
    }
}

object RefreshPolicy {
    fun interval(runs: List<Run>, chats: Collection<List<ChatMessage>>, sending: Boolean = false): Long =
        if (sending || runs.any { it.status == "running" } || chats.any { rows -> rows.any { it.status == "pending" } }) {
            2_000L
        } else {
            30_000L
        }
}

data class SettingsLink(val id: String, val title: String, val detail: String = "")

fun settingsLinks(mock: Boolean, online: Boolean, plugins: List<PluginView>): List<SettingsLink> = listOf(
    SettingsLink("connection", "连接", if (mock) "模拟数据" else if (online) "Mac 在线" else "连不上"),
    SettingsLink("plugins", "插件", "${plugins.count { it.enabled }} 个已启用"),
    SettingsLink("sources", "消息来源", "${plugins.count { "source" in it.provides }} 个"),
    SettingsLink("schedule", "运行时间表"),
    SettingsLink("runs", "运行记录"),
    SettingsLink("style", "卡片风格"),
    SettingsLink("stickers", "贴图预览"),
)

data class ScheduleDraft(
    val source: String,
    val extraction: String,
    val threshold: Double,
    val preferred: String,
) {
    val valid: Boolean
        get() = CronText.valid(source) && CronText.valid(extraction) &&
            threshold.isFinite() && threshold in 0.0..1.0 && preferred in listOf("claude_code", "codex", "deepseek")

    fun patch(original: RuntimeSettings): JsonObject = buildJsonObject {
        require(valid)
        if (source != original.sourceCron) put("source_cron", source)
        if (extraction != original.extractionCron) put("extraction_cron", extraction)
        if (kotlin.math.abs(threshold - original.confidenceThreshold) > 0.0001) {
            put("confidence_threshold", threshold)
        }
        if (preferred != original.harnessOrder.firstOrNull()) {
            put("harness_order", Wire.encodeToJsonElement(listOf(preferred) + original.harnessOrder.filterNot { it == preferred }))
        }
    }

    companion object {
        fun from(settings: RuntimeSettings) = ScheduleDraft(
            settings.sourceCron,
            settings.extractionCron,
            settings.confidenceThreshold,
            settings.harnessOrder.firstOrNull() ?: "claude_code",
        )
    }
}

fun firstLaunchInstant(settings: DeviceSettings): Instant = Instant.ofEpochMilli(settings.firstLaunch)
