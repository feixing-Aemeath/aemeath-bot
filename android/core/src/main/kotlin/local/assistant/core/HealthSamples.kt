package local.assistant.core

import java.time.Instant
import java.time.ZoneId

/** Sample-level wire mapping; the older daily aggregation API remains available to desktop. */
object HealthSamples {
    const val FIRST_DAYS = 14L
    const val BATCH_SIZE = 1000

    fun firstStart(now: Instant, zone: ZoneId): Instant = now.atZone(zone).minusDays(FIRST_DAYS).toInstant()

    fun quantity(
        id: String,
        type: String,
        value: Double,
        unit: String,
        start: Instant,
        end: Instant = start,
        source: String
    ): HealthSample? = if (value.isFinite() && end >= start) {
        HealthSample(id, type, value, unit, start.toString(), end.toString(), source)
    } else null

    fun seriesId(recordId: String, type: String, at: Instant, index: Int): String =
        HealthAggregation.stableId("sample", recordId, type, at.toString(), index.toString())

    fun sleep(id: String, start: Instant, end: Instant, source: String, stages: List<SleepStage>): List<HealthSample> {
        if (end <= start) return emptyList()
        val recorded = if (stages.isEmpty()) listOf(SleepStage("sleep_asleep", start, end)) else stages
        return recorded.mapIndexedNotNull { index, stage ->
            if (stage.type !in HealthAggregation.sleepTypes || stage.start < start || stage.end > end ||
                stage.end <= stage.start
            ) return@mapIndexedNotNull null
            quantity(
                seriesId(id, stage.type, stage.start, index), stage.type, 1.0, "",
                stage.start, stage.end, source
            )
        }
    }

    fun workout(
        id: String,
        activity: String,
        start: Instant,
        end: Instant,
        source: String,
        durationMinutes: Double = java.time.Duration.between(start, end).toMillis() / 60000.0
    ): HealthSample? = if (end > start && durationMinutes >= 0) {
        quantity(id, "workout:$activity", durationMinutes, "min", start, end, source)
    } else null

    fun batches(samples: List<HealthSample>): List<HealthBatch> = samples.chunked(BATCH_SIZE).map(::HealthBatch)

    /** A partially uploaded page is retried with the same IDs; never checkpoint it early. */
    suspend fun uploadPage(
        samples: List<HealthSample>,
        nextToken: String?,
        upload: suspend (HealthBatch) -> HealthAccepted,
        saveToken: (String) -> Unit
    ): Int {
        var count = 0
        for (batch in batches(samples)) {
            val result = upload(batch)
            check(result.accepted >= 0 && result.duplicates >= 0 &&
                result.accepted + result.duplicates == batch.samples.size
            ) { "Backend did not acknowledge the complete health batch" }
            count += result.accepted
        }
        if (nextToken != null) saveToken(nextToken)
        return count
    }
}
