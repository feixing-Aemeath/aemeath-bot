package local.assistant.core

import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.*
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit
import java.util.concurrent.CountDownLatch
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.OkHttpClient
import okhttp3.Response
import java.io.IOException

class BackendTest {
    @Test
    fun todosFollowTotalEvenWhenTheServerReturnsShortPages() = runTest {
        withServer { s, api ->
            val old = testTodo("old").copy(status = "completed")
            val future = testTodo("future").copy(dueAt = "2027-01-01T00:00:00Z")
            s.enqueue(MockResponse().setBody(Wire.encodeToJsonElement(Page(listOf(old), 2)).toString()))
            s.enqueue(MockResponse().setBody(Wire.encodeToJsonElement(Page(listOf(future), 2)).toString()))
            assertEquals(listOf(old, future), api.allTodos())
            assertEquals("0", s.takeRequest().requestUrl!!.queryParameter("offset"))
            assertEquals("1", s.takeRequest().requestUrl!!.queryParameter("offset"))
        }
    }

    @Test
    fun emptyTodoPageBeforeTotalFailsInsteadOfLooping() = runTest {
        withServer { s, api ->
            s.enqueue(MockResponse().setBody(Wire.encodeToJsonElement(Page(listOf(testTodo()), 2)).toString()))
            s.enqueue(MockResponse().setBody("""{"items":[],"total":2}"""))
            expectError("decoding") { api.allTodos() }
            assertEquals(2, s.requestCount)
        }
    }

    @Test
    fun cancelledRowsStillAdvanceTodoPaginationOffset() = runTest {
        withServer { s, api ->
            val cancelled = testTodo("cancelled").copy(status = "cancelled")
            val pending = testTodo("pending")
            s.enqueue(MockResponse().setBody(Wire.encodeToJsonElement(Page(listOf(cancelled), 2)).toString()))
            s.enqueue(MockResponse().setBody(Wire.encodeToJsonElement(Page(listOf(pending), 2)).toString()))
            assertEquals(listOf(pending), api.allTodos())
            s.takeRequest()
            assertEquals("1", s.takeRequest().requestUrl!!.queryParameter("offset"))
        }
    }

