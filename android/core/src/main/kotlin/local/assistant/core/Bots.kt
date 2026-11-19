package local.assistant.core

import java.time.*

data class StickerCatalog(
    val names: Set<String>,
    val roles: Map<String, PluginMascot>,
    val characters: Map<String, String>,
    val layered: Set<String>
) {
    fun resolved(name: String?) = name?.takeIf { it in names } ?: "placeholder"

    companion object {
        fun fromPaths(paths: Set<String>, read: (String) -> String?): StickerCatalog {
            val names = paths.filter { '/' !in it && it.endsWith(".png", ignoreCase = true) }.map {
                it.substringBeforeLast('.')
            }.toSet()
            val roles = runCatching {
                Wire.decodeFromString<Map<String, PluginMascot>>(read("app.json") ?: "{}")
            }.getOrDefault(emptyMap())
            val characters = runCatching {
                Wire.decodeFromString<Map<String, String>>(read("characters.json") ?: "{}")
            }.getOrDefault(emptyMap())
            return StickerCatalog(
                names,
                roles,
                characters,
                names.filter { name ->
                    listOf("base", "eyes_open", "eyes_closed").all { "$name/$it.png" in paths }
                }.toSet()
            )
        }
    }
}

fun parseAccent(hex: String?): Int? {
    val text = hex?.trim()?.removePrefix("#") ?: return null
    return text.takeIf { it.length == 6 && it.all { c -> c in '0'..'9' || c.lowercaseChar() in 'a'..'f' } }
        ?.toIntOrNull(16)
        ?.or(0xFF000000.toInt())
}

enum class BotKind {
    ASSISTANT,
    CHARACTER,
    KNOWLEDGE,
    SOURCE
}

data class Bot(
    val id: String,
    val name: String,
    val role: String,
    val kind: BotKind,
    val look: PluginMascot,
    val joined: List<String> = emptyList()
) {
    val builtIn get() = kind == BotKind.ASSISTANT || kind == BotKind.KNOWLEDGE
    val pluginIds get() = if (builtIn) joined else listOf(id) + joined

    val duties
        get() =
            if (joined.isNotEmpty() && !builtIn)
                pluginIds.map { dutyNames[it]?.first() ?: it }
            else
                dutyNames[id] ?: listOf(role)

    val tag get() = duties.joinToString(" · ")

    val intro
        get() =
            if (dutyNames[id] != null || joined.isNotEmpty() && !builtIn)
                duties.joinToString("与") { "“$it”" }
            else if (kind == BotKind.SOURCE) "消息来源" else role

    companion object {
        val dutyNames = mapOf(
            "assistant" to listOf("个人助理"),
            "knowledge" to listOf("知识库", "课堂笔记"),
            "campus" to listOf("请假", "学校活动"),
            "grades" to listOf("学分", "成绩"),
            "health" to listOf("健康", "睡眠"),
            "homework" to listOf("作业", "截止时间"),
            "timetable" to listOf("课表", "上课时间"),
            "token_usage" to listOf("Token 余额", "消耗"),
            "finance" to listOf("记账", "预算"),
            "qq_screen" to listOf("QQ 群", "私聊"),
            "wechat_screen" to listOf("微信群", "私聊"),
            "feishu" to listOf("飞书群", "单聊")
        )

        fun build(plugins: List<PluginView>, catalog: StickerCatalog): List<Bot> {
            fun role(
                id: String,
                fallback: String,
                description: String,
                kind: BotKind
            ): Bot {
                val look = catalog.roles[id] ?: PluginMascot()
                return Bot(
                    id,
                    catalog.characters[look.sticker] ?: fallback,
                    description,
                    kind,
                    look
                )
            }
            var assistant = role(
                "assistant",
                "助理Bot",
                "个人助理",
                BotKind.ASSISTANT
            )
            var knowledge = role(
                "knowledge",
                "知识库Bot",
                "知识库",
                BotKind.KNOWLEDGE
            )
            val characters = mutableListOf<Bot>()
            val sources = mutableListOf<Bot>()
            val ordered = plugins.filter { it.state in listOf("enabled", "error") } + plugins.filter {
                it.state !in listOf("enabled", "error")
            }
            ordered.forEach { plugin ->
                val look = plugin.mascot ?: PluginMascot()
                val bot = Bot(
                    plugin.id,
                    catalog.characters[look.sticker] ?: plugin.name,
                    plugin.name,
                    BotKind.CHARACTER,
                    look
                )
                val s = look.sticker
                if ("source" in plugin.provides) sources += bot.copy(name = plugin.name, kind = BotKind.SOURCE)
                else if (s != null && s == assistant.look.sticker) assistant = assistant.copy(
                    joined = assistant.joined + bot.id
                )
                else if (s != null && s == knowledge.look.sticker) knowledge = knowledge.copy(
                    joined = knowledge.joined + bot.id
                )
                else {
                    val i = characters.indexOfFirst { s != null && s in catalog.names && it.look.sticker == s }
                    if (i < 0) characters += bot else characters[i] = characters[i].copy(
                        joined = characters[i].joined + bot.id,
                        role = characters[i].role + " · " + bot.role
                    )
                }
            }
            return listOf(assistant) + characters + knowledge + sources
        }
    }
}

