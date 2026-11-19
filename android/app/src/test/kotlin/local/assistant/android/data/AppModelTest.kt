package local.assistant.android.data

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import kotlinx.serialization.json.*
import local.assistant.core.*
import org.junit.Assert.*
import org.junit.Before
import org.junit.After
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class AppModelTest {
    private val application get() = ApplicationProvider.getApplicationContext<Application>()
    private var model: AppModel? = null

    @Before
    fun reset() {
        application.getSharedPreferences("device", 0).edit().clear().commit()
    }

    @After
    fun stop() {
        model?.stop()
        Dispatchers.resetMain()
    }

    private fun model(api: Backend): AppModel = AppModel(application, api, backendFactory = { api }).also { model = it }

    private fun backend(handler: suspend (String, List<String>, Map<String, String>) -> ByteArray?): Backend {
        val mock = MockBackend()
        return object : Backend() {
            override suspend fun raw(method: String, path: List<String>, query: Map<String, String>, body: ByteArray?, contentType: String): ByteArray =
                handler(method, path, query) ?: mock.raw(method, path, query, body, contentType)
        }
    }

    @Test
    fun leavingConnectionScreenDoesNotStopRefreshOrPolling() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val started = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        var requests = 0
        val model = model(backend { _, path, _ ->
            if (path == listOf("status")) {
                requests++
                started.complete(Unit)
                release.await()
            }
            null
        })
        val screen = launch { model.connect("http://example.invalid", "", true) }
        started.await()
        screen.cancelAndJoin()
        release.complete(Unit)
        runCurrent()
        assertTrue(model.state.value.online)
        assertFalse(model.state.value.loading)
        val before = requests
        advanceTimeBy(30_000)
        runCurrent()
        assertTrue(requests > before)
        model.stop()
    }

    @Test
    fun healthReportsFailureKeepsCoreOnlineAndPreviousReports() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        var failReports = false
        val model = model(backend { _, path, _ ->
            if (failReports && path == listOf("health", "reports")) throw APIError("server", 500)
            null
        })
        model.refresh()
        val reports = model.state.value.reports
        assertTrue(reports.isNotEmpty())
        failReports = true
        model.refresh()
        assertTrue(model.state.value.online)
        assertTrue(model.state.value.todos.isNotEmpty())
        assertEquals(reports, model.state.value.reports)
    }

    @Test
    fun cancellingHealthReadStillCancelsRefresh() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val model = model(backend { _, path, _ ->
            if (path == listOf("health", "reports")) throw CancellationException("cancelled")
            null
        })
        val refreshing = async { model.refresh() }
        try {
            refreshing.await()
            fail("Cancellation must propagate")
        } catch (_: CancellationException) {}
        assertFalse(model.state.value.online)
        assertFalse(model.state.value.loading)
    }

    @Test
    fun slowChatReadCannotEraseAcceptedMessage() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
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
        val model = model(api)
        model.refresh()
        intercept = true
        val reading = async { model.loadChat("assistant") }
        started.await()
        assertTrue(model.send(model.bots().first(), "刚发出的消息"))
        release.complete(Unit)
        reading.await()
        assertEquals(2, model.state.value.chats.getValue("assistant").size)
        assertTrue(model.replying(model.bots().first()))
        model.stop()
    }

    @Test
    fun sameDayChatHistoryLoadsTheRemainingPage() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val rows = (202 downTo 1).map {
            ChatMessage("m$it", "assistant", "me", "消息$it", "ready", createdAt = "2026-11-05T08:00:00Z", updatedAt = "2026-11-05T08:00:00Z")
        }
        val offsets = mutableListOf<Int>()
        val model = model(backend { _, path, query ->
            if (path != listOf("bots", "assistant", "messages")) null else {
                val offset = query.getValue("offset").toInt()
                offsets += offset
                Wire.encodeToJsonElement(Page(rows.drop(offset).take(200), rows.size)).toString().toByteArray()
            }
        })
        model.loadChat("assistant")
        assertEquals(202, model.state.value.chatTotals["assistant"])
        assertEquals(200, model.state.value.chats.getValue("assistant").size)
        model.loadChat("assistant", earlier = true)
        assertEquals(listOf(0, 200), offsets)
        assertEquals(202, model.state.value.chats.getValue("assistant").size)
    }

    @Test
    fun emptyChatPageBeforeTotalShowsAnError() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val model = model(backend { _, path, _ ->
            if (path == listOf("bots", "assistant", "messages")) """{"items":[],"total":202}""".toByteArray() else null
        })
        model.loadChat("assistant", earlier = true)
        assertNotNull(model.state.value.error)
        assertTrue(model.state.value.chats.isEmpty())
    }

    @Test
    fun refreshIncludesTodoAfterTwoHundredCompletedItems() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val todos = (0..200).map { Todo("t$it", "待办$it", status = if (it < 200) "completed" else "pending") }
        val model = model(backend { _, path, query ->
            if (path == listOf("todos")) {
                val offset = query.getValue("offset").toInt()
                Wire.encodeToJsonElement(Page(todos.drop(offset).take(200), todos.size)).toString().toByteArray()
            } else null
        })
        model.refresh()
        assertTrue(model.state.value.online)
        assertEquals("t200", model.state.value.todos.last().id)
    }

    @Test
    fun cancelledTodoDisappearsImmediatelyAndAfterRefresh() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val model = model(MockBackend())
        model.refresh()
        val todo = model.state.value.today!!.todos.first()
        model.updateTodo(todo, buildJsonObject { put("status", "cancelled") }).join()
        assertFalse(model.state.value.todos.any { it.id == todo.id })
        assertFalse(model.state.value.today!!.todos.any { it.id == todo.id })
        model.refresh()
        assertFalse(model.state.value.todos.any { it.id == todo.id })
    }
}