    private fun cancellationClosesCall(original: Boolean) = runBlocking {
        withServer { s, _ ->
            val ended = CountDownLatch(1)
            // OkHttp reports responseBodyStart after the first read returns, which a stalled body
            // never does, so the headers are the sign that the download has reached the stall.
            val headersRead = CountDownLatch(1)
            val client = OkHttpClient.Builder().readTimeout(5, TimeUnit.SECONDS)
                .eventListener(object : EventListener() {
                    override fun callFailed(call: Call, ioe: IOException) { ended.countDown() }
                    override fun callEnd(call: Call) { ended.countDown() }
                    override fun responseHeadersEnd(call: Call, response: Response) { headersRead.countDown() }
                }).build()
            val api = LiveBackend(s.url("/").toString(), { "synthetic-token" }, client)
            s.enqueue(if (original) MockResponse().setBody("pdf").setBodyDelay(5, TimeUnit.SECONDS)
                else MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            val reading = launch(Dispatchers.Default) {
                if (original) api.copyOriginal("synthetic.pdf", ByteArrayOutputStream()) else api.status()
                fail("Cancelled requests must not succeed")
            }
            try {
                assertNotNull(s.takeRequest(2, TimeUnit.SECONDS))
                if (original) assertTrue(headersRead.await(2, TimeUnit.SECONDS))
                reading.cancel()
                withTimeout(1_500) { reading.join() }
                assertTrue("Cancellation must close the HTTP call", ended.await(1_500, TimeUnit.MILLISECONDS))
            } finally {
                reading.cancelAndJoin()
                client.dispatcher.executorService.shutdown()
                client.connectionPool.evictAll()
            }
        }
    }

    @Test
    fun cancellationClosesARequestWaitingForHeaders() = cancellationClosesCall(original = false)

    @Test
    fun cancellationClosesAStalledOriginalDownload() = cancellationClosesCall(original = true)

    private suspend fun withServer(block: suspend (MockWebServer, LiveBackend) -> Unit) {
        val server = MockWebServer()
        server.start()
        try {
            block(server, LiveBackend(server.url("/proxy/").toString(), { "test-secret" }))
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun bearerTokenBasePathAndDecoding() = runTest {
        withServer { s, api ->
            s.enqueue(MockResponse().setBody(fixture("status")))
            assertTrue(api.status().online)
            val req = s.takeRequest()
            assertEquals("/proxy/api/v1/status", req.path)
            assertEquals("Bearer test-secret", req.getHeader("Authorization"))
            assertNull(req.getHeader("Cookie"))
        }
    }

    @Test
    fun standardGetEndpointsUseDocumentedRoutes() = runTest {
        withServer { s, api ->
            suspend fun check(body: String, path: String, call: suspend () -> Unit) {
                s.enqueue(MockResponse().setBody(body))
                call()
                assertEquals(path, s.takeRequest().path)
            }
            check(fixture("today"), "/proxy/api/v1/today") { api.today() }
            check(fixture("todoPage"), "/proxy/api/v1/todos?limit=12&offset=3&status=pending") {
                api.todos("pending", 12, 3)
            }
            check(fixture("digests"), "/proxy/api/v1/digests?limit=200&offset=0") { api.digests() }
            check(fixture("runs"), "/proxy/api/v1/runs?limit=200&offset=0") { api.runs() }
            check(fixture("plugins"), "/proxy/api/v1/plugins") { api.plugins() }
            check("[]", "/proxy/api/v1/plugins/health/cards") { api.cards("health") }
            check(fixture("settings"), "/proxy/api/v1/settings") { api.settings() }
            check(fixture("healthReports"), "/proxy/api/v1/health/reports?limit=100&offset=0") { api.healthReports() }
            check("""{"items":[],"total":0}""", "/proxy/api/v1/bots/assistant/messages?limit=200&offset=0") {
                api.chat("assistant")
            }
        }
    }

    @Test
    fun patchesJobsAndHealthBodiesHaveNoUnsetFields() = runTest {
        withServer { s, api ->
            s.enqueue(MockResponse().setBody(fixture("todo")))
            api.updateTodo("id", patch("status" to JsonPrimitive("completed")))
            s.takeRequest()
                .let {
                    assertEquals("PATCH", it.method)
                    assertEquals("/proxy/api/v1/todos/id", it.path)
                    assertEquals("""{"status":"completed"}""", it.body.readUtf8())
                }
            s.enqueue(MockResponse().setBody(fixture("plugin")))
            api.updatePlugin("qq_screen", patch("enabled" to JsonPrimitive(false)))
            assertEquals("PATCH", s.takeRequest().method)
            s.enqueue(MockResponse().setResponseCode(204))
            api.deletePlugin("qq_screen")
            assertEquals("DELETE", s.takeRequest().method)
            s.enqueue(MockResponse().setBody(fixture("settings")))
            api.updateSettings(patch("source_cron" to JsonPrimitive("0 * * * *")))
            assertEquals("PATCH", s.takeRequest().method)
            s.enqueue(MockResponse().setBody(fixture("jobAccepted")))
            api.runJob("plugin:health:morning_report")
            assertEquals("/proxy/api/v1/jobs/plugin:health:morning_report/run", s.takeRequest().path)
            s.enqueue(MockResponse().setBody(fixture("healthAccepted")))
            api.uploadHealth(HealthBatch(emptyList()))
            assertEquals("""{"samples":[]}""", s.takeRequest().body.readUtf8())
        }
    }

    @Test
    fun knowledgeReadRoutesEscapeQueries() = runTest {
        withServer { s, api ->
            s.enqueue(MockResponse().setBody(fixture("knowledge-overview")))
            api.knowledge()
            assertEquals("/proxy/api/v1/knowledge", s.takeRequest().path)
            s.enqueue(MockResponse().setBody("""{"path":"知识库/数学","entries":[]}"""))
            api.knowledgeFiles("知识库/数学")
            s.takeRequest().requestUrl!!.let {
                assertEquals("知识库/数学", it.queryParameter("path"))
                assertEquals("/proxy/api/v1/knowledge/files", it.encodedPath)
            }
            s.enqueue(MockResponse().setBody(fixture("knowledge-note")))
            api.knowledgeFile("知识库/数学/整理/笔记.md")
            assertEquals("知识库/数学/整理/笔记.md", s.takeRequest().requestUrl!!.queryParameter("path"))
            s.enqueue(MockResponse().setBody("""{"results":[]}"""))
            api.searchKnowledge("期限 & 复习")
            assertEquals("期限 & 复习", s.takeRequest().requestUrl!!.queryParameter("q"))
        }
    }

    @Test
    fun knowledgeWritesAndBinaryUploadUseDocumentedRoutes() = runTest {
        withServer { s, api ->
            val area = """{"path":"知识库/数学","name":"数学"}"""
            s.enqueue(MockResponse().setBody(area))
            api.setReaders("知识库/数学", listOf("知识库Bot"))
            s.takeRequest().let {
                assertEquals("/proxy/api/v1/knowledge/access", it.path)
                assertEquals("PATCH", it.method)
            }
            s.enqueue(MockResponse().setBody(area))
            api.createTopic("数学")
            assertEquals("""{"name":"数学"}""", s.takeRequest().body.readUtf8())
            s.enqueue(MockResponse().setBody(fixture("knowledge-overview")))
            api.updateKnowledge(patch("agents" to JsonArray(emptyList())))
            assertEquals("/proxy/api/v1/knowledge", s.takeRequest().path)
            s.enqueue(MockResponse().setBody("""{"path":"x","name":"x","title":"x","kind":"source","format":"pdf"}"""))
            api.uploadKnowledge("知识库/数学/a.pdf", "pdf-bytes".toByteArray(), "application/pdf")
            s.takeRequest()
                .let {
                    assertEquals("POST", it.method)
                    assertEquals("application/pdf", it.getHeader("Content-Type"))
                    assertEquals("pdf-bytes", it.body.readUtf8())
                    assertEquals("知识库/数学/a.pdf", it.requestUrl!!.queryParameter("path"))
                }
        }
    }

    @Test
    fun originalDownloadStreamsWithBearerAndNoRedirect() = runTest {
        withServer { s, api ->
            s.enqueue(MockResponse().setBody("%PDF-original"))
            val out = ByteArrayOutputStream()
            api.copyOriginal("知识库/a.pdf", out)
            assertEquals("%PDF-original", out.toString("UTF-8"))
            assertEquals("Bearer test-secret", s.takeRequest().getHeader("Authorization"))
            s.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "http://example.invalid/private"))
            expectError("server") { api.original("a") }
            s.takeRequest()
            assertEquals(2, s.requestCount)
        }
    }

    @Test
    fun missingTokenSendsNoRequest() = runTest {
        withServer { s, _ ->
            val api = LiveBackend(s.url("/").toString(), { " " })
            expectError("missing_token") { api.status() }
            assertNull(s.takeRequest(30, TimeUnit.MILLISECONDS))
        }
    }

    @Test
    fun httpErrorsNeverExposeServerBodiesOrToken() = runTest {
        withServer { s, api ->
            mapOf(
                401 to "unauthorized",
                403 to "forbidden",
                404 to "not_found",
                409 to "conflict",
                422 to "invalid_request",
                413 to "invalid_request",
                500 to "server"
            )
                .forEach { (status, category) ->
                    s.enqueue(MockResponse().setResponseCode(status).setBody("private chat test-secret"))
                    expectError(category) { api.status() }
                    s.takeRequest()
                }
        }
    }

    @Test
    fun malformedJsonMapsToDecodingError() = runTest {
        withServer { s, api ->
            s.enqueue(MockResponse().setBody("not json"))
            expectError("decoding") { api.status() }
        }
    }

    @Test
    fun chatPostPreservesExplicitAgentAndModel() = runTest {
        withServer { s, api ->
            val me = ChatMessage(
                "m",
                "assistant",
                "me",
                "你好",
                "ready",
                createdAt = testNow.toString(),
                updatedAt = testNow.toString()
            )
            val reply = me.copy(
                id = "r",
                author = "bot",
                status = "pending",
                text = ""
            )
            s.enqueue(MockResponse().setBody(Wire.encodeToJsonElement(ChatAccepted(me, reply)).toString()))
            assertEquals(
                "pending",
                api.sendChat(
                    "assistant",
                    "你好",
                    "助理Bot",
                    ChatChoice("claude_code", "opus")
                ).reply.status
            )
            val request = s.takeRequest()
            assertEquals("POST", request.method)
            assertEquals("/proxy/api/v1/bots/assistant/messages", request.path)
            val body = Wire.parseToJsonElement(request.body.readUtf8()).obj()
            assertEquals("opus", body["model"].text())
            assertEquals("claude_code", body["harness"].text())
            assertEquals("助理Bot", body["bot_name"].text())
        }
    }

    @Test
    fun idsAreAlwaysSingleEncodedSegments() {
        val api = LiveBackend("http://localhost:8765/base/", { "x" })
        val url = api.url(listOf("todos", "a/b ?#%"), mapOf("q" to "a & b"))
        assertEquals(
            listOf(
                "base",
                "api",
                "v1",
                "todos",
                "a/b ?#%"
            ),
            url.pathSegments
        )
        assertEquals("a & b", url.queryParameter("q"))
    }

    @Test
    fun urlsWithEmbeddedCredentialsOrQueriesAreRejected() {
        listOf(
            "invalid",
            "http://name:secret@localhost/",
            "http://localhost/?token=secret",
            "http://localhost/#x"
        )
            .forEach { url ->
                try {
                    LiveBackend(url, { "x" })
                    fail(url)
                } catch (e: APIError) {
                    assertEquals("invalid_request", e.category)
                }
            }
    }
}
