package local.assistant.core

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import java.time.*
import java.time.temporal.ChronoUnit

/** The exact iPhone MockBackend scenario exported by scripts/ExportFixtures.swift. */
class MockBackend(
    private val clock: () -> Instant = { Instant.now() },
    private val zone: ZoneId = ZoneId.systemDefault()
) : Backend() {
    private val mutex = Mutex()
    private val delta = ChronoUnit.DAYS.between(LocalDate.of(2026, 10, 31), clock().atZone(zone).toLocalDate())

    private fun shift(e: JsonElement): JsonElement = when (e) {
        is JsonObject -> JsonObject(e.mapValues { shift(it.value) })
        is JsonArray -> JsonArray(e.map(::shift))
        is JsonPrimitive -> if (e.isString) {
            val text = e.content
            when {
                Regex("\\d{4}-\\d{2}-\\d{2}T.*").matches(text) -> runCatching {
                    JsonPrimitive(instant(text).atZone(zone).plusDays(delta).toInstant().toString())
                }.getOrDefault(e)
                Regex("\\d{4}-\\d{2}-\\d{2}").matches(text) -> JsonPrimitive(
                    LocalDate.parse(text).plusDays(delta).toString()
                )
                else -> e
            }
        } else e
        else -> e
    }

    private inline fun <reified T> load(name: String): T {
        val text = checkNotNull(javaClass.getResourceAsStream("/mock/$name.json")).bufferedReader().use {
            it.readText()
        }
        return Wire.decodeFromJsonElement(shift(Wire.parseToJsonElement(text)))
    }

    private var todoItems = load<List<Todo>>("todos")
    private var digestItems = load<List<Digest>>("digests")
    private var runItems = load<List<Run>>("runs")
    private var pluginItems = load<List<PluginView>>("plugins")
        .map { p ->
            val keys =
                if (p.id in listOf("qq_screen", "wechat_screen", "feishu"))
                    listOf("whitelist") + if (p.id == "feishu") emptyList() else listOf("preview_only")
                else if (p.id == "homework") listOf("course_chats") else emptyList()
            p.copy(
                configSchema = buildJsonObject {
                    put("properties", JsonObject(keys.associateWith { JsonObject(emptyMap()) }))
                }
            )
        }
    private var runtime = load<RuntimeSettings>("settings")
    private var library = load<KnowledgeOverview>("knowledge")
    private var contents = load<Map<String, KnowledgeFile>>("knowledge-contents").toMutableMap()
    private val reportItems = load<List<HealthReport>>("reports")
    private val chats = mutableMapOf<String, List<ChatMessage>>()
    private val uploaded = mutableSetOf<String>()
    private var nextId = 100
    var online = true

    private fun tick() {
        val now = clock()
        runItems = runItems.map {
            if (it.status == "running" && now >= instant(it.startedAt).plusSeconds(4)) it.copy(
                status = "succeeded",
                finishedAt = instant(it.startedAt).plusSeconds(4).toString()
            ) else it
        }
        chats.replaceAll { _, rows ->
            rows.map {
                if (it.status == "pending" && now >= instant(it.createdAt).plusSeconds(2)) it.copy(
                    status = "ready",
                    text = "（模拟数据里的示例回复）连上 Mac 上的后端以后，我会查你的待办和记录来回答，也能帮你记待办。",
                    updatedAt = instant(it.createdAt).plusSeconds(2).toString()
                ) else it
            }
        }
    }

    private inline fun <reified T> bytes(value: T) = Wire.encodeToString(value).toByteArray()

    override suspend fun raw(
        method: String,
        path: List<String>,
        query: Map<String, String>,
        body: ByteArray?,
        contentType: String
    ): ByteArray = mutex.withLock {
        if (!online) throw APIError("transport")
        tick()
        val p = body?.takeIf { contentType == "application/json" && it.isNotEmpty() }
            ?.decodeToString()
            ?.let { Wire.parseToJsonElement(it).obj() } ?: JsonObject(emptyMap())
        val now = clock().toString()
        fun page(items: List<JsonElement>): ByteArray {
            val offset = query["offset"]?.toIntOrNull() ?: 0
            val limit = query["limit"]?.toIntOrNull() ?: 200
            return jsonObject(
                "items" to JsonArray(items.drop(offset).take(limit)),
                "total" to JsonPrimitive(items.size)
            )
                .toString()
                .toByteArray()
        }
        when {
            path == listOf("status") -> bytes(
                load<Status>("status").copy(lastRun = runItems.maxByOrNull { instant(it.startedAt) })
            )
            path == listOf("today") -> bytes(
                Today(
                    clock().atZone(zone).toLocalDate().toString(),
                    load<Today>("today").summary,
                    todoItems.filter {
                        it.status == "pending" && it.dueAt?.let { d ->
                            instant(d).atZone(zone).toLocalDate() <= clock().atZone(zone).toLocalDate()
                        } == true
                    },
                    pluginItems.filter { it.enabled }.flatMap { cardsFor(it.id) }
                )
            )
            path == listOf("todos") -> page(
                todoItems.filter {
                    if (query["status"] == null) it.status != "deleted" else it.status == query["status"]
                }
                    .map { Wire.encodeToJsonElement(it) }
            )
            path.firstOrNull() == "todos" && method == "PATCH" -> {
                val old = todoItems.find { it.id == path[1] } ?: throw APIError("not_found")
                if (old.status == "deleted" && p["status"].text() != "deleted") throw APIError("conflict")
                var updated = Wire.decodeFromJsonElement<Todo>(JsonObject(Wire.encodeToJsonElement(old).obj() + p))
                if (p["due_at"] == JsonNull) updated = updated.copy(hasTime = false)
                if (updated.hasTime && updated.dueAt == null || updated.title.isBlank()) throw APIError(
                    "invalid_request"
                )
                updated = updated.copy(
                    updatedAt = now,
                    syncStatus = "synced",
                    reminderId = if (updated.status == "deleted") null else updated.reminderId,
                    calendarEventId = if (updated.status == "deleted") null else updated.calendarEventId
                )
                todoItems = todoItems.map { if (it.id == updated.id) updated else it }
                bytes(updated)
            }
            path == listOf("digests") -> page(
                digestItems.sortedByDescending { it.date }.map { Wire.encodeToJsonElement(it) }
            )
            path == listOf("runs") -> page(
                runItems.sortedByDescending { instant(it.startedAt) }.map { Wire.encodeToJsonElement(it) }
            )
            path.firstOrNull() == "jobs" -> {
                val id = path[1]
                val known = listOf(
                    "extract_todos",
                    "apple_sync",
                    "backup",
                    "memory_index",
                    "knowledge_organize",
                    "plugin:health:refresh"
                ) + pluginItems.filter { it.state == "enabled" }.map { "fetch:${it.id}" }
                if (id !in known) throw APIError("not_found")
                if (runItems.any { it.job == id && it.status == "running" }) throw APIError("conflict")
                runItems = runItems + Run(
                    "mock-${++nextId}",
                    id,
                    if (id == "extract_todos") runtime.harnessOrder.firstOrNull() else null,
                    now,
                    status = "running"
                )
                bytes(JobAccepted(id, "accepted"))
            }
            path == listOf("plugins") -> bytes(pluginItems)
            path.firstOrNull() == "plugins" -> {
                val old = pluginItems.find { it.id == path[1] } ?: throw APIError("not_found")
                when {
                    method == "DELETE" -> {
                        pluginItems = pluginItems.filterNot { it.id == old.id }
                        byteArrayOf()
                    }
                    method == "PATCH" -> {
                        if (old.state == "unsupported" && p["enabled"].flag() == true) throw APIError("conflict")
                        val updated = old.copy(
                            enabled = p["enabled"].flag() ?: old.enabled,
                            state = p["enabled"].flag()?.let { if (it) "enabled" else "disabled" } ?: old.state,
                            config = p["config"] as? JsonObject ?: old.config
                        )
                        pluginItems = pluginItems.map { if (it.id == old.id) updated else it }
                        bytes(updated)
                    }
                    else -> bytes(if (old.state == "enabled") cardsFor(old.id) else emptyList())
                }
            }
            path == listOf("settings") -> {
                if (method == "PATCH") {
                    if (
                        listOf("source_cron", "extraction_cron").any {
                            p[it].text()?.let { v -> !CronText.valid(v) } == true
                        }
                    ) throw APIError("invalid_request")
                    runtime = Wire.decodeFromJsonElement(JsonObject(Wire.encodeToJsonElement(runtime).obj() + p))
                }
                bytes(runtime)
            }
            path == listOf("health", "reports") -> page(reportItems.map { Wire.encodeToJsonElement(it) })
            path == listOf("health", "samples") -> {
                val batch = Wire.decodeFromJsonElement<HealthBatch>(p)
                val count = batch.samples.count { uploaded.add(it.id) }
                bytes(HealthAccepted(count, batch.samples.size - count))
            }
            path.firstOrNull() == "bots" -> {
                val id = path[1]
                if (
                    id !in listOf("assistant", "knowledge") && pluginItems.none {
                        it.id == id && it.state == "enabled" && "source" !in it.provides
                    }
                ) throw APIError("not_found")
                if (method == "POST") {
                    if (chats[id].orEmpty().any { it.status == "pending" }) throw APIError("conflict")
                    val text = p["text"].text()?.trim()?.takeIf { it.isNotEmpty() } ?: throw APIError("invalid_request")
                    if (text.codePointCount(0, text.length) > 2000) throw APIError("invalid_request")
                    val me = ChatMessage(
                        "mock-chat-${++nextId}",
                        id,
                        "me",
                        text,
                        "ready",
                        createdAt = now,
                        updatedAt = now
                    )
                    val reply = ChatMessage(
                        "mock-chat-${++nextId}",
                        id,
                        "bot",
                        "",
                        "pending",
                        createdAt = now,
                        updatedAt = now
                    )
                    chats[id] = chats[id].orEmpty() + me + reply
                    bytes(ChatAccepted(me, reply))
                } else page(chats[id].orEmpty().asReversed().map { Wire.encodeToJsonElement(it) })
            }
            path.firstOrNull() == "knowledge" -> knowledgeRoute(
                method,
                path,
                query,
                p,
                body,
                contentType
            )
            else -> throw APIError("not_found")
        }
    }

    private fun cardsFor(id: String): List<Card> = runCatching { load<List<Card>>("cards-$id") }.getOrDefault(
        emptyList()
    )

    private fun overview(): KnowledgeOverview = library.copy(
        areas = library.areas.map { a ->
            val own = contents.values.filter { it.area == a.path }
            a.copy(
                files = own.size,
                notes = own.count { it.kind == "note" },
                sources = own.count { it.kind == "source" },
                pending = own.count { it.status == "pending" }
            )
        }
    )

    private fun validateReaders(names: List<String>) {
        if (names.any { n -> library.readers.none { it.id == n } }) throw APIError("invalid_request")
    }

    private fun knowledgeRoute(
        method: String,
        path: List<String>,
        query: Map<String, String>,
        patch: JsonObject,
        body: ByteArray?,
        type: String
    ): ByteArray {
        fun names(key: String) = patch[key].array().mapNotNull { it.text() }
        val requested = query["path"].orEmpty()
        when (path.getOrNull(1)) {
            null -> {
                if (method == "PATCH") {
                    if (patch.containsKey("agents")) {
                        val agents = names("agents")
                        val valid = library.readers.filter { it.kind == "bot" }.map { it.id } + agents
                        library = library.copy(
                            readers = library.readers.filter { it.kind == "bot" } + agents.map {
                                KnowledgeReader(it, "agent")
                            },
                            areas = library.areas.map { it.copy(readers = it.readers.filter { r -> r in valid }) },
                            defaultReaders = library.defaultReaders.filter { it in valid }
                        )
                    }
                    if (patch.containsKey("default_readers")) {
                        validateReaders(names("default_readers"))
                        library = library.copy(defaultReaders = names("default_readers"))
                    }
                }
                return bytes(overview())
            }
            "access" -> {
                val area = library.areas.find { it.path == patch["path"].text() } ?: throw APIError("not_found")
                validateReaders(names("readers"))
                val updated = area.copy(
                    readers = library.ordered((names("readers") + area.locked).distinct()).map { it.id }
                )
                library = library.copy(areas = library.areas.map { if (it.path == area.path) updated else it })
                return bytes(updated)
            }
            "topics" -> {
                val name = patch["name"].text()?.trim().orEmpty()
                if (!Knowledge.validTopic(name)) throw APIError("invalid_request")
                if (library.topics.any { it.name == name }) throw APIError("conflict")
                val readers = if (patch.containsKey("readers")) names("readers") else library.defaultReaders
                validateReaders(readers)
                val area = KnowledgeArea("知识库/$name", name, readers = readers)
                library = library.copy(areas = library.areas + area)
                return bytes(area)
            }
            "files" -> {
                if (method == "POST") {
                    if (contents.containsKey(requested)) throw APIError("conflict")
                    val area = library.topics.find { requested.startsWith(it.path + "/") } ?: throw APIError(
                        "invalid_request"
                    )
                    val name = Knowledge.fileName(requested)
                    if (
                        requested.removePrefix(area.path + "/").contains('/') || name.startsWith('.') || name.contains(
                            '\\'
                        ) || body == null || body.size > 50 * 1024 * 1024
                    ) throw APIError("invalid_request")
                    val format = when {
                        type == "application/pdf" -> "pdf"
                        type.startsWith("image/") -> "image"
                        type.contains("wordprocessingml") -> "docx"
                        else -> name.substringAfterLast('.', "other")
                    }
                    val status = if (area.readers.isEmpty()) "skipped" else "pending"
                    val file = KnowledgeFile(
                        requested,
                        name,
                        name.substringBeforeLast('.'),
                        "source",
                        format,
                        body.size.toLong(),
                        clock().toString(),
                        area.path,
                        area.readers,
                        if (format in listOf("md", "txt")) body.decodeToString() else "",
                        status = status
                    )
                    contents[requested] = file
                    return bytes(entry(file))
                }
                val area = library.areas.find { it.path == requested } ?: throw APIError("not_found")
                return bytes(
                    KnowledgeFiles(
                        requested,
                        area.path,
                        area.readers,
                        contents.values.filter { it.area == requested }.map(::entry)
                    )
                )
            }
            "file" -> {
                val f = contents[requested] ?: throw APIError("not_found")
                return bytes(f.copy(readers = library.areas.find { it.path == f.area }?.readers ?: f.readers))
            }
            "search" -> {
                val q = query["q"].orEmpty()
                return bytes(
                    KnowledgeSearchPage(
                        contents.values.filter { it.title.contains(q, true) || it.content.contains(q, true) }
                            .map {
                                KnowledgeSearchResult(
                                    it.path,
                                    it.name,
                                    it.title,
                                    it.kind,
                                    it.area,
                                    it.displayText.take(180)
                                )
                            }
                    )
                )
            }
            "raw" -> {
                val f = contents[requested] ?: throw APIError("not_found")
                // iPhone mocks also have no original bytes. A tiny, valid preview is generated.
                if (f.format == "pdf") return mockPdf()
                throw APIError("not_found")
            }
            else -> throw APIError("not_found")
        }
    }

    private fun entry(f: KnowledgeFile) = KnowledgeEntry(
        f.path,
        f.name,
        f.title,
        f.kind,
        f.format,
        f.size,
        f.modifiedAt,
        f.status,
        f.error
    )

    private fun mockPdf(): ByteArray {
        val objects = listOf(
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        )
        val stream = "BT /F1 18 Tf 20 100 Td (Mock original - Aemeath Bot) Tj ET"
        val all = objects + "<< /Length ${stream.length} >>\nstream\n$stream\nendstream"
        val b = StringBuilder("%PDF-1.4\n")
        val offsets = mutableListOf(0)
        all.forEachIndexed { i, o ->
            offsets += b.length
            b.append("${i+1} 0 obj\n$o\nendobj\n")
        }
        val xref = b.length
        b.append("xref\n0 6\n0000000000 65535 f \n")
        offsets.drop(1).forEach { b.append("%010d 00000 n \n".format(it)) }
        b.append("trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n$xref\n%%EOF")
        return b.toString().toByteArray()
    }
}
