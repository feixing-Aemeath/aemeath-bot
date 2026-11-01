package local.assistant.core

import kotlinx.serialization.json.*
import org.junit.Assert.*
import java.time.*

fun fixture(name: String) =
    checkNotNull(object {}.javaClass.getResourceAsStream("/fixtures/$name.json")).bufferedReader().use { it.readText() }

inline fun <reified T> fromFixture(name: String): T = Wire.decodeFromString(fixture(name))

val testZone: ZoneId = ZoneId.of("Asia/Shanghai")

val testNow: Instant = instant("2026-10-15T21:00:00+08:00")

fun testTodo(id: String = "t", plugin: String = "qq_screen", created: String = testNow.toString()) = Todo(
    id,
    "待办$id",
    sourcePlugin = plugin,
    createdAt = created
)

fun testRun(
    job: String = "extract_todos",
    status: String = "succeeded",
    ago: Long = 600,
    error: String? = null,
    seconds: Long? = 30
) = Run(
    "r-$job",
    job,
    "claude_code",
    testNow.minusSeconds(ago).toString(),
    seconds?.let { testNow.minusSeconds(ago).plusSeconds(it).toString() },
    status,
    error
)

suspend fun expectError(category: String, block: suspend () -> Unit) {
    try {
        block()
        fail("expected $category")
    } catch (e: APIError) {
        assertEquals(category, e.category)
    }
}

fun patch(vararg fields: Pair<String, JsonElement>) = JsonObject(fields.toMap())
