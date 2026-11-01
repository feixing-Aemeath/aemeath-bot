package local.assistant.core

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import java.time.*

class HealthTest {
    @Test
    fun meansGroupByLocalDayAndSource() {
        val readings = listOf(
            MetricReading(instant("2026-10-14T15:59:00Z"), 50.0, "watch"),
            MetricReading(instant("2026-10-14T15:30:00Z"), 70.0, "watch"),
            MetricReading(instant("2026-10-14T16:01:00Z"), 90.0, "watch"),
            MetricReading(instant("2026-10-14T15:30:00Z"), 55.0, "phone")
        )
        val means = HealthAggregation.means(
            "heart_rate",
            "count/min",
            readings,
            testZone
        )
        assertEquals(3, means.size)
        assertEquals(60.0, means.first { it.day.dayOfMonth == 14 && it.source == "watch" }.value, 0.0)
        assertEquals(55.0, means.first { it.source == "phone" }.value, 0.0)
    }

    @Test
    fun nonFiniteReadingsDoNotContaminateDailyMeans() {
        assertEquals(
            55.0,
            HealthAggregation.means(
                "heart_rate",
                "count/min",
                listOf(
                    MetricReading(testNow, 55.0, "watch"),
                    MetricReading(testNow, Double.NaN, "watch"),
                    MetricReading(testNow, Double.POSITIVE_INFINITY, "watch")
                ),
                testZone
            )
                .single()
                .value,
            0.0
        )
    }

    @Test
    fun invalidRecordedStagesDoNotInventSleep() {
        val night = NightSleep(
            "s",
            testNow.minusSeconds(3600),
            testNow,
            "watch",
            listOf(SleepStage("unknown", testNow.minusSeconds(3600), testNow))
        )
        assertTrue(
            HealthAggregation.batch(
                emptyList(),
                listOf(night),
                testNow,
                testZone
            ).samples.isEmpty()
        )
    }

    @Test
    fun readinessParsingMatchesPhone() {
        listOf(
            "7.5",
            "7,5",
            "7，5",
            " 7.5 "
        ).forEach { assertEquals(7.5, ReadinessCheckin.parseApple(it)!!, 0.0) }
        listOf(
            "",
            "abc",
            "11",
            "-1",
            "NaN",
            "Infinity"
        ).forEach { assertNull(ReadinessCheckin.parseApple(it)) }
    }

    @Test
    fun readinessRequiresAtLeastOneValidField() {
        assertFalse(ReadinessCheckin().valid)
        assertTrue(ReadinessCheckin(0.0).valid)
        assertTrue(ReadinessCheckin(10.0, 5).valid)
        assertTrue(ReadinessCheckin(perceived = 1).valid)
        assertFalse(ReadinessCheckin(Double.NaN).valid)
        assertFalse(ReadinessCheckin(perceived = 0).valid)
        assertFalse(ReadinessCheckin(7.0, 6).valid)
    }

    @Test
    fun checkinsUseTodayOrSameLocalTimeOnAnEarlierDay() {
        val checkin = ReadinessCheckin(7.5, 4)
        val today = checkin.batch(LocalDate.of(2026, 10, 15), testNow, testZone).samples
        assertEquals(listOf("apple_fitness_readiness", "perceived_readiness"), today.map { it.type })
        assertTrue(today.all { instant(it.startAt) == testNow })
        val previous = checkin.batch(LocalDate.of(2026, 10, 14), testNow, testZone).samples
        assertTrue(previous.all { instant(it.startAt) == testNow.minusSeconds(86400) && it.startAt == it.endAt })
        assertEquals("24小时助手 App", previous[0].source)
    }

    @Test
    fun uploadsUseTheExactIPhoneShape() {
        val batch = ReadinessCheckin(6.0).batch(LocalDate.of(2026, 10, 15), testNow, testZone)
        val root = Wire.encodeToJsonElement(batch).obj()
        assertEquals(setOf("samples"), root.keys)
        assertEquals(
            setOf(
                "id",
                "type",
                "value",
                "unit",
                "start_at",
                "end_at",
                "source"
            ),
            root["samples"].array().single().obj().keys
        )
    }

    private val yesterday = LocalDate.of(2026, 10, 14)

    private fun metric(day: LocalDate = yesterday, type: String = "step_count", value: Double = 1000.0) = DailyMetric(
        day,
        type,
        value,
        "count",
        "Health Connect Android 手机"
    )

