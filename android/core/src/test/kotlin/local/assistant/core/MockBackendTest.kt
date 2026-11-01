package local.assistant.core

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class MockBackendTest {
    @Test
    fun todayHasOpenDueTodosAndSources() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val today = api.today()
        assertEquals("2026-10-15", today.date)
        assertTrue(today.todos.isNotEmpty())
        assertTrue(
            today.todos.all {
                it.status == "pending" &&
                    instant(it.dueAt!!).atZone(testZone).toLocalDate() <= testNow.atZone(testZone).toLocalDate()
            }
        )
        assertTrue(api.todos().items.any { !it.sources.isNullOrEmpty() })
    }

    @Test
    fun completingEditingClearingAndDeletingTodos() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val id = api.todos().items.first().id
        assertEquals("completed", api.updateTodo(id, patch("status" to JsonPrimitive("completed"))).status)
        assertFalse(api.today().todos.any { it.id == id })
        assertEquals("新标题", api.updateTodo(id, patch("title" to JsonPrimitive("新标题"))).title)
        assertNull(api.updateTodo(id, patch("due_at" to JsonNull)).dueAt)
        assertEquals("deleted", api.updateTodo(id, patch("status" to JsonPrimitive("deleted"))).status)
        assertFalse(api.todos().items.any { it.id == id })
        expectError("conflict") { api.updateTodo(id, patch("status" to JsonPrimitive("pending"))) }
    }

    @Test
    fun runNowIsBusyThenSucceedsAndRejectsOverlap() = runTest {
        var now = testNow
        val api = MockBackend({ now }, testZone)
        assertEquals("extract_todos", api.runJob("extract_todos").jobId)
        assertEquals("running", api.runs().items.first().status)
        expectError("conflict") { api.runJob("extract_todos") }
        now = now.plusSeconds(5)
        assertEquals("succeeded", api.runs().items.first().status)
    }

    @Test
    fun pluginSwitchesCardsAndConfigurationPersist() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val p = api.plugins().first { it.state == "enabled" && it.provides.contains("cards") }
        assertTrue(api.cards(p.id).isNotEmpty())
        api.updatePlugin(p.id, patch("enabled" to JsonPrimitive(false)))
        assertTrue(api.cards(p.id).isEmpty())
        val config = buildJsonObject { put("custom", "kept") }
        assertEquals(config, api.updatePlugin(p.id, patch("config" to config)).config)
        api.updatePlugin(p.id, patch("enabled" to JsonPrimitive(true)))
        assertTrue(api.cards(p.id).isNotEmpty())
    }

    @Test
    fun deletingOnePluginLeavesOthersAlive() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val plugins = api.plugins()
        val removed = plugins.first()
        api.deletePlugin(removed.id)
        assertEquals(plugins.size - 1, api.plugins().size)
        assertTrue(api.status().online)
        expectError("not_found") { api.cards(removed.id) }
    }

    @Test
    fun settingsReportsDigestsAndPagination() = runTest {
        val api = MockBackend({ testNow }, testZone)
        assertTrue(api.healthReports().items.isNotEmpty())
        assertTrue(api.digests().items.isNotEmpty())
        val settings = api.settings()
        val edited = api.updateSettings(patch("source_cron" to JsonPrimitive("0 * * * *")))
        assertEquals("0 * * * *", edited.sourceCron)
        assertEquals(settings.harnessOrder, edited.harnessOrder)
        expectError("invalid_request") { api.updateSettings(patch("source_cron" to JsonPrimitive("bad"))) }
        val all = api.todos()
        val page = api.todos(limit = 1, offset = 1)
        assertEquals(all.total, page.total)
        assertEquals(all.items[1].id, page.items.single().id)
    }

    @Test
    fun healthCheckinsAreAcceptedOnlyOnce() = runTest {
        val api = MockBackend({ testNow }, testZone)
        val batch = ReadinessCheckin(7.0, 4).batch(testNow.atZone(testZone).toLocalDate(), testNow, testZone)
        assertEquals(HealthAccepted(2, 0), api.uploadHealth(batch))
        assertEquals(HealthAccepted(0, 2), api.uploadHealth(batch))
    }

    @Test
    fun offlineAndSourceChatAreRejected() = runTest {
        val api = MockBackend({ testNow }, testZone)
        expectError("not_found") {
            api.sendChat(
                "qq_screen",
                "你好",
                null,
                ChatChoice()
            )
        }
        api.online = false
        expectError("transport") { api.today() }
        api.online = true
        assertTrue(api.status().online)
    }

    @Test
    fun blankChatAndOverlappingRepliesAreRejected() = runTest {
        val api = MockBackend({ testNow }, testZone)
        expectError("invalid_request") {
            api.sendChat(
                "assistant",
                "  ",
                null,
                ChatChoice()
            )
        }
        api.sendChat(
            "assistant",
            "你好",
            null,
            ChatChoice()
        )
        expectError("conflict") {
            api.sendChat(
                "assistant",
                "再见",
                null,
                ChatChoice()
            )
        }
    }
}