data class BotMessage(
    val id: String,
    val author: String,
    val at: Instant,
    val text: String,
    val title: String? = null,
    val todos: List<Todo> = emptyList(),
    val failed: Boolean = false
) {
    val preview get() = (title ?: text.lineSequence().firstOrNull().orEmpty()).replace("**", "")
}

suspend fun sendDraft(submitted: String, current: () -> String, clear: () -> Unit, send: suspend (String) -> Boolean) {
    if (send(submitted) && current() == submitted) clear()
}

object BotTimeline {
    // Rows already shown keep their place when two share a time. On a tie between copies of one
    // row the server's wins: hiding a bill's password changes the text, not the time.
    fun mergeChat(existing: List<ChatMessage>, incoming: List<ChatMessage>): List<ChatMessage> =
        (existing + incoming.asReversed()).groupBy { it.id }.values.map { versions ->
            versions.asReversed().maxWith(compareBy<ChatMessage> { instant(it.updatedAt) }.thenBy { if (it.status == "pending") 0 else 1 })
        }.sortedBy { instant(it.createdAt) }

    fun chat(lines: List<ChatMessage>) = lines.mapNotNull { line ->
        when {
            line.author == "me" -> BotMessage(
                line.id,
                "me",
                instant(line.createdAt),
                line.text
            )
            line.status == "pending" -> null
            line.status == "ready" -> BotMessage(
                line.id,
                "bot",
                instant(line.updatedAt),
                line.text
            )
            else -> BotMessage(
                line.id,
                "app",
                instant(line.updatedAt),
                when (line.error) {
                    "interrupted" -> "没回复完：后端重启了，再发一次吧。"
                    "all_harnesses_failed" -> "没能回复：Claude Code 和 Codex 都没完成，可能还没登录，或者额度用完了。"
                    else -> "没能回复，再发一次试试。"
                },
                failed = true
            )
        }
    }

