package local.assistant.core

import kotlinx.serialization.json.*
import java.time.*
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

object Names {
    private val plugins = mapOf(
        "qq_screen" to "QQ",
        "wechat_screen" to "微信",
        "feishu" to "飞书",
        "sms" to "短信",
        "mail" to "邮件",
        "photos" to "照片",
        "health" to "健康",
        "homework" to "作业",
        "campus" to "请假与学校活动",
        "grades" to "学分成绩",
        "weather" to "天气",
        "timetable" to "课表",
        "goodnotes" to "GoodNotes",
        "class_photos" to "课堂照片",
        "video_notes" to "视频",
        "finance" to "记账",
        "chat" to "对话"
    )

    fun plugin(id: String) = plugins[id] ?: id

    fun plugins(ids: String) = ids.split(',').filter { it.isNotEmpty() }.joinToString(" · ", transform =::plugin)

    fun harness(id: String?) = when (id) {
        null -> "—"
        "claude_code" -> "Claude Code"
        "codex" -> "Codex"
        "deepseek" -> "DeepSeek"
        else -> id
    }

    fun job(id: String): String {
        mapOf(
            "extract_todos" to "提炼待办",
            "apple_sync" to "同步提醒事项",
            "backup" to "备份",
            "memory_index" to "整理记忆索引",
            "daily_notes" to "整理每日文件夹",
            "knowledge_organize" to "整理知识库"
        )[id]?.let { return it }
        if (id.startsWith("fetch:")) return "读取${plugin(id.removePrefix("fetch:"))}"
        if (id.startsWith("chat:")) return when (val bot = id.removePrefix("chat:")) {
            "assistant" -> "回复对话"
            "knowledge" -> "回复对话 · 知识库"
            else -> "回复对话 · ${plugin(bot)}"
        }
        val p = id.split(':')
        if (p.size >= 3 && p[0] == "plugin") {
            val task = p.drop(2).joinToString(":")
            val word = mapOf(
                "morning_report" to "晨间简报",
                "weekly_report" to "周报",
                "mcp" to "工具调用",
                "mcp_tools" to "注册工具",
                "cards" to "卡片",
                "setup" to "启动",
                "limits" to "读取用量限额",
                "read" to "读取",
                "daily" to "整理每日文件夹",
                "forecast" to "读取天气预报",
                "brief" to "今日提醒"
            )[task] ?: task
            return "${plugin(p[1])} · $word"
        }
        return id
    }
}

fun format(at: Instant, pattern: String, zone: ZoneId = ZoneId.systemDefault()): String = DateTimeFormatter.ofPattern(
    pattern,
    Locale.CHINA
).withZone(zone).format(at)

object DueText {
    fun overdue(todo: Todo, now: Instant, zone: ZoneId): Boolean {
        val due = todo.dueAt?.let(::instant) ?: return false
        return if (todo.hasTime) due < now else due.atZone(zone).toLocalDate() < now.atZone(zone).toLocalDate()
    }

    fun describe(todo: Todo, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String {
        val due = todo.dueAt?.let(::instant) ?: return "无截止时间"
        val date = due.atZone(zone).toLocalDate()
        val today = now.atZone(zone).toLocalDate()
        var text = when (date) {
            today -> "今天"
            today.plusDays(1) -> "明天"
            else -> format(due, "M月d日", zone)
        }
        if (todo.hasTime) text += " " + format(due, "HH:mm", zone)
        if (todo.status == "pending" && overdue(todo, now, zone)) text = "已逾期 · $text"
        return text
    }
}

object RelativeTime {
    fun describe(at: Instant, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String {
        val s = Duration.between(at, now).seconds
        if (s < 60) return "刚刚"
        if (s < 3600) return "${s / 60} 分钟前"
        val d = at.atZone(zone).toLocalDate()
        val today = now.atZone(zone).toLocalDate()
        if (d == today) return "${s / 3600} 小时前"
        return if (d == today.minusDays(1)) "昨天 " + format(at, "HH:mm", zone) else format(at, "M月d日 HH:mm", zone)
    }

    fun duration(seconds: Double): String {
        val n = seconds.roundToInt().coerceAtLeast(0)
        return when {
            n < 60 -> "$n 秒"
            n < 3600 -> "${n / 60} 分 %02d 秒".format(n % 60)
            else -> "${n / 3600} 小时 ${(n % 3600) / 60} 分"
        }
    }
}

fun Todo.sourceText(): String {
    val first = sources?.firstOrNull() ?: return Names.plugins(sourcePlugin)
    val parts = mutableListOf(Names.plugin(first.plugin), first.chatName)
    if (first.sender.isNotEmpty() && first.sender != first.chatName) parts += first.sender
    val places = sources.map { it.plugin to it.chatName }.distinct().size
    return parts.joinToString(" · ") + if (places > 1) " 等 $places 处" else ""
}

fun Todo.syncText() = when {
    sourcePlugin == "watchdog" -> "只在 App 里，不进提醒事项"
    !syncError.isNullOrEmpty() -> "同步提醒事项失败"
    syncStatus == "synced" -> "已同步到提醒事项"
    else -> "等待同步到提醒事项"
}

object RunText {
    fun status(value: String) = mapOf(
        "running" to "运行中",
        "succeeded" to "成功",
        "failed" to "失败",
        "cancelled" to "已取消"
    )[value] ?: value

