package local.assistant.core

import org.junit.Test
import org.junit.Assert.*
import kotlinx.serialization.json.*
import java.time.*

class ModelsTest {
    @Test
    fun todoFromBackend() {
        val t = fromFixture<Todo>("todo")
        assertEquals("完成信息核对", t.title)
        assertEquals(instant("2026-10-16T17:00:00+08:00"), instant(t.dueAt!!))
        assertEquals(.93, t.confidence, 1e-9)
        assertEquals("pending", t.syncStatus)
        assertNull(t.reminderId)
    }

    @Test
    fun pageOptionalFields() {
        val p = fromFixture<Page<Todo>>("todoPage")
        assertEquals(2, p.total)
        assertNull(p.items[1].dueAt)
        assertEquals("completed", p.items[1].status)
        assertEquals("fake-reminders-t2", p.items[1].reminderId)
    }

    @Test
    fun statusFromBackend() {
        val s = fromFixture<Status>("status")
        assertTrue(s.online)
        assertEquals(false, s.agents["codex"]?.available)
        assertEquals("quota_exhausted", s.lastRun?.error)
        assertEquals(2, s.pendingSync)
    }

    @Test
    fun todayKeepsUnknownCard() {
        val t = fromFixture<Today>("today")
        assertEquals("2026-10-16", t.date)
        assertEquals("sparkline", t.cards[1].type)
        assertTrue(t.cards[1].content is CardContent.Unsupported)
    }

    @Test
    fun digestFromBackend() {
        assertEquals("2026-10-15", fromFixture<Page<Digest>>("digests").items[0].date)
    }

    @Test
    fun nullableRunFields() {
        val r = fromFixture<Page<Run>>("runs").items[0]
        assertNull(r.harness)
        assertNull(r.finishedAt)
        assertEquals(2, r.attempt)
    }

    @Test
    fun runtimeSettings() {
        val s = fromFixture<RuntimeSettings>("settings")
        assertEquals(listOf("claude_code", "codex"), s.harnessOrder)
        assertEquals(2.0, s.retryBaseSeconds, 0.0)
    }

    @Test
    fun pluginSchemaAndNullSticker() {
        val p = fromFixture<List<PluginView>>("plugins").first()
        assertEquals("array", p.configSchema["properties"].obj()["whitelist"].obj()["type"].text())
        assertNull(p.mascot?.sticker)
        assertEquals("#22B8CF", p.mascot?.accent)
    }

    @Test
    fun acceptedShapes() {
        assertEquals("memory_index", fromFixture<JobAccepted>("jobAccepted").jobId)
        assertEquals(1, fromFixture<HealthAccepted>("healthAccepted").accepted)
    }

    @Test
    fun timestampsAndOffsets() {
        assertEquals(instant("2026-10-09T09:00:00Z"), instant("2026-10-09T17:00:00+08:00"))
        assertEquals(453973000, instant("2026-10-09T09:00:00.453973Z").nano)
        assertEquals(400000000, instant("2026-10-09T09:00:00.4Z").nano)
    }

    @Test
    fun rejectsDateOnlyTimestamp() {
        assertThrows(java.time.format.DateTimeParseException::class.java) { instant("2026-10-09") }
    }

    @Test
    fun ignoreNewResponseFields() {
        val text = fixture("status").trim().dropLast(1) + ",\"new_field\":true}"
        assertTrue(Wire.decodeFromString<Status>(text).online)
    }

    @Test
    fun reportsDecodeNewlinesAndNulls() {
        val r = fromFixture<Page<HealthReport>>("healthReports")
        assertTrue(r.items[0].body!!.contains("\n\n- 静息心率 58"))
        assertNull(r.items[1].title)
        assertEquals("report_not_written", r.items[1].error)
    }

