package local.assistant.android.data

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import local.assistant.core.*
import java.time.*

data class AppState(
    val online: Boolean = false,
    val loading: Boolean = false,
    val settled: Boolean = false,
    val status: Status? = null,
    val today: Today? = null,
    val todos: List<Todo> = emptyList(),
    val digests: List<Digest> = emptyList(),
    val runs: List<Run> = emptyList(),
    val plugins: List<PluginView> = emptyList(),
    val reports: List<HealthReport> = emptyList(),
    val cards: Map<String, List<Card>> = emptyMap(),
    val chats: Map<String, List<ChatMessage>> = emptyMap(),
    val settings: RuntimeSettings? = null,
    val error: String? = null,
    val revision: Long = 0,
    val clock: Instant = Instant.now(),
)

class AppModel @JvmOverloads constructor(
    application: Application,
    initialBackend: Backend? = null,
    private val now: () -> Instant = { Instant.now() }
) : AndroidViewModel(application) {
    val device = DeviceSettings(application)
    var backend: Backend = initialBackend ?: device.backend()
        private set

    val catalog: StickerCatalog = run {
        val assets = application.assets
        val entries = assets.list("").orEmpty()
        val paths = entries.toSet() + entries.flatMap { folder -> assets.list(folder).orEmpty().map { "$folder/$it" } }
        StickerCatalog.fromPaths(paths) { name ->
            runCatching { assets.open(name).bufferedReader().use { it.readText() } }.getOrNull()
        }
    }
    private val mutable = MutableStateFlow(AppState())
    val state = mutable.asStateFlow()
    private var polling: Job? = null
    private val chatPolls = mutableMapOf<String, Job>()
    private val pluginWrites = mutableMapOf<String, Mutex>()
    private var generation = 0
    val zone: ZoneId
        get() = runCatching { ZoneId.of(mutable.value.settings?.timezone ?: ZoneId.systemDefault().id) }
            .getOrDefault(ZoneId.systemDefault())

    fun bots() = Bot.build(mutable.value.plugins, catalog)

    fun selected() = PadPresentation.selectedBot(device.chosen, bots(), mutable.value.settled) ?: bots().first()

    fun dismissError() {
        mutable.update { it.copy(error = null) }
    }

    fun error(text: String) {
        mutable.update { it.copy(error = text) }
    }

    fun choose(id: String) {
        device.chosen = id
        device.markSeen(id)
        mutable.update { it.copy(revision = it.revision + 1) }
        viewModelScope.launch { loadBot(id) }
    }

    fun start() {
        if (polling != null) return
        polling = viewModelScope.launch {
            // Refresh relative labels and expire the three-second completion pose without more HTTP calls.
            launch {
                while (isActive) {
                    delay(1000)
                    mutable.update { it.copy(clock = Instant.now()) }
                }
            }
            while (isActive) {
                refresh()
                delay(if (mutable.value.runs.any { it.status == "running" }) 2000 else 30000)
            }
        }
    }

    fun stop() {
        polling?.cancel()
        polling = null
        chatPolls.values.forEach { it.cancel() }
        chatPolls.clear()
    }

    suspend fun refresh() {
        val api = backend
        val epoch = generation
        val revision = mutable.value.revision
        mutable.update { it.copy(loading = true) }
        try {
            val loaded = coroutineScope {
                val status = async { api.status() }
                val today = async { api.today() }
                val todos = async { api.todos().items }
                val digests = async { api.digests().items }
                val runs = async { api.runs().items }
                val plugins = async { api.plugins() }
                val reports = async { api.healthReports().items }
                val settings = async { api.settings() }
                AppState(
                    status = status.await(),
                    today = today.await(),
                    todos = todos.await(),
                    digests = digests.await(),
                    runs = runs.await(),
                    plugins = plugins.await(),
                    reports = reports.await(),
                    settings = settings.await()
                )
            }
            if (epoch != generation) return
            if (revision != mutable.value.revision) {
                mutable.update { it.copy(loading = false) }
                return
            }
            mutable.update {
                loaded.copy(
                    online = loaded.status?.online == true,
                    settled = true,
                    cards = it.cards,
                    chats = it.chats,
                    error = it.error,
                    revision = it.revision + 1,
                    clock = now()
                )
            }
            if (bots().none { it.id == device.chosen }) device.chosen = "assistant"
            loadBot(device.chosen)
            // Fetch other conversations too, so unread markers reflect replies while away.
            supervisorScope {
                bots().filter { it.kind != BotKind.SOURCE && it.id != device.chosen }.map { bot ->
                    launch { loadChat(bot.id) }
                }.joinAll()
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            if (epoch == generation) mutable.update { it.copy(online = false, loading = false, settled = true) }
        }
    }

    suspend fun loadBot(id: String) {
        val bot = bots().find { it.id == id } ?: return
        val epoch = generation
        val api = backend
        supervisorScope {
            bot.pluginIds.map { plugin ->
                launch {
                    try {
                        val cards = api.cards(plugin)
                        if (epoch == generation) mutable.update { it.copy(cards = it.cards + (plugin to cards)) }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (_: Exception) {
                        /* isolate one broken plugin */
                    }
                }
            }
                .joinAll()
        }
        if (bot.kind != BotKind.SOURCE) loadChat(id)
    }

    suspend fun loadChat(id: String) {
        val epoch = generation
        val api = backend
        try {
            val rows = api.chat(id).items.asReversed()
            if (epoch != generation) return
            mutable.update { it.copy(chats = it.chats + (id to rows)) }
            if (rows.any { it.status == "pending" } && chatPolls[id]?.isActive != true) chatPolls[id] =
                viewModelScope.launch {
                    try {
                        repeat(600) {
                            delay(1500)
                            val next = api.chat(id).items.asReversed()
                            if (epoch != generation) return@launch
                            mutable.update { it.copy(chats = it.chats + (id to next)) }
                            if (next.none { it.status == "pending" }) {
                                refresh()
                                return@launch
                            }
                        }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (_: Exception) {
                        error("读取回复失败：检查连接后下拉刷新")
                    }
                }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {}
    }

    suspend fun perform(label: String, action: suspend () -> Unit): Boolean = try {
        action()
        true
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        error("$label：${(e as? APIError)?.display ?: "操作失败，请重试"}")
        false
    }

    suspend fun send(bot: Bot, text: String): Boolean {
        if (bot.kind == BotKind.SOURCE || text.isBlank()) return false
        val epoch = generation
        val api = backend
        return perform("没发出去") {
            val accepted = api.sendChat(
                bot.id,
                text.trim(),
                bot.name,
                device.choice
            )
            if (epoch == generation) {
                mutable.update {
                    it.copy(
                        chats = it.chats + (bot.id to (it.chats[bot.id].orEmpty() + accepted.message + accepted.reply))
                    )
                }
                loadChat(bot.id)
            }
        }
    }

    fun updateTodo(todo: Todo, patch: JsonObject) = viewModelScope.launch {
        if (patch.isEmpty()) return@launch
        val epoch = generation
        val api = backend
        perform("没能保存待办") {
            val updated = api.updateTodo(todo.id, patch)
            if (epoch != generation) return@perform
            mutable.update { s ->
                s.copy(
                    revision = s.revision + 1,
                    todos = s.todos.map { if (it.id == updated.id) updated else it },
                    today = s.today?.let { t ->
                        t.copy(
                            todos = t.todos.map { if (it.id == updated.id) updated else it }.filter {
                                it.status != "deleted"
                            }
                        )
                    },
                    cards = s.cards.mapValues { (_, cards) ->
                        cards.map { c ->
                            if (c.type == "todo_list") c.copy(
                                data = JsonObject(
                                    c.data.obj() + ("todos" to Wire.encodeToJsonElement(
                                        (c.content as? CardContent.Todos)?.todos.orEmpty()
                                            .map { if (it.id == updated.id) updated else it }
                                            .filter { it.status != "deleted" }
                                    ))
                                )
                            ) else c
                        }
                    }
                )
            }
        }
    }

    fun toggle(todo: Todo) = updateTodo(
        todo,
        buildJsonObject { put("status", if (todo.status == "completed") "pending" else "completed") }
    )

    fun runNow(id: String = "extract_todos") = viewModelScope.launch {
        perform("没能开始运行") { backend.runJob(id) }
        refresh()
    }

    fun updatePlugin(id: String, patch: JsonObject): Job {
        val before = mutable.value.plugins.find { it.id == id }?.config ?: JsonObject(emptyMap())
        val epoch = generation
        val api = backend
        val lock = pluginWrites.getOrPut(id) { Mutex() }
        return viewModelScope.launch {
            lock.withLock {
                if (epoch != generation) return@withLock
                perform("没能保存插件设置") {
                    val latest = mutable.value.plugins.find { it.id == id }?.config ?: before
                    val merged = if (patch.containsKey("config")) JsonObject(
                        patch + ("config" to PluginConfigEdit.merging(before, latest, patch["config"].obj()))
                    ) else patch
                    val p = api.updatePlugin(id, merged)
                    if (epoch == generation) {
                        mutable.update {
                            it.copy(
                                revision = it.revision + 1,
                                plugins = it.plugins.map { old -> if (old.id == p.id) p else old }
                            )
                        }
                        loadBot(device.chosen)
                    }
                }
            }
        }
    }

    suspend fun connect(url: String, token: String, mock: Boolean): Boolean {
        if (!mock && !runCatching { LiveBackend(url, token = { null }) }.isSuccess) {
            error("后端地址不符合要求")
            return false
        }
        return perform("没能保存连接") {
            if (token.isNotBlank()) device.saveToken(token)
            stop()
            generation++
            device.baseUrl = url.trim()
            device.mock = mock
            backend = device.backend()
            device.resetHealthCursors()
            mutable.value = AppState()
            refresh()
            start()
        }
    }

    fun messages(bot: Bot): List<BotMessage> {
        val s = mutable.value
        return BotTimeline.messages(
            bot,
            s.today,
            s.digests,
            s.todos,
            s.runs,
            s.reports,
            s.chats[bot.id].orEmpty(),
            now(),
            zone
        )
    }

    fun unread(bot: Bot) = BotTimeline.unread(
        messages(bot),
        Instant.ofEpochMilli(device.seen(bot.id)),
        Instant.ofEpochMilli(device.firstLaunch)
    )

    fun replying(bot: Bot) = mutable.value.chats[bot.id].orEmpty().any { it.author == "bot" && it.status == "pending" }

    fun botState(bot: Bot): MascotState {
        val s = mutable.value
        val now = now()
        if (!s.online) return MascotState.OFFLINE
        if (replying(bot)) return MascotState.WORKING
        if (bot.kind == BotKind.KNOWLEDGE) return MascotState.knowledge(
            s.plugins.filter { it.id in bot.joined },
            s.runs,
            now
        )
        return s.plugins.find { it.id == bot.id }?.let { MascotState.plugin(it, s.runs, now) } ?: MascotState.assistant(
            s.status,
            s.runs,
            now
        )
    }

    fun statusLine(bot: Bot): String {
        val s = mutable.value
        val now = now()
        if (bot.kind == BotKind.KNOWLEDGE) return knowledgeStatusLine(
            s.plugins.filter { it.id in bot.joined },
            s.runs,
            now,
            zone
        )
        return s.plugins.find { it.id == bot.id }?.statusLine(
            s.runs,
            s.todos,
            now,
            zone
        ) ?: botState(bot).headline
    }
}