    private val errors = mapOf(
        "quota_exhausted" to "额度用完",
        "authentication_required" to "需要重新登录",
        "cli_unavailable" to "找不到 Agent 命令",
        "timeout" to "超时",
        "agent_failed" to "Agent 运行失败",
        "agent_incomplete" to "Agent 没有做完",
        "messages_remaining" to "还有消息没处理完",
        "all_harnesses_failed" to "两个 Agent 都失败了",
        "harness_exception" to "Agent 适配器出错",
        "agent_runner_unavailable" to "Agent 运行器不可用",
        "report_not_written" to "简报没有写出来",
        "apple_sync_failed" to "同步提醒事项失败",
        "apple_helper_failed" to "提醒事项工具出错",
        "screen_helper_failed" to "截图助手出错",
        "screen_history_gap" to "没接上上次读到的位置",
        "screen_chat_mismatch" to "打开的会话不对",
        "screen_not_conversation_list" to "没停在消息列表（点回「消息」）",
        "screen_recording_denied" to "没有屏幕录制权限",
        "screen_accessibility_denied" to "没有辅助功能权限",
        "screen_app_not_running" to "聊天软件没有打开",
        "screen_window_not_found" to "找不到主窗口（不要最小化）",
        "screen_capture_failed" to "截图失败",
        "screen_chat_not_in_list" to "会话列表里找不到这个会话",
        "screen_chat_not_open" to "没能打开这个会话",
        "feishu_oauth_failed" to "飞书授权失败",
        "feishu_history_failed" to "读取飞书消息失败",
        "plugin_setup_failed" to "插件启动失败",
        "plugin_tool_failed" to "插件工具出错",
        "plugin_cards_failed" to "插件卡片出错",
        "job_failed" to "任务失败",
        "interrupted" to "后端重启时中断",
        "internal_error" to "内部错误",
    )

    fun error(code: String?) = code?.takeIf { it.isNotEmpty() }?.let { errors[it] ?: it }

    fun duration(run: Run, now: Instant = Instant.now()) = RelativeTime.duration(
        Duration.between(instant(run.startedAt), run.finishedAt?.let(::instant) ?: now).toMillis() / 1000.0
    )

    fun last(run: Run?, now: Instant, zone: ZoneId): String = run?.let {
        "最近一次：${Names.job(it.job)} · ${status(it.status)} · ${RelativeTime.describe(instant(it.startedAt), now, zone)}" +
            (error(it.error)?.let { e -> " · $e" } ?: "")
    } ?: "还没有运行记录"
}

fun PluginView.ownRuns(runs: List<Run>) = runs.filter { it.job == "fetch:$id" || it.job.startsWith("plugin:$id:") }

fun knowledgeStatusLine(
    inputs: List<PluginView>,
    runs: List<Run>,
    now: Instant,
    zone: ZoneId
): String {
    runs.find { it.job == "knowledge_organize" && it.status == "running" }
        ?.let { return "正在整理知识库，已经 ${RunText.duration(it,now)}" }
    inputs.find { it.state == "error" }?.let {
        return Names.plugin(it.id) + it.statusLine(
            runs,
            emptyList(),
            now,
            zone
        )
    }
    return if (inputs.isEmpty())
        "整理知识库里的资料"
    else if (inputs.none { it.state == "enabled" })
        "资料来源都还没打开"
    else
        "资料来源：" + inputs.filter { it.state == "enabled" }.joinToString("、") { Names.plugin(it.id) }
}

fun PluginView.statusLine(
    runs: List<Run>,
    todos: List<Todo>,
    now: Instant,
    zone: ZoneId
): String {
    when (state) {
        "disabled" -> return "已停用"
        "unsupported" -> return "这台电脑上不能运行"
        "error" -> return "出错：${RunText.error(error) ?: "未知原因"}"
    }
    val last = ownRuns(runs).firstOrNull() ?: return "还没有运行过"
    if (last.status == "running") return "正在运行，已经 ${RunText.duration(last, now)}"
    val whenText = RelativeTime.describe(instant(last.finishedAt ?: last.startedAt), now, zone)
    if (last.status != "succeeded") return "上次运行在 $whenText，${RunText.error(last.error) ?: RunText.status(last.status)}"
    if ("source" !in provides) return "上次运行在 $whenText"
    val count = todos.count {
        it.status != "deleted" && instant(it.createdAt) >= instant(last.startedAt) && id in it.sourcePlugin.split(',')
    }
    return "上次运行在 $whenText，提取了 $count 条待办"
}

object CronText {
    fun valid(cron: String) = cron.trim().split(Regex("\\s+")).size == 5

