package local.assistant.core

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerializationException
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

// Categories only. Neither exception messages nor logs contain server bodies or credentials.
class APIError(val category: String, val statusCode: Int? = null) : Exception(category) {
    val display
        get() = when (category) {
            "missing_token" -> "还没有填写令牌"
            "unauthorized" -> "令牌不对，或者已经作废、过期"
            "forbidden" -> "这个令牌没有使用 App 的权限，在后端用 scripts.tokens 创建一个 app 权限的令牌"
            "not_found" -> "找不到这一项"
            "conflict" -> "和当前状态冲突（比如任务已经在运行）"
            "invalid_request" -> "内容不符合要求"
            "decoding" -> "后端返回的数据看不懂"
            "server" -> "后端出错（$statusCode）"
            else -> "连不上后端"
        }
}

abstract class Backend {
    abstract suspend fun raw(
        method: String,
        path: List<String>,
        query: Map<String, String> = emptyMap(),
        body: ByteArray? = null,
        contentType: String = "application/json"
    ): ByteArray

    suspend inline fun <reified T> get(vararg path: String, query: Map<String, String> = emptyMap()): T = decode(
        raw("GET", path.toList(), query)
    )

    suspend inline fun <reified T> send(method: String, path: List<String>, body: JsonElement? = null): T = decode(
        raw(method, path, body = body?.toString()?.toByteArray())
    )

    inline fun <reified T> decode(bytes: ByteArray): T = try {
        Wire.decodeFromString<T>(bytes.decodeToString())
    } catch (_: SerializationException) {
        throw APIError("decoding")
    } catch (_: IllegalArgumentException) {
        throw APIError("decoding")
    }

    suspend fun status(): Status = get("status")

    suspend fun today(): Today = get("today")

    suspend fun todos(status: String? = null, limit: Int = 200, offset: Int = 0): Page<Todo> = get(
        "todos",
        query = paging(limit, offset) + listOfNotNull(status?.let { "status" to it }).toMap()
    )

    suspend fun updateTodo(id: String, patch: JsonObject): Todo = send("PATCH", listOf("todos", id), patch)

    suspend fun digests(limit: Int = 200, offset: Int = 0): Page<Digest> = get("digests", query = paging(limit, offset))

    suspend fun runs(limit: Int = 200, offset: Int = 0): Page<Run> = get("runs", query = paging(limit, offset))

    suspend fun plugins(): List<PluginView> = get("plugins")

    suspend fun updatePlugin(id: String, patch: JsonObject): PluginView = send("PATCH", listOf("plugins", id), patch)

    suspend fun deletePlugin(id: String) {
        raw("DELETE", listOf("plugins", id))
    }

    suspend fun cards(id: String): List<Card> = get("plugins", id, "cards")

    suspend fun runJob(id: String): JobAccepted = send("POST", listOf("jobs", id, "run"))

    suspend fun settings(): RuntimeSettings = get("settings")

    suspend fun updateSettings(patch: JsonObject): RuntimeSettings = send("PATCH", listOf("settings"), patch)

    suspend fun healthReports(): Page<HealthReport> = get("health", "reports", query = paging(100, 0))

    suspend fun uploadHealth(batch: HealthBatch): HealthAccepted = send(
        "POST",
        listOf("health", "samples"),
        Wire.encodeToJsonElement(batch)
    )

    suspend fun chat(id: String, offset: Int = 0): Page<ChatMessage> = get(
        "bots",
        id,
        "messages",
        query = paging(200, offset)
    )

    suspend fun sendChat(
        id: String,
        text: String,
        botName: String?,
        choice: ChatChoice
    ): ChatAccepted = send("POST", listOf("bots", id, "messages"), choice.body(text, botName))

    suspend fun knowledge(): KnowledgeOverview = get("knowledge")

    suspend fun updateKnowledge(patch: JsonObject): KnowledgeOverview = send("PATCH", listOf("knowledge"), patch)

    suspend fun setReaders(path: String, readers: List<String>): KnowledgeArea = send(
        "PATCH",
        listOf("knowledge", "access"),
        buildJsonObject {
            put("path", path)
            put("readers", Wire.encodeToJsonElement(readers))
        }
    )