    @Test
    fun dailyTotalsOnlyUploadClosedDaysAndFiniteNumbers() {
        val batch = HealthAggregation.batch(
            listOf(metric(), metric(yesterday.plusDays(1)), metric(type = "invalid", value = Double.NaN)),
            emptyList(),
            testNow,
            testZone
        )
        assertEquals(1, batch.samples.size)
        val s = batch.samples.single()
        assertEquals(1000.0, s.value, 0.0)
        assertEquals(instant("2026-10-14T00:00:00+08:00"), instant(s.startAt))
        assertEquals(instant("2026-10-15T00:00:00+08:00"), instant(s.endAt))
    }

    @Test
    fun stableIdsMakeRetriesAndDuplicateReadingsIdempotent() {
        val a = HealthAggregation.batch(
            listOf(metric(), metric()),
            emptyList(),
            testNow,
            testZone
        )
        val b = HealthAggregation.batch(
            listOf(metric(value = 1200.0)),
            emptyList(),
            testNow.plusSeconds(60),
            testZone
        )
        assertEquals(1, a.samples.size)
        assertEquals(a.samples.single().id, b.samples.single().id)
        assertTrue(a.samples.single().id.matches(Regex("android-[0-9a-f]{64}")))
        assertNotEquals(HealthAggregation.stableId("ab", "c"), HealthAggregation.stableId("a", "bc"))
    }

    @Test
    fun daylightSavingDaysUseActualMidnightBoundaries() {
        val zone = ZoneId.of("America/New_York")
        val day = LocalDate.of(2026, 3, 8)
        val s = HealthAggregation.batch(
            listOf(metric(day)),
            emptyList(),
            Instant.parse("2026-03-10T12:00:00Z"),
            zone
        ).samples.single()
        assertEquals(23, Duration.between(instant(s.startAt), instant(s.endAt)).toHours().toInt())
    }

    @Test
    fun sleepUploadsIntervalsAndNeverRawHeartRate() {
        val start = testNow.minusSeconds(12 * 3600)
        val end = start.plusSeconds(8 * 3600)
        val night = NightSleep(
            "session",
            start,
            end,
            "手表",
            listOf(
                SleepStage("sleep_core", start, start.plusSeconds(3600)),
                SleepStage("sleep_rem", start.plusSeconds(3600), end)
            )
        )
        val samples = HealthAggregation.batch(
            emptyList(),
            listOf(night, night),
            testNow,
            testZone
        ).samples
        assertEquals(2, samples.size)
        assertEquals(listOf("sleep_core", "sleep_rem"), samples.map { it.type })
        assertTrue(samples.all { it.value == 1.0 && it.unit.isEmpty() })
    }

    @Test
    fun incompleteNightsAndInvalidIntervalsAreExcluded() {
        val night = NightSleep(
            "s",
            testNow.minusSeconds(60),
            testNow.plusSeconds(60),
            "watch",
            emptyList()
        )
        assertTrue(
            HealthAggregation.batch(
                emptyList(),
                listOf(night),
                testNow,
                testZone
            ).samples.isEmpty()
        )
        assertTrue(
            HealthAggregation.batch(
                emptyList(),
                listOf(night.copy(end = night.start)),
                testNow,
                testZone
            ).samples.isEmpty()
        )
    }

    @Test
    fun unclassifiedCompletedSleepIsExplicitlyUnspecified() {
        val night = NightSleep(
            "s",
            testNow.minusSeconds(3600),
            testNow,
            "watch",
            emptyList()
        )
        assertEquals(
            "sleep_asleep",
            HealthAggregation.batch(
                emptyList(),
                listOf(night),
                testNow,
                testZone
            ).samples.single().type
        )
    }

    @Test
    fun independentSourcesKeepDistinctIdsAndRmssdIsNotSdnn() {
        val samples = HealthAggregation.batch(
            listOf(metric(type = "hrv_rmssd", value = 42.0), metric(type = "hrv_rmssd").copy(source = "other")),
            emptyList(),
            testNow,
            testZone
        )
            .samples
        assertEquals(2, samples.size)
        assertTrue(samples.all { it.type == "hrv_rmssd" })
        assertNotEquals(samples[0].id, samples[1].id)
    }
}