    fun describe(cron: String): String {
        if (!valid(cron)) return "格式不对：需要 5 段（分 时 日 月 星期）"
        val p = cron.trim().split(Regex("\\s+"))
        val minute = p[0]
        val hour = p[1]
        if (p.drop(2).any { it != "*" }) return cron
        if (minute.startsWith("*/") && hour == "*" && minute.drop(2).toIntOrNull() != null)
            return "每 ${minute.drop(2)} 分钟"
        val m = minute.toIntOrNull() ?: return cron
        val mm = "%02d".format(m)
        hour.toIntOrNull()?.let { return "每天 $it:$mm" }
        if (hour == "*") return "每小时的第 $m 分"
        val parts = hour.split('/')
        if (parts.size == 2 && parts[1].toIntOrNull() != null) {
            if (parts[0] == "*") return "每 ${parts[1]} 小时"
            val range = parts[0].split('-').mapNotNull(String::toIntOrNull)
            if (range.size == 2) return "每天 ${range[0]}:$mm–${range[1]}:$mm，每 ${parts[1]} 小时"
        }
        return cron
    }
}

object PluginConfigEdit {
    val lists = listOf("whitelist" to "白名单会话", "course_chats" to "课程群")

    fun lists(plugin: PluginView) = lists.filter { plugin.configSchema["properties"].obj().containsKey(it.first) }

    fun names(plugin: PluginView, key: String) = plugin.config[key].array().mapNotNull { it.text() }

    fun adding(name: String, names: List<String>) = name.trim().takeIf { it.isNotEmpty() && it !in names }?.let {
        names + it
    } ?: names

    fun hasPreview(plugin: PluginView) = plugin.configSchema["properties"].obj().containsKey("preview_only")

    fun preview(plugin: PluginView) = plugin.config["preview_only"].flag() ?: true

    fun setting(plugin: PluginView, key: String, value: JsonElement) = JsonObject(plugin.config + (key to value))

    fun merging(before: JsonObject, latest: JsonObject, desired: JsonObject): JsonObject {
        val changes = desired.filter { (key, value) -> before[key] != value }
            .mapValues { (key, value) ->
                if (key in listOf("whitelist", "course_chats")) {
                    val removed = before[key].array() - value.array().toSet()
                    val added = value.array() - before[key].array().toSet()
                    JsonArray((latest[key].array().filterNot { it in removed } + added).distinct())
                } else value
            }
        return JsonObject(latest + changes)
    }
}

data class DigestDay(
    val date: String,
    val reads: List<Run>,
    val notes: List<String>,
    val todos: List<Todo>
)

fun digestDays(
    plugin: String,
    digests: List<Digest>,
    todos: List<Todo>,
    runs: List<Run>,
    zone: ZoneId
): List<DigestDay> = digests.filter { it.plugin == plugin }
    .groupBy { it.date }
    .toSortedMap(reverseOrder())
    .map { (day, written) ->
        DigestDay(
            day,
            runs.filter { it.job == "fetch:$plugin" && format(instant(it.startedAt), "yyyy-MM-dd", zone) == day }
                .sortedBy { instant(it.startedAt) },
            written.flatMap { it.summary.lines().map(String::trim).filter(String::isNotEmpty) },
            todos.filter {
                it.status != "deleted" && plugin in it.sourcePlugin.split(',') && format(
                    instant(it.createdAt),
                    "yyyy-MM-dd",
                    zone
                ) == day
            }
        )
    }