    suspend fun createTopic(name: String, readers: List<String>? = null): KnowledgeArea = send(
        "POST",
        listOf("knowledge", "topics"),
        jsonObject("name" to JsonPrimitive(name), "readers" to readers?.let { Wire.encodeToJsonElement(it) })
    )

    suspend fun knowledgeFiles(path: String): KnowledgeFiles = get("knowledge", "files", query = mapOf("path" to path))

    suspend fun knowledgeFile(path: String): KnowledgeFile = get("knowledge", "file", query = mapOf("path" to path))

    suspend fun searchKnowledge(query: String): List<KnowledgeSearchResult> = get<KnowledgeSearchPage>(
        "knowledge",
        "search",
        query = mapOf("q" to query, "limit" to "30")
    ).results

    suspend fun original(path: String) = raw("GET", listOf("knowledge", "raw"), mapOf("path" to path))

    open suspend fun copyOriginal(path: String, output: java.io.OutputStream) {
        output.write(original(path))
    }

    suspend fun uploadKnowledge(path: String, data: ByteArray, type: String): KnowledgeEntry = decode(
        raw(
            "POST",
            listOf("knowledge", "files"),
            mapOf("path" to path),
            data,
            type
        )
    )

    fun paging(limit: Int, offset: Int) = mapOf("limit" to limit.toString(), "offset" to offset.toString())
}

class LiveBackend(
    base: String,
    private val token: () -> String?,
    private val client: OkHttpClient = defaultClient()
) : Backend() {
    private val baseUrl = base.toHttpUrlOrNull()
        ?.takeIf { it.username.isEmpty() && it.password.isEmpty() && it.query == null && it.fragment == null }
        ?: throw APIError("invalid_request")

    fun url(path: List<String>, query: Map<String, String>): HttpUrl = baseUrl.newBuilder()
        .apply {
            // Preserve reverse-proxy base paths, percent-encode each ID as one segment.
            if (baseUrl.encodedPath.endsWith('/')) removePathSegment(baseUrl.pathSize - 1)
            listOf("api", "v1").plus(path).forEach { addPathSegment(it) }
            query.forEach { (k, v) -> addQueryParameter(k, v) }
        }
        .build()

    override suspend fun raw(
        method: String,
        path: List<String>,
        query: Map<String, String>,
        body: ByteArray?,
        contentType: String
    ): ByteArray = withContext(Dispatchers.IO) {
        val credential = token()?.takeIf { it.isNotBlank() } ?: throw APIError("missing_token")
        val request = Request.Builder()
            .url(url(path, query))
            .header("Authorization", "Bearer $credential")
            .header("Accept", if (path.lastOrNull() == "raw") "*/*" else "application/json")
            .method(
                method,
                if (method in listOf("POST", "PATCH", "PUT")) (body ?: byteArrayOf()).toRequestBody(
                    contentType.toMediaType()
                ) else null
            )
            .build()
        try {
            client.newCall(request)
                .execute()
                .use { response ->
                    if (!response.isSuccessful) throw APIError(
                        when (response.code) {
                            401 -> "unauthorized"
                            403 -> "forbidden"
                            404 -> "not_found"
                            409 -> "conflict"
                            422, 413 -> "invalid_request"
                            else -> "server"
                        },
                        response.code
                    )
                    response.body?.bytes() ?: byteArrayOf()
                }
        } catch (_: IOException) {
            throw APIError("transport")
        }
    }

    override suspend fun copyOriginal(path: String, output: java.io.OutputStream): Unit = withContext(Dispatchers.IO) {
        val credential = token()?.takeIf { it.isNotBlank() } ?: throw APIError("missing_token")
        val request = Request.Builder()
            .url(url(listOf("knowledge", "raw"), mapOf("path" to path)))
            .header("Authorization", "Bearer $credential")
            .build()
        try {
            client.newCall(request)
                .execute()
                .use { response ->
                    if (!response.isSuccessful) throw APIError(
                        when (response.code) {
                            401 -> "unauthorized"
                            403 -> "forbidden"
                            404 -> "not_found"
                            else -> "server"
                        },
                        response.code
                    )
                    val body = response.body ?: throw APIError("transport")
                    body.byteStream().use { it.copyTo(output) }
                }
            Unit
        } catch (_: IOException) {
            throw APIError("transport")
        }
    }

    companion object {
        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .followRedirects(false)
            .followSslRedirects(false)
            .build()
    }
}
