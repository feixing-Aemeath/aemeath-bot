package local.assistant.core

import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

class HealthIncrementalSyncTest {
    private val now = Instant.parse("2026-10-31T10:30:00Z")
    private val zone = ZoneId.of("Asia/Shanghai")
    private val sample = HealthSample("id", "heart_rate", 70.0, "count/min", now.toString(), now.toString(), "watch")

    @Test
    fun initialScanReservesTokenBeforeReadingAndDrainsConcurrentChanges() = runBlocking {
        val events = mutableListOf<String>()
        val saved = mutableListOf<String>()
        val count = HealthIncrementalSync.run(null, now, zone,
            newToken = {
                events += "reserve"
                "reserved"
            },
            initial = { start, end, cursor ->
                assertEquals(now.minusSeconds(14 * 86400), start)
                assertEquals(now, end)
                events += "history-$cursor"
                if (cursor == null) HealthInitialPage(listOf(sample), "page2") else HealthInitialPage(emptyList())
            },
            changes = { cursor ->
                assertEquals("reserved", cursor)
                assertEquals(listOf("reserved"), saved)
                events += "changes"
                HealthChangePage(listOf(sample.copy(id = "added-during-scan")), "done")
            },
            upload = { HealthAccepted(it.samples.size, 0) },
            saveToken = { saved += it }
        )
        assertEquals(listOf("reserve", "history-null", "history-page2", "changes"), events)
        assertEquals(listOf("reserved", "done"), saved)
        assertEquals(2, count)
    }

    @Test
    fun subsequentSyncOnlyRequestsChangesAndFollowsPagination() = runBlocking {
        val saved = mutableListOf<String>()
        HealthIncrementalSync.run("old", now, zone,
            newToken = { error("No new token for a healthy stream") },
            initial = { _, _, _ -> error("No history scan") },
            changes = { cursor ->
                when (cursor) {
                    "old" -> HealthChangePage(listOf(sample), "page2", hasMore = true)
                    "page2" -> HealthChangePage(emptyList(), "done")
                    else -> error("Unexpected cursor")
                }
            },
            upload = { HealthAccepted(it.samples.size, 0) },
            saveToken = { saved += it }
        )
        assertEquals(listOf("page2", "done"), saved)
    }

    @Test
    fun failedInitialScanNeverSavesItsReservedToken() = runBlocking {
        val saved = mutableListOf<String>()
        try {
            HealthIncrementalSync.run(null, now, zone,
                newToken = { "reserved" },
                initial = { _, _, cursor ->
                    if (cursor != null) error("read failed")
                    HealthInitialPage(listOf(sample), "page2")
                },
                changes = { error("History must finish first") },
                upload = { HealthAccepted(it.samples.size, 0) },
                saveToken = { saved += it }
            )
            fail("Expected read failure")
        } catch (_: IllegalStateException) {
            assertTrue(saved.isEmpty())
        }
    }

    @Test
    fun failedChangePageRetainsTheLastAcceptedPage() = runBlocking {
        var saved = "old"
        try {
            HealthIncrementalSync.run(saved, now, zone,
                newToken = { error("No reset") },
                initial = { _, _, _ -> error("No scan") },
                changes = { cursor ->
                    if (cursor == "old") HealthChangePage(listOf(sample), "page2", true)
                    else HealthChangePage(listOf(sample.copy(id = "failure")), "done")
                },
                upload = {
                    if (it.samples.single().id == "failure") error("offline")
                    HealthAccepted(it.samples.size, 0)
                },
                saveToken = { saved = it }
            )
            fail("Expected upload failure")
        } catch (_: IllegalStateException) {
            assertEquals("page2", saved)
        }
    }

    @Test
    fun expiredTokenReplaysThirtyDaysWithStableIds() = runBlocking {
        val saved = mutableListOf<String>()
        val count = HealthIncrementalSync.run("expired", now, zone,
            newToken = { "reserved" },
            initial = { start, _, _ ->
                assertEquals(now.minusSeconds(30 * 86400), start)
                HealthInitialPage(listOf(sample))
            },
            changes = {
                if (it == "expired") HealthChangePage(emptyList(), "invalid", expired = true)
                else HealthChangePage(emptyList(), "done")
            },
            upload = { HealthAccepted(0, it.samples.size) },
            saveToken = { saved += it }
        )
        assertEquals(0, count)
        assertEquals(listOf("reserved", "done"), saved)
    }

    @Test
    fun revokedPermissionDoesNotAdvanceCursor() = runBlocking {
        var saved = "old"
        try {
            HealthIncrementalSync.run(saved, now, zone,
                newToken = { error("No reset") },
                initial = { _, _, _ -> error("No scan") },
                changes = { throw SecurityException("revoked") },
                upload = { error("No upload") },
                saveToken = { saved = it }
            )
            fail("Expected permission failure")
        } catch (_: SecurityException) {
            assertEquals("old", saved)
        }
    }
}
