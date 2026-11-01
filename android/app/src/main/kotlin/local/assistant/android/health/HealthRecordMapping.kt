package local.assistant.android.health

import androidx.health.connect.client.records.*
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.metadata.Device
import androidx.health.connect.client.records.metadata.Metadata
import local.assistant.core.*
import java.time.Instant
import java.time.Duration

object HealthRecordMapping {
    private fun source(meta: Metadata): String {
        val kind = when (meta.device?.type) {
            Device.TYPE_WATCH -> "Android 手表"
            Device.TYPE_PHONE -> "Android 手机"
            else -> "Health Connect"
        }
        return "$kind ${meta.dataOrigin.packageName}"
    }

    fun samples(record: Record): List<HealthSample> {
        val id = record.metadata.id
        val source = source(record.metadata)
        fun quantity(
            type: String,
            value: Double,
            unit: String,
            start: Instant,
            end: Instant = start
        ) = listOfNotNull(HealthSamples.quantity(id, type, value, unit, start, end, source))
        return when (record) {
            is HeartRateRecord -> record.samples.mapIndexedNotNull { index, sample ->
                HealthSamples.quantity(
                    HealthSamples.seriesId(id, "heart_rate", sample.time, index), "heart_rate",
                    sample.beatsPerMinute.toDouble(), "count/min", sample.time, source = source
                )
            }
            is RestingHeartRateRecord -> quantity(
                "resting_heart_rate", record.beatsPerMinute.toDouble(), "count/min", record.time
            )
            is HeartRateVariabilityRmssdRecord -> quantity(
                "hrv_rmssd", record.heartRateVariabilityMillis, "ms", record.time
            )
            is RespiratoryRateRecord -> quantity("respiratory_rate", record.rate, "count/min", record.time)
            is OxygenSaturationRecord -> quantity("oxygen_saturation", record.percentage.value, "%", record.time)
            is Vo2MaxRecord -> quantity("vo2max", record.vo2MillilitersPerMinuteKilogram, "mL/min·kg", record.time)
            is StepsRecord -> quantity(
                "step_count", record.count.toDouble(), "count", record.startTime, record.endTime
            )
            is ActiveCaloriesBurnedRecord -> quantity(
                "active_energy", record.energy.inKilocalories, "kcal", record.startTime, record.endTime
            )
            is SkinTemperatureRecord -> {
                val baseline = record.baseline
                if (record.measurementLocation != SkinTemperatureRecord.MEASUREMENT_LOCATION_WRIST ||
                    baseline == null
                ) {
                    emptyList()
                } else record.deltas.mapIndexedNotNull { index, delta ->
                    HealthSamples.quantity(
                        HealthSamples.seriesId(id, "wrist_temperature", delta.time, index), "wrist_temperature",
                        baseline.inCelsius + delta.delta.inCelsius, "degC", delta.time, source = source
                    )
                }
            }
            is SleepSessionRecord -> HealthSamples.sleep(
                id, record.startTime, record.endTime, source,
                record.stages.map { stage ->
                    // Preserve unknown stages so a recorded but unknown night isn't invented.
                    SleepStage(HealthSync.sleepType(stage.stage) ?: "unknown", stage.startTime, stage.endTime)
                }
            )
            is ExerciseSessionRecord -> {
                val paused = record.segments.filter {
                    it.segmentType == ExerciseSegment.EXERCISE_SEGMENT_TYPE_PAUSE ||
                        it.segmentType == ExerciseSegment.EXERCISE_SEGMENT_TYPE_REST
                }.sumOf { Duration.between(it.startTime, it.endTime).toMillis() }
                val elapsed = Duration.between(record.startTime, record.endTime).toMillis()
                listOfNotNull(
                    HealthSamples.workout(
                        id, HealthSync.activity(record.exerciseType), record.startTime, record.endTime, source,
                        durationMinutes = (elapsed - paused).coerceAtLeast(0) / 60000.0
                    )
                )
            }
            else -> emptyList()
        }
    }
}
