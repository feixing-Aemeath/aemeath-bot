package local.assistant.core

import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

class HealthSamplesTest {
    private val now = Instant.parse("2026-10-31T10:30:00Z")
    private fun sample(id: String = "record", value: Double = 64.0) =
        checkNotNull(HealthSamples.quantity(id, "heart_rate", value, "count/min", now, source = "watch"))

    @Test
    fun firstSyncUsesFourteenCalendarDaysAtTheSameTime() {
        assertEquals(Instant.parse("2026-10-17T10:30:00Z"), HealthSamples.firstStart(now, ZoneId.of("Asia/Shanghai")))
        val dst = Instant.parse("2026-03-15T12:00:00Z")
        assertEquals(Instant.parse("2026-03-01T13:00:00Z"), HealthSamples.firstStart(dst, ZoneId.of("America/New_York")))
    }

    @Test
    fun quantitiesPreserveEveryValueAndTimestamp() {
        val rows = listOf(sample("a", 50.0), sample("b", 170.0))
        assertEquals(listOf(50.0, 170.0), rows.map { it.value })
        assertTrue(rows.all { it.startAt == now.toString() && it.startAt == it.endAt })
        assertEquals("watch", rows.first().source)
    }

    @Test
    fun nonFiniteOrBackwardsQuantitiesAreOmitted() {
        assertNull(HealthSamples.quantity("a", "heart_rate", Double.NaN, "count/min", now, source = "watch"))
        assertNull(HealthSamples.quantity("a", "heart_rate", 60.0, "count/min", now, now.minusSeconds(1), "watch"))
    }

    @Test
    fun seriesIdsAreStableAndDistinctWithinARecord() {
        val a = HealthSamples.seriesId("record", "heart_rate", now, 0)
        assertEquals(a, HealthSamples.seriesId("record", "heart_rate", now, 0))
        assertNotEquals(a, HealthSamples.seriesId("record", "heart_rate", now, 1))
        assertNotEquals(a, HealthSamples.seriesId("other", "heart_rate", now, 0))
    }

    @Test
    fun eachWorkoutKeepsItsOwnIntervalAndFractionalMinutes() {
        val a = checkNotNull(HealthSamples.workout("a", "Running", now.minusSeconds(123), now, "watch"))
        val b = checkNotNull(HealthSamples.workout("b", "Running", now.minusSeconds(60), now, "watch"))
        assertEquals("workout:Running", a.type)
        assertEquals(2.05, a.value, 0.00001)
        assertEquals("min", a.unit)
        assertEquals(now.minusSeconds(123).toString(), a.startAt)
        assertNotEquals(a.id, b.id)
        assertNull(HealthSamples.workout("c", "Other", now, now, "watch"))
    }

    @Test
    fun sleepKeepsIndividualStages() {
        val start = now.minusSeconds(3600)
        val rows = HealthSamples.sleep("sleep", start, now, "watch", listOf(
            SleepStage("sleep_core", start, start.plusSeconds(1800)),
            SleepStage("sleep_rem", start.plusSeconds(1800), now)
        ))
        assertEquals(listOf("sleep_core", "sleep_rem"), rows.map { it.type })
        assertTrue(rows.all { it.value == 1.0 && it.unit.isEmpty() })
        assertNotEquals(rows[0].id, rows[1].id)
    }

    @Test
    fun onlyUnstagedSleepFallsBackToUnspecified() {
        val start = now.minusSeconds(3600)
        assertEquals("sleep_asleep", HealthSamples.sleep("s", start, now, "watch", emptyList()).single().type)
        assertTrue(HealthSamples.sleep("s", start, now, "watch", listOf(SleepStage("unknown", start, now))).isEmpty())
        assertTrue(HealthSamples.sleep("s", start, now, "watch", listOf(SleepStage("sleep_rem", start.minusSeconds(1), now))).isEmpty())
    }

    @Test
    fun batchesRespectTheBackendLimitAndRetainOrder() {
        val rows = (0..2000).map { sample(it.toString()) }
        val batches = HealthSamples.batches(rows)
        assertEquals(listOf(1000, 1000, 1), batches.map { it.samples.size })
        assertEquals(rows, batches.flatMap { it.samples })
        assertTrue(HealthSamples.batches(emptyList()).isEmpty())
    }

    @Test
    fun checkpointIsSavedAfterAllBatchesIncludingDuplicates() = runBlocking {
        var saved: String? = null
        var requests = 0
        val count = HealthSamples.uploadPage(List(1001) { sample(it.toString()) }, "next", {
            assertNull(saved)
            requests++
            HealthAccepted(0, it.samples.size)
        }, { saved = it })
        assertEquals(2, requests)
        assertEquals(0, count)
        assertEquals("next", saved)
    }

    @Test
    fun partialUploadFailureLeavesTheTokenUntouched() = runBlocking {
        var saved = "previous"
        var requests = 0
        try {
            HealthSamples.uploadPage(List(1001) { sample(it.toString()) }, "next", {
                requests++
                if (requests == 2) error("offline")
                HealthAccepted(it.samples.size, 0)
            }, { saved = it })
            fail("Expected upload failure")
        } catch (_: IllegalStateException) {
            assertEquals("previous", saved)
        }
    }

    @Test
    fun incompleteAcknowledgementDoesNotAdvanceToken() = runBlocking {
        var saved = "previous"
        try {
            HealthSamples.uploadPage(listOf(sample()), "next", { HealthAccepted(0, 0) }, { saved = it })
            fail("Expected incomplete acknowledgement")
        } catch (_: IllegalStateException) {
            assertEquals("previous", saved)
        }
    }

    @Test
    fun emptyChangesCanAdvanceWithoutAnEmptyHttpRequest() = runBlocking {
        var saved: String? = null
        HealthSamples.uploadPage(emptyList(), "next", { error("Unexpected upload") }, { saved = it })
        assertEquals("next", saved)
    }
}
