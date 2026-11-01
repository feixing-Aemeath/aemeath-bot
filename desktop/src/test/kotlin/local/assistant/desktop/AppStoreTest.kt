package local.assistant.desktop

import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.serialization.json.*
import local.assistant.core.*
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer

@OptIn(ExperimentalCoroutinesApi::class)
class AppStoreTest {
    @get:Rule
    val temporary = TemporaryFolder()

    private fun store(scope: CoroutineScope, backend: Backend = MockBackend()): AppStore = AppStore(
        DeviceSettings(temporary.root.toPath()), Resources.catalog, scope,
    ) { backend }

    @Test
    fun mockRefreshLoadsAllDesktopSections() = runTest {
        val store = store(this)
        store.refresh()
        val state = store.state.value
        assertTrue(state.online)
        assertTrue(state.settled)
        assertNotNull(state.today)
        assertNotNull(state.settings)
        assertTrue(state.plugins.isNotEmpty())
        assertTrue(state.todos.isNotEmpty())
        assertEquals(BotKind.ASSISTANT, store.bots().first().kind)
        assertTrue(store.bots().any { it.kind == BotKind.KNOWLEDGE })
    }

    @Test
    fun mockTodoCompletionEditingAndDeletionUpdateVisibleData() = runTest {
        val store = store(this)
        store.refresh()
        val todo = store.state.value.today!!.todos.first()
        assertTrue(store.toggle(todo))
        assertEquals("completed", store.state.value.todos.first { it.id == todo.id }.status)
        assertTrue(store.updateTodo(todo, buildJsonObject { put("title", "改过的标题") }))
        assertEquals("改过的标题", store.state.value.today!!.todos.first { it.id == todo.id }.title)
        assertTrue(store.updateTodo(todo, buildJsonObject { put("status", "deleted") }))
        assertFalse(store.state.value.todos.any { it.id == todo.id })
        assertFalse(store.state.value.today!!.todos.any { it.id == todo.id })
    }

    @Test
    fun mockChatLocksSendingUntilTheReplyArrives() = runTest {
        val store = store(this)
        store.refresh()
        val bot = store.bots().first()
        assertTrue(store.send(bot, "帮我看看今天的待办"))
        assertTrue(store.replying(bot))
        assertFalse(store.send(bot, "第二句"))
        assertEquals(2, store.state.value.chats.getValue(bot.id).size)
        assertEquals(MascotState.WORKING, store.botState(bot))
    }

    @Test
    fun sourcesNeverSendMessages() = runTest {
        val store = store(this)
        store.refresh()
        val source = store.bots().first { it.kind == BotKind.SOURCE }
        assertFalse(store.send(source, "不能发送"))
        assertFalse(store.state.value.chats.containsKey(source.id))
    }

    @Test
    fun mockPluginsCanBeDisabledAndDeletedAtRuntime() = runTest {
        val store = store(this)
        store.refresh()
        val plugin = store.state.value.plugins.first { it.enabled && it.state != "unsupported" }
        assertTrue(store.updatePlugin(plugin, buildJsonObject { put("enabled", false) }))
        assertFalse(store.state.value.plugins.first { it.id == plugin.id }.enabled)
        assertTrue(store.deletePlugin(plugin.id))
        assertFalse(store.state.value.plugins.any { it.id == plugin.id })
        assertTrue(store.state.value.online)
    }

    @Test
    fun mockScheduleUpdatesSurviveRefresh() = runTest {
        val store = store(this)
        store.refresh()
        val original = store.state.value.settings!!
        assertTrue(store.updateSchedule(ScheduleDraft.from(original).copy(source = "*/15 * * * *", preferred = "codex")))
        store.refresh()
        assertEquals("*/15 * * * *", store.state.value.settings!!.sourceCron)
        assertEquals("codex", store.state.value.settings!!.harnessOrder.first())
    }

    @Test
    fun mockRunNowAppearsInTheRunTable() = runTest {
        val store = store(this)
        store.refresh()
        assertTrue(store.runNow())
        assertTrue(store.state.value.runs.any { it.job == "extract_todos" && it.status == "running" })
    }

    @Test
    fun optionalPluginFailuresDoNotMakeTheBackendOffline() = runTest {
        val mock = MockBackend()
        val backend = object : Backend() {
            override suspend fun raw(method: String, path: List<String>, query: Map<String, String>, body: ByteArray?, contentType: String): ByteArray {
                if (path == listOf("health", "reports") || path.lastOrNull() == "cards") throw APIError("server", 500)
                return mock.raw(method, path, query, body, contentType)
            }
        }
        val store = store(this, backend)
        store.refresh()
        assertTrue(store.state.value.online)
        assertTrue(store.state.value.todos.isNotEmpty())
    }

    @Test
    fun errorsNeverExposeExceptionMessages() = runTest {
        val store = store(this)
        assertFalse(store.perform("保存失败") { throw IllegalStateException("SECRET-TOKEN and CHAT CONTENT") })
        assertEquals("保存失败：操作失败，请重试", store.state.value.error)
        store.dismissError()
        assertNull(store.state.value.error)
    }

