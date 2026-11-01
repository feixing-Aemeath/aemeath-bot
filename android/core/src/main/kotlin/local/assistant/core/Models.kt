package local.assistant.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import kotlinx.serialization.json.*
import java.time.Instant
import java.time.OffsetDateTime

val Wire = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
}

fun instant(value: String): Instant = OffsetDateTime.parse(value).toInstant()

fun JsonElement?.obj(): JsonObject = this as? JsonObject ?: JsonObject(emptyMap())

fun JsonElement?.array(): List<JsonElement> = (this as? JsonArray)?.toList() ?: emptyList()

fun JsonElement?.text(): String? = (this as? JsonPrimitive)?.takeIf { it.isString }?.content

fun JsonElement?.number(): Double? = (this as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull?.takeIf {
    it.isFinite()
}

fun JsonElement?.flag(): Boolean? = (this as? JsonPrimitive)?.booleanOrNull

fun jsonObject(vararg fields: Pair<String, JsonElement?>): JsonObject = JsonObject(
    fields.mapNotNull { (k, v) -> v?.let { k to it } }.toMap()
)

@Serializable
data class TodoSource(
    @SerialName("message_id") val messageId: String,
    val plugin: String,
    @SerialName("chat_name") val chatName: String,
    val sender: String,
    @SerialName("sent_at") val sentAt: String,
)

@Serializable
data class Todo(
    val id: String,
    val title: String,
    val notes: String = "",
    @SerialName("due_at") val dueAt: String? = null,
    @SerialName("has_time") val hasTime: Boolean = false,
    @SerialName("source_plugin") val sourcePlugin: String = "",
    @SerialName("source_refs") val sourceRefs: List<String> = emptyList(),
    @SerialName("source_excerpt") val sourceExcerpt: String = "",
    val confidence: Double = 1.0,
    val status: String = "pending",
    @SerialName("dedup_key") val dedupKey: String = "",
    @SerialName("reminder_id") val reminderId: String? = null,
    @SerialName("calendar_event_id") val calendarEventId: String? = null,
    @SerialName("created_by") val createdBy: String = "agent",
    @SerialName("created_at") val createdAt: String = Instant.EPOCH.toString(),
    @SerialName("updated_at") val updatedAt: String = createdAt,
    @SerialName("sync_status") val syncStatus: String = "synced",
    @SerialName("sync_error") val syncError: String? = null,
    val sources: List<TodoSource>? = null,
)

@Serializable
data class Page<T>(val items: List<T>, val total: Int)

@Serializable
data class Digest(
    val id: String,
    val date: String,
    val plugin: String,
    val summary: String,
    @SerialName("created_at") val createdAt: String
)

@Serializable
data class Run(
    val id: String,
    val job: String,
    val harness: String? = null,
    @SerialName("started_at") val startedAt: String,
    @SerialName("finished_at") val finishedAt: String? = null,
    val status: String,
    val error: String? = null,
    val attempt: Int = 1,
)

@Serializable
data class AgentModel(val id: String, val name: String, @SerialName("default") val isDefault: Boolean = false)

@Serializable
data class AgentStatus(
    val available: Boolean,
    val authentication: String = "unchecked",
    val models: List<AgentModel> = emptyList()
)

@Serializable
data class Status(
    val online: Boolean,
    val platform: String,
    @SerialName("apple_mode") val appleMode: String,
    val agents: Map<String, AgentStatus>,
    @SerialName("last_run") val lastRun: Run? = null,
    @SerialName("pending_sync") val pendingSync: Int = 0,
)

@Serializable
data class Today(
    val date: String,
    val summary: String,
    val todos: List<Todo>,
    val cards: List<Card> = emptyList()
)

@Serializable
data class PluginMascot(val sticker: String? = null, val accent: String = "#8C96A8")

@Serializable
data class PluginView(
    val id: String,
    val name: String,
    val version: String = "0.1.0",
    val icon: String = "",
    val description: String = "",
    val platforms: List<String> = emptyList(),
    val permissions: List<String> = emptyList(),
    val schedule: String? = null,
    val provides: List<String> = emptyList(),
    @SerialName("config_schema") val configSchema: JsonObject = JsonObject(emptyMap()),
    val mascot: PluginMascot? = null,
    val enabled: Boolean = false,
    val config: JsonObject = JsonObject(emptyMap()),
    @SerialName("last_run_at") val lastRunAt: String? = null,
    val state: String = "disabled",
    val error: String? = null,
)

@Serializable
data class RuntimeSettings(
    val timezone: String = "Asia/Shanghai",
    @SerialName("source_cron") val sourceCron: String = "*/30 * * * *",
    @SerialName("extraction_cron") val extractionCron: String = "0 7-23/3 * * *",
    @SerialName("confidence_threshold") val confidenceThreshold: Double = 0.8,
    @SerialName("harness_order") val harnessOrder: List<String> = listOf("claude_code", "codex"),
    @SerialName("job_harness") val jobHarness: Map<String, String> = emptyMap(),
    @SerialName("agent_timeout") val agentTimeout: Int = 300,
    @SerialName("retry_attempts") val retryAttempts: Int = 3,
    @SerialName("retry_base_seconds") val retryBaseSeconds: Double = 2.0,
)

@Serializable
data class HealthSample(
    val id: String,
    val type: String,
    val value: Double,
    val unit: String,
    @SerialName("start_at") val startAt: String,
    @SerialName("end_at") val endAt: String,
    val source: String,
)

@Serializable
data class HealthBatch(val samples: List<HealthSample>)

@Serializable
data class HealthAccepted(val accepted: Int, val duplicates: Int)

@Serializable
data class JobAccepted(@SerialName("job_id") val jobId: String, val status: String)

@Serializable
data class HealthReport(
    val id: String,
    val date: String,
    val kind: String,
    val status: String,
    val title: String? = null,
    val body: String? = null,
    val error: String? = null,
    val harness: String? = null,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class ChatMessage(
    val id: String,
    @SerialName("bot_id") val botId: String,
    val author: String,
    val text: String,
    val status: String,
    val error: String? = null,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class ChatAccepted(val message: ChatMessage, val reply: ChatMessage)

data class ChatChoice(val harness: String? = null, val model: String? = null) {
    val stored: String
        get() = harness?.let { it + (model?.let { m -> "/$m" } ?: "") } ?: ""

    fun body(text: String, botName: String? = null) = jsonObject(
        "text" to JsonPrimitive(text),
        "bot_name" to botName?.let(::JsonPrimitive),
        "harness" to harness?.let(::JsonPrimitive),
        "model" to model?.takeIf { harness != null }?.let(::JsonPrimitive),
    )

    companion object {
        fun restore(stored: String?): ChatChoice {
            val parts = stored.orEmpty().split('/', limit = 2).filter { it.isNotEmpty() }
            return ChatChoice(parts.firstOrNull(), parts.getOrNull(1))
        }
    }
}

data class ChatOption(val choice: ChatChoice, val agent: String, val title: String) {
    val short: String get() = if (choice.harness != null && choice.model == null) agent else title

    companion object {
        fun build(status: Status?): List<ChatOption> = buildList {
            add(ChatOption(ChatChoice(), "", "自动"))
            for (id in listOf("claude_code", "codex", "deepseek")) {
                val agent = status?.agents?.get(id)?.takeIf { it.available } ?: continue
                if (agent.models.isEmpty()) add(ChatOption(ChatChoice(id), Names.harness(id), "默认模型"))
                agent.models.forEach { add(ChatOption(ChatChoice(id, it.id), Names.harness(id), it.name)) }
            }
        }

        fun label(choice: ChatChoice, options: List<ChatOption>): String =
            options.firstOrNull { it.choice == choice }?.short
            ?: choice.model ?: choice.harness?.let(Names::harness) ?: "自动"
    }
}