    @Test
    fun oldAgentModelsDefaultToEmpty() {
        val a = Wire.decodeFromString<AgentStatus>("""{"available":true}""")
        assertEquals(emptyList<AgentModel>(), a.models)
        assertEquals("unchecked", a.authentication)
    }

    @Test
    fun availableAgentOptions() {
        val s = fromFixture<Status>("status")
            .copy(
                agents = mapOf(
                    "claude_code" to AgentStatus(true, models = listOf(AgentModel("opus", "Opus"))),
                    "codex" to AgentStatus(true)
                )
            )
        assertEquals(listOf("", "claude_code/opus", "codex"), ChatOption.build(s).map { it.choice.stored })
        assertEquals("Codex", ChatOption.build(s).last().short)
    }

    @Test
    fun unavailableAgentOmitted() {
        val s = fromFixture<Status>("status").copy(agents = mapOf("codex" to AgentStatus(false)))
        assertEquals(1, ChatOption.build(s).size)
        assertEquals(listOf("自动"), ChatOption.build(null).map { it.short })
    }

    @Test
    fun modelChoiceRoundTrip() {
        listOf(
            "",
            "codex",
            "claude_code/opus",
            "codex/model/variant"
        ).forEach { assertEquals(it, ChatChoice.restore(it).stored) }
        assertEquals(ChatChoice(), ChatChoice.restore(null))
    }

    @Test
    fun modelFallbackLabel() {
        assertEquals("gpt-x", ChatOption.label(ChatChoice("codex", "gpt-x"), emptyList()))
        assertEquals("自动", ChatOption.label(ChatChoice(), emptyList()))
    }

    @Test
    fun chatPayloadOmitsAutomaticFields() {
        assertEquals(setOf("text"), ChatChoice().body("在吗").keys)
        assertEquals(
            setOf(
                "text",
                "bot_name",
                "harness",
                "model"
            ),
            ChatChoice("claude_code", "opus").body("在吗", "作业Bot").keys
        )
    }

    @Test
    fun unchangedTodoDraftSendsNothing() {
        val t = fromFixture<Todo>("todo")
        assertTrue(TodoDraft.from(t, zone = testZone).patch(t, testZone).isEmpty())
    }

    @Test
    fun trimmedTitleAndEmptyValidation() {
        val t = fromFixture<Todo>("todo")
        val d = TodoDraft.from(t, zone = testZone)
        assertEquals(patch("title" to JsonPrimitive("改")), d.copy(title = " 改 ").patch(t, testZone))
        assertFalse(d.copy(title = "  ").valid)
    }

    @Test
    fun clearingDeadlineSendsExplicitNullOnly() {
        val t = fromFixture<Todo>("todo")
        assertEquals(
            patch("due_at" to JsonNull),
            TodoDraft.from(t, zone = testZone).copy(hasDue = false).patch(t, testZone)
        )
    }

    @Test
    fun dateOnlyDeadlineNormalizesLocalMidnight() {
        val t = fromFixture<Todo>("todo")
        val p = TodoDraft.from(t, zone = testZone).copy(hasTime = false).patch(t, testZone)
        assertEquals(instant("2026-10-16T00:00:00+08:00"), instant(p["due_at"].text()!!))
        assertEquals(false, p["has_time"].flag())
    }

    @Test
    fun noDeadlineDraftSuggestsTodayAtEighteen() {
        val d = TodoDraft.from(fromFixture("todoWithoutDue"), testNow, testZone)
        assertFalse(d.hasDue)
        assertEquals(
            LocalDateTime.of(
                2026,
                10,
                15,
                18,
                0
            ),
            d.due
        )
    }

    @Test
    fun addingTimedDeadlineSendsBothFields() {
        val t = fromFixture<Todo>("todoWithoutDue")
        val p = TodoDraft.from(t, testNow, testZone).copy(hasDue = true, hasTime = true).patch(t, testZone)
        assertEquals(2, p.size)
        assertTrue(p["has_time"].flag()!!)
    }
}