    @Test
    fun invalidConnectionDoesNotReplaceTheWorkingSession() = runTest {
        val store = store(this)
        store.refresh()
        val original = store.backend
        assertFalse(store.connect("not-a-url", "", false))
        assertSame(original, store.backend)
        assertTrue(store.state.value.online)
    }

    @Test
    fun mockKnowledgeSupportsTopicPermissionsSearchAndUploads() = runTest {
        val store = store(this)
        val api = store.backend
        val library = api.knowledge()
        val area = api.createTopic("桌面测试", library.defaultReaders)
        val changed = api.setReaders(area.path, emptyList())
        assertTrue(changed.readers.isEmpty())
        val entry = api.uploadKnowledge("${area.path}/测试.txt", "示例资料".toByteArray(), "text/plain")
        assertEquals("测试.txt", entry.name)
        assertTrue(api.knowledgeFiles(area.path).entries.any { it.path == entry.path })
        assertTrue(api.searchKnowledge("测试").any { it.path == entry.path })
    }

    @Test
    fun bundledCatalogUsesTheExistingMacStickerNames() {
        assertTrue("aemeath" in Resources.catalog.names)
        assertTrue("aemeath" in Resources.catalog.layered)
        assertEquals("aemeath", Resources.catalog.roles["assistant"]?.sticker)
    }

    @Test
    fun pollingUsesThirtySecondsAndStartIsIdempotent() = runTest {
        val mock = MockBackend()
        var refreshes = 0
        val api = object : Backend() {
            override suspend fun raw(method: String, path: List<String>, query: Map<String, String>, body: ByteArray?, contentType: String): ByteArray {
                if (path == listOf("status")) refreshes++
                return mock.raw(method, path, query, body, contentType)
            }
        }
        val store = store(backgroundScope, api)
        store.start()
        store.start()
        runCurrent()
        assertEquals(1, refreshes)
        advanceTimeBy(29_999)
        runCurrent()
        assertEquals(1, refreshes)
        advanceTimeBy(1)
        runCurrent()
        assertEquals(2, refreshes)
        store.stop()
    }

    @Test
    fun pendingChatWakesTheIdlePollerAndUsesTwoSeconds() = runTest {
        val mock = MockBackend()
        var refreshes = 0
        val api = object : Backend() {
            override suspend fun raw(method: String, path: List<String>, query: Map<String, String>, body: ByteArray?, contentType: String): ByteArray {
                if (path == listOf("status")) refreshes++
                return mock.raw(method, path, query, body, contentType)
            }
        }
        val store = store(backgroundScope, api)
        store.start()
        runCurrent()
        assertTrue(store.send(store.bots().first(), "看看待办"))
        runCurrent()
        val before = refreshes
        advanceTimeBy(2_000)
        runCurrent()
        assertEquals(before + 1, refreshes)
        store.stop()
    }

    @Test
    fun cancellingAnOperationPropagatesCancellation() = runTest {
        val store = store(this)
        var cancelled = false
        try {
            store.perform("不该显示错误") { throw CancellationException("cancelled") }
        } catch (_: CancellationException) {
            cancelled = true
        }
        assertTrue(cancelled)
        assertNull(store.state.value.error)
    }

    @Test
    fun slowChatReadCannotEraseAnAcceptedMessage() = runTest {
        val mock = MockBackend()
        val started = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        var intercept = false
        val api = object : Backend() {
            override suspend fun raw(method: String, path: List<String>, query: Map<String, String>, body: ByteArray?, contentType: String): ByteArray {
                val response = mock.raw(method, path, query, body, contentType)
                if (intercept && method == "GET" && path == listOf("bots", "assistant", "messages")) {
                    intercept = false
                    started.complete(Unit)
                    release.await()
                }
                return response
            }
        }
        val store = store(this, api)
        store.refresh()
        intercept = true
        val reading = async { store.loadChat("assistant") }
        started.await()
        assertTrue(store.send(store.bots().first(), "刚发出的消息"))
        release.complete(Unit)
        reading.await()
        assertEquals(2, store.state.value.chats.getValue("assistant").size)
        assertTrue(store.replying(store.bots().first()))
    }

    @Test
    fun inFlightRefreshPreservesTheNewCardStyle() = runTest {
        val mock = MockBackend()
        val started = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        val api = object : Backend() {
            override suspend fun raw(method: String, path: List<String>, query: Map<String, String>, body: ByteArray?, contentType: String): ByteArray {
                if (path == listOf("status")) {
                    started.complete(Unit)
                    release.await()
                }
                return mock.raw(method, path, query, body, contentType)
            }
        }
        val store = store(this, api)
        val refreshing = async { store.refresh() }
        started.await()
        store.setStyle("soft")
        release.complete(Unit)
        refreshing.await()
        assertEquals("soft", store.state.value.cardStyle)
    }

    @Test
    fun existingConnectionNeverReadsAReplacementCredential() = runTest {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse().setBody("{}"))
            val device = DeviceSettings(temporary.root.toPath())
            device.mock = false
            device.baseUrl = server.url("/").toString()
            device.tokens.save("old-connection-token")
            val store = AppStore(device, Resources.catalog, this)
            device.tokens.save("replacement-token")
            store.backend.raw("GET", listOf("status"))
            assertEquals("Bearer old-connection-token", server.takeRequest().getHeader("Authorization"))
        }
    }
}
