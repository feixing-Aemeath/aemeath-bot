package local.assistant.core

import java.time.*
import java.security.MessageDigest

data class ReadinessCheckin(val appleReadiness: Double? = null, val perceived: Int? = null) {
    val valid get() =
        (appleReadiness != null || perceived != null) &&
            (appleReadiness == null || appleReadiness.isFinite() && appleReadiness in 0.0..10.0) &&
            (perceived == null || perceived in 1..5)

    fun batch(day: LocalDate, now: Instant, zone: ZoneId): HealthBatch {
        require(valid)
        val today = now.atZone(zone)
        val at =
            if (day == today.toLocalDate())
                now
            else
                day.atTime(today.toLocalTime().withNano(0)).atZone(zone).toInstant()
        return HealthBatch(
            listOfNotNull(
                appleReadiness?.let {
                    checkinSample(
                        "apple_fitness_readiness",
                        it,
                        at,
                        now
                    )
                },
                perceived?.let {
                    checkinSample(
                        "perceived_readiness",
                        it.toDouble(),
                        at,
                        now
                    )
                },
            )
        )
    }

    private fun checkinSample(
        type: String,
        value: Double,
        at: Instant,
        now: Instant
    ) = HealthSample(
        "checkin-$type-${now.toEpochMilli()}",
        type,
        value,
        "",
        at.toString(),
        at.toString(),
        "24小时助手 App"
    )

    companion object {
        fun parseApple(text: String) = text.trim().replace(',', '.').replace('，', '.').toDoubleOrNull()?.takeIf {
            it.isFinite() && it in 0.0..10.0
        }
    }
}

// Legacy daily aggregation remains available to shared-core clients. Android uploads via HealthSamples.
data class DailyMetric(
    val day: LocalDate,
    val type: String,
    val value: Double,
    val unit: String,
    val source: String
)

data class MetricReading(val at: Instant, val value: Double, val source: String)

data class SleepStage(val type: String, val start: Instant, val end: Instant)

data class NightSleep(
    val id: String,
    val start: Instant,
    val end: Instant,
    val source: String,
    val stages: List<SleepStage>
)

object HealthAggregation {
    fun means(
        type: String,
        unit: String,
        readings: List<MetricReading>,
        zone: ZoneId
    ): List<DailyMetric> = readings.filter { it.value.isFinite() }
        .groupBy { it.at.atZone(zone).toLocalDate() to it.source }
        .map { (key, rows) ->
            DailyMetric(
                key.first,
                type,
                rows.map { it.value }.average(),
                unit,
                key.second
            )
        }

    fun stableId(vararg parts: String): String = "android-" + MessageDigest.getInstance("SHA-256")
        .digest(parts.joinToString("\u001f").toByteArray())
        .joinToString("") { "%02x".format(it.toInt() and 255) }

    // Only closed days: retries of a daily total never accumulate an unfinished day's values.
    fun batch(
        metrics: List<DailyMetric>,
        nights: List<NightSleep>,
        now: Instant,
        zone: ZoneId
    ): HealthBatch {
        val today = now.atZone(zone).toLocalDate()
        val daily = metrics.filter { it.day < today && it.value.isFinite() }
            .map {
                HealthSample(
                    stableId(it.day.toString(), it.type, it.source),
                    it.type,
                    it.value,
                    it.unit,
                    it.day.atStartOfDay(zone).toInstant().toString(),
                    it.day.plusDays(1).atStartOfDay(zone).toInstant().toString(),
                    it.source
                )
            }
        val sleep = nights.filter { it.end <= now && it.end > it.start }
            .flatMap { night ->
                val valid = night.stages.filter {
                    it.type in sleepTypes && it.end > it.start && it.start >= night.start && it.end <= night.end
                }
                val stages =
                    if (night.stages.isEmpty()) listOf(SleepStage("sleep_asleep", night.start, night.end)) else valid
                stages.map {
                    HealthSample(
                        stableId(
                            night.source,
                            night.id,
                            it.type,
                            it.start.toString(),
                            it.end.toString()
                        ),
                        it.type,
                        1.0,
                        "",
                        it.start.toString(),
                        it.end.toString(),
                        night.source
                    )
                }
            }
        return HealthBatch((daily + sleep).distinctBy { it.id })
    }

    val sleepTypes = setOf(
        "sleep_in_bed",
        "sleep_asleep",
        "sleep_awake",
        "sleep_core",
        "sleep_deep",
        "sleep_rem"
    )
}