    fun messages(
        bot: Bot,
        today: Today?,
        digests: List<Digest>,
        todos: List<Todo>,
        runs: List<Run>,
        reports: List<HealthReport>,
        chat: List<ChatMessage>,
        now: Instant,
        zone: ZoneId
    ): List<BotMessage> {
        val said = mutableListOf<BotMessage>()
        if (bot.kind == BotKind.ASSISTANT) {
            val extractions = runs.filter {
                it.job == "extract_todos" &&
                    instant(it.startedAt).atZone(zone).toLocalDate() == now.atZone(zone).toLocalDate()
            }
            extractions.filter { it.status != "succeeded" }.forEach {
                val agent = Names.harness(it.harness) + if (it.attempt > 1) "（第 ${it.attempt} 次）" else ""
                val text = when (it.status) {
                    "running" -> "正在提炼待办… · $agent"
                    "failed" -> "提炼待办失败：${RunText.error(it.error) ?: "未知原因"} · $agent"
                    else -> "提炼待办被取消了 · $agent"
                }
                said += BotMessage(
                    "run-${it.id}",
                    "bot",
                    instant(it.finishedAt ?: it.startedAt),
                    text,
                    failed = it.status == "failed"
                )
            }
            val done = extractions.filter { it.status == "succeeded" }
            done.sortedBy { instant(it.finishedAt ?: it.startedAt) }.lastOrNull()?.let { last ->
                val at = instant(last.finishedAt ?: last.startedAt)
                said += BotMessage(
                    "runs-${format(now, "yyyy-MM-dd", zone)}",
                    "bot",
                    at,
                    "今天提炼了 ${done.size} 次待办，最近一次 ${format(at, "HH:mm", zone)} · ${Names.harness(last.harness)}"
                )
            }
            if (today != null && today.summary.isNotEmpty()) said += BotMessage(
                "summary-${today.date}",
                "bot",
                digests.filter { it.date == today.date }.maxOfOrNull { instant(it.createdAt) } ?: now,
                today.summary,
                "今天的总结"
            )
        } else {
            digestDays(
                bot.id,
                digests,
                todos,
                runs,
                zone
            )
                .forEach { day ->
                    val at = digests.filter { it.plugin == bot.id && it.date == day.date }.maxOf {
                        instant(it.createdAt)
                    }
                    said += BotMessage(
                        "digest-${bot.id}-${day.date}",
                        "bot",
                        at,
                        spaced(day.notes),
                        todos = day.todos
                    )
                }
            if (bot.id == "health") reports.forEach {
                val kind = if (it.kind == "morning") "晨间简报" else "周报"
                said += BotMessage(
                    "report-${it.id}",
                    "bot",
                    instant(it.updatedAt),
                    when (it.status) {
                        "pending" -> "正在写…"
                        "failed" -> "没有生成：${RunText.error(it.error) ?: "未知原因"}"
                        else -> it.body.orEmpty()
                    },
                    it.title?.let { t -> "$kind · $t" } ?: kind,
                    failed = it.status == "failed"
                )
            }
        }
        return (said + chat(chat)).sortedBy { it.at }
    }

    fun unread(messages: List<BotMessage>, seen: Instant?, firstLaunch: Instant) = messages.count {
        it.author == "bot" && it.at > (seen ?: firstLaunch)
    }

    fun spaced(lines: List<String>) = lines.mapIndexed { index, line ->
        if (index > 0 && line.startsWith("**")) "\n$line" else line
    }.joinToString("\n")

    fun recent(messages: List<BotMessage>, earlier: Int, now: Instant, zone: ZoneId): Pair<List<BotMessage>, List<LocalDate>> {
        val today = now.atZone(zone).toLocalDate()
        val dates = messages.map { it.at.atZone(zone).toLocalDate() }.filter { it < today }.distinct().sortedDescending()
        val shown = dates.take(earlier.coerceAtLeast(0)).toSet()
        return messages.filter {
            val date = it.at.atZone(zone).toLocalDate()
            date >= today || date in shown
        } to dates.drop(earlier.coerceAtLeast(0))
    }

    fun earlierLabel(hidden: List<LocalDate>, now: Instant, zone: ZoneId): String? = hidden.firstOrNull()?.let {
        "看${day(it, now, zone)}的消息" + if (hidden.size > 1) "（更早还有 ${hidden.size - 1} 天）" else ""
    }

    fun day(at: Instant, now: Instant, zone: ZoneId): String = day(at.atZone(zone).toLocalDate(), now, zone)

    private fun day(date: LocalDate, now: Instant, zone: ZoneId): String = when (date) {
        now.atZone(zone).toLocalDate() -> "今天"
        now.atZone(zone).toLocalDate().minusDays(1) -> "昨天"
        else -> "${date.monthValue}月${date.dayOfMonth}日"
    }
}
