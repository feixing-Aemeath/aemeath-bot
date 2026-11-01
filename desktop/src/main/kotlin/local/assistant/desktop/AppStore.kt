package local.assistant.desktop

import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import local.assistant.core.*
import java.time.Instant
import java.time.ZoneId

data class DesktopState(
    val online: Boolean = false,
    val settled: Boolean = false,
    val loading: Boolean = false,
    val status: Status? = null,
    val today: Today? = null,
    val todos: List<Todo> = emptyList(),
    val digests: List<Digest> = emptyList(),
    val runs: List<Run> = emptyList(),
    val plugins: List<PluginView> = emptyList(),
    val reports: List<HealthReport> = emptyList(),
    val settings: RuntimeSettings? = null,
    val cards: Map<String, List<Card>> = emptyMap(),
    val chats: Map<String, List<ChatMessage>> = emptyMap(),
    val sending: Set<String> = emptySet(),
    val error: String? = null,
    val revision: Long = 0,
    val clock: Instant = Instant.now(),
    val cardStyle: String = "plain",
)

class AppStore(
    val device: DeviceSettings,
    val catalog: StickerCatalog,
    private val scope: CoroutineScope,
    private val backendFactory: () -> Backend = {
        if (device.mock) MockBackend() else {
            // Bind credentials to this connection. An old in-flight request must never read a new server's token.
            val credential = device.tokens.read()
            LiveBackend(device.baseUrl, { credential })
        }
    },
) {
    private val mutable = MutableStateFlow(DesktopState(cardStyle = device.style))
    val state = mutable.asStateFlow()
    var backend: Backend = backendFactory()
        private set
    private val lock = Mutex()
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private var polling: Job? = null
    private var generation = 0

    val zone: ZoneId
        get() = runCatching { ZoneId.of(state.value.settings?.timezone ?: ZoneId.systemDefault().id) }
            .getOrDefault(ZoneId.systemDefault())

    fun bots(): List<Bot> = Bot.build(state.value.plugins, catalog)
    fun messages(bot: Bot): List<BotMessage> {
        val current = state.value
        return BotTimeline.messages(
            bot, current.today, current.digests, current.todos, current.runs,
            current.reports, current.chats[bot.id].orEmpty(), current.clock, zone,
        )
    }

    fun unread(bot: Bot): Boolean = BotTimeline.unread(
        messages(bot), Instant.ofEpochMilli(device.seen(bot.id)), firstLaunchInstant(device),
    ) > 0

    fun markSeen(id: String) {
        device.markSeen(id)
    }

    fun choose(id: String) {
        device.chosen = id
        markSeen(id)
        scope.launch { loadBot(id) }
    }

    fun replying(bot: Bot): Boolean = bot.id in state.value.sending ||
        state.value.chats[bot.id].orEmpty().any { it.author == "bot" && it.status == "pending" }

    fun botState(bot: Bot): MascotState {
        val current = state.value
        if (!current.online) return MascotState.OFFLINE
        if (replying(bot)) return MascotState.WORKING
        if (bot.kind == BotKind.KNOWLEDGE) {
            return MascotState.knowledge(current.plugins.filter { it.id in bot.joined }, current.runs, current.clock)
        }
        return current.plugins.find { it.id == bot.id }?.let {
            MascotState.plugin(it, current.runs, current.clock)
        } ?: MascotState.assistant(current.status, current.runs, current.clock)
    }

    fun statusLine(bot: Bot): String {
        val current = state.value
        if (bot.kind == BotKind.KNOWLEDGE) {
            return knowledgeStatusLine(current.plugins.filter { it.id in bot.joined }, current.runs, current.clock, zone)
        }
        return current.plugins.find { it.id == bot.id }?.statusLine(current.runs, current.todos, current.clock, zone)
            ?: botState(bot).headline
    }

    fun start() {
        if (polling?.isActive == true) return
        polling = scope.launch {
            launch {
                while (isActive) {
                    delay(1_000)
                    mutable.update { it.copy(clock = Instant.now()) }
                }
            }
            while (isActive) {
                refresh()
                val current = state.value
                withTimeoutOrNull(RefreshPolicy.interval(current.runs, current.chats.values, current.sending.isNotEmpty())) {
                    wake.receive()
                }
            }
        }
    }

    fun stop() {
        polling?.cancel()
        polling = null
    }

    fun error(text: String) {
        mutable.update { it.copy(error = text) }
    }

    fun dismissError() {
        mutable.update { it.copy(error = null) }
    }

    fun setStyle(style: String) {
        device.style = style
        mutable.update { it.copy(cardStyle = style) }
    }

    suspend fun perform(label: String, operation: suspend () -> Unit): Boolean = try {
        operation()
        true
    } catch (error: CancellationException) {
        throw error
    } catch (error: Exception) {
        // Do not show arbitrary exception messages: they may contain URLs, tokens or chat text.
        this.error("$label：${(error as? APIError)?.display ?: "操作失败，请重试"}")
        false
    }

    private suspend fun <T> optional(fallback: T, operation: suspend () -> T): T = try {
        operation()
    } catch (error: CancellationException) {
        throw error
    } catch (_: Exception) {
        fallback
    }

    suspend fun refresh() = lock.withLock {
        val api = backend
        val before = state.value
        mutable.update { it.copy(loading = true) }
        try {
            val loaded = coroutineScope {
                val status = async { api.status() }
                val today = async { api.today() }
                val todos = async { api.todos().items }
                val runs = async { api.runs().items }
                val plugins = async { api.plugins() }
                val digests = async { optional(before.digests) { api.digests().items } }
                val reports = async { optional(before.reports) { api.healthReports().items } }
                val settings = async { optional(before.settings) { api.settings() } }
                before.copy(
                    status = status.await(), today = today.await(), todos = todos.await(),
                    runs = runs.await(), plugins = plugins.await(), digests = digests.await(),
                    reports = reports.await(), settings = settings.await(),
                )
            }
            mutable.update {
                loaded.copy(
                    online = loaded.status?.online == true, settled = true, loading = false,
                    error = it.error, sending = it.sending, revision = it.revision + 1, clock = Instant.now(),
                    chats = it.chats, cards = it.cards,
                    cardStyle = it.cardStyle,
                )
            }
            val selected = bots().find { it.id == device.chosen } ?: bots().first()
            if (device.chosen != "settings" && selected.id != device.chosen) device.chosen = selected.id
            loadBot(selected.id)
            supervisorScope {
                bots().filter { it.id != selected.id && canChat(it) }.map { bot ->
                    launch { loadChat(bot.id) }
                }.joinAll()
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            mutable.update { it.copy(online = false, settled = true, loading = false) }
        }
    }

    private fun canChat(bot: Bot): Boolean = bot.kind != BotKind.SOURCE &&
        (bot.builtIn || state.value.plugins.any { it.id == bot.id && it.enabled })

    suspend fun loadBot(id: String) {
        val bot = bots().find { it.id == id } ?: return
        val api = backend
        val epoch = generation
        supervisorScope {
            bot.pluginIds.map { plugin ->
                launch {
                    try {
                        val cards = api.cards(plugin)
                        if (epoch == generation) mutable.update { it.copy(cards = it.cards + (plugin to cards)) }
                    } catch (error: CancellationException) {
                        throw error
                    } catch (_: Exception) {
                        // A broken plugin never takes away the other cards or the conversation.
                    }
                }
            }.joinAll()
        }
        if (canChat(bot)) loadChat(id)
    }

    suspend fun loadChat(id: String, earlier: Boolean = false) {
        val api = backend
        val epoch = generation
        try {
            val existing = state.value.chats[id].orEmpty()
            val page = api.chat(id, if (earlier) existing.size else 0)
            if (epoch != generation) return
            mutable.update { current ->
                val rows = (current.chats[id].orEmpty() + page.items).groupBy { it.id }.values.map { versions ->
                    versions.maxWith(compareBy<ChatMessage> { instant(it.updatedAt) }.thenBy { if (it.status == "pending") 0 else 1 })
                }.sortedBy { instant(it.createdAt) }
                current.copy(chats = current.chats + (id to rows))
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            if (earlier) error("读取更早的消息失败，请检查连接")
        }
    }

    suspend fun send(bot: Bot, text: String): Boolean {
        if (!canChat(bot) || replying(bot) || text.isBlank() || text.trim().length > 2_000) return false
        val epoch = generation
        val api = backend
        mutable.update { it.copy(sending = it.sending + bot.id) }
        return try {
            perform("没发出去") {
                val accepted = api.sendChat(bot.id, text.trim(), bot.name, device.choice)
                if (epoch == generation) {
                    mutable.update {
                        it.copy(chats = it.chats + (bot.id to (it.chats[bot.id].orEmpty() + accepted.message + accepted.reply)))
                    }
                    wake.trySend(Unit)
                }
            }
        } finally {
            if (epoch == generation) mutable.update { it.copy(sending = it.sending - bot.id) }
        }
    }

    suspend fun updateTodo(todo: Todo, patch: JsonObject): Boolean = lock.withLock {
        if (patch.isEmpty()) return@withLock true
        perform("没能保存待办") {
            val updated = backend.updateTodo(todo.id, patch)
            fun List<Todo>.replaced(): List<Todo> = map { if (it.id == updated.id) updated else it }
                .filter { it.status != "deleted" }
            mutable.update { current ->
                current.copy(
                    todos = current.todos.replaced(), today = current.today?.let { it.copy(todos = it.todos.replaced()) },
                    cards = current.cards.mapValues { (_, cards) ->
                        cards.map { card ->
                            val content = card.content
                            if (content is CardContent.Todos) {
                                card.copy(data = JsonObject(card.data.obj() + ("todos" to Wire.encodeToJsonElement(content.todos.replaced()))))
                            } else card
                        }
                    },
                )
            }
        }
    }

    suspend fun toggle(todo: Todo) = updateTodo(todo, buildJsonObject {
        put("status", if (todo.status == "completed") "pending" else "completed")
    })

    suspend fun runNow(id: String = "extract_todos"): Boolean {
        val result = lock.withLock { perform("没能开始运行") { backend.runJob(id) } }
        refresh()
        wake.trySend(Unit)
        return result
    }

    suspend fun updatePlugin(plugin: PluginView, patch: JsonObject): Boolean = lock.withLock {
        perform("没能保存插件设置") {
            val latest = state.value.plugins.find { it.id == plugin.id } ?: plugin
            val merged = if (patch.containsKey("config")) {
                JsonObject(patch + ("config" to PluginConfigEdit.merging(plugin.config, latest.config, patch["config"].obj())))
            } else patch
            val updated = backend.updatePlugin(plugin.id, merged)
            mutable.update {
                it.copy(plugins = it.plugins.map { old -> if (old.id == updated.id) updated else old })
            }
            loadBot(device.chosen)
        }
    }

    suspend fun deletePlugin(id: String): Boolean = lock.withLock {
        perform("没能删除插件") {
            backend.deletePlugin(id)
            mutable.update { it.copy(plugins = it.plugins.filterNot { plugin -> plugin.id == id }, cards = it.cards - id) }
        }
    }

    suspend fun updateSchedule(draft: ScheduleDraft): Boolean = lock.withLock {
        val original = state.value.settings ?: return@withLock false
        perform("没能保存时间表") {
            val updated = backend.updateSettings(draft.patch(original))
            mutable.update { it.copy(settings = updated) }
        }
    }

    suspend fun connect(url: String, token: String, mock: Boolean, clearToken: Boolean = false): Boolean {
        if (!mock && runCatching { LiveBackend(url.trim(), { null }) }.isFailure) {
            error("后端地址不符合要求")
            return false
        }
        val saved = lock.withLock {
            perform("没能保存连接") {
                if (clearToken) device.tokens.save("") else if (token.isNotBlank()) device.tokens.save(token)
                device.baseUrl = url.trim()
                device.mock = mock
                generation++
                backend = backendFactory()
                mutable.value = DesktopState(cardStyle = device.style)
            }
        }
        if (saved) {
            refresh()
            wake.trySend(Unit)
        }
        return saved
    }
}
