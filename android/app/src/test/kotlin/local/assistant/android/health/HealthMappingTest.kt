package local.assistant.android.health

import org.junit.Assert.*
import org.junit.Test
import androidx.health.connect.client.records.*
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.units.Energy
import androidx.health.connect.client.units.Temperature
import androidx.health.connect.client.units.TemperatureDelta
import androidx.health.connect.client.units.Percentage
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.Instant

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class HealthMappingTest {
    private val start = Instant.parse("2026-10-31T01:00:00Z")
    private val end = start.plusSeconds(120)
    private val metadata = Metadata.manualEntryWithId("record-id")
    @Test
    fun exerciseKindsUseTheIPhoneBackendAliases() {
        assertEquals("Running", HealthSync.activity(ExerciseSessionRecord.EXERCISE_TYPE_RUNNING_TREADMILL))
        assertEquals("Cycling", HealthSync.activity(ExerciseSessionRecord.EXERCISE_TYPE_BIKING_STATIONARY))
        assertEquals("Swimming", HealthSync.activity(ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_OPEN_WATER))
        assertEquals("TraditionalStrengthTraining", HealthSync.activity(ExerciseSessionRecord.EXERCISE_TYPE_WEIGHTLIFTING))
        assertEquals("Other", HealthSync.activity(-1))
    }

    @Test
    fun knownSleepStagesUseDocumentedAliases() {
        assertEquals("sleep_core", HealthSync.sleepType(SleepSessionRecord.STAGE_TYPE_LIGHT))
        assertEquals("sleep_deep", HealthSync.sleepType(SleepSessionRecord.STAGE_TYPE_DEEP))
        assertEquals("sleep_rem", HealthSync.sleepType(SleepSessionRecord.STAGE_TYPE_REM))
        assertEquals("sleep_asleep", HealthSync.sleepType(SleepSessionRecord.STAGE_TYPE_SLEEPING))
        assertEquals("sleep_awake", HealthSync.sleepType(SleepSessionRecord.STAGE_TYPE_OUT_OF_BED))
    }

    @Test
    fun unknownSleepStageIsOmitted() {
        assertNull(HealthSync.sleepType(SleepSessionRecord.STAGE_TYPE_UNKNOWN))
        assertNull(HealthSync.sleepType(-1))
    }

    @Test
    fun heartRateSeriesUploadsEachReadingInsteadOfTheMean() {
        val record = HeartRateRecord(start, null, end, null, listOf(
            HeartRateRecord.Sample(start, 60),
            HeartRateRecord.Sample(start.plusSeconds(30), 175)
        ), metadata)
        val rows = HealthRecordMapping.samples(record)
        assertEquals(listOf(60.0, 175.0), rows.map { it.value })
        assertEquals(listOf(start.toString(), start.plusSeconds(30).toString()), rows.map { it.startAt })
        assertTrue(rows.all { it.endAt == it.startAt && it.unit == "count/min" })
        assertNotEquals(rows[0].id, rows[1].id)
        assertEquals(rows, HealthRecordMapping.samples(record))
    }

    @Test
    fun stepsAndEnergyPreserveTheirOriginalIntervals() {
        val steps = StepsRecord(start, null, end, null, 123, metadata)
        val energy = ActiveCaloriesBurnedRecord(start, null, end, null, Energy.kilocalories(45.5), metadata)
        val step = HealthRecordMapping.samples(steps).single()
        assertEquals("step_count", step.type)
        assertEquals(123.0, step.value, 0.0)
        assertEquals(start.toString(), step.startAt)
        assertEquals(end.toString(), step.endAt)
        val calories = HealthRecordMapping.samples(energy).single()
        assertEquals("active_energy", calories.type)
        assertEquals(45.5, calories.value, 0.00001)
        assertEquals("kcal", calories.unit)
    }

    @Test
    fun workoutsAreUploadedPerSessionWithAllIPhoneFields() {
        val record = ExerciseSessionRecord(start, null, end, null, metadata, ExerciseSessionRecord.EXERCISE_TYPE_RUNNING)
        val sample = HealthRecordMapping.samples(record).single()
        assertEquals("record-id", sample.id)
        assertEquals("workout:Running", sample.type)
        assertEquals(2.0, sample.value, 0.0)
        assertEquals("min", sample.unit)
        assertEquals(start.toString(), sample.startAt)
        assertEquals(end.toString(), sample.endAt)
        assertTrue(sample.source.startsWith("Health Connect"))
    }

    @Test
    fun workoutDurationExcludesRecordedPausesAndRest() {
        val record = ExerciseSessionRecord(
            start, null, end, null, metadata, ExerciseSessionRecord.EXERCISE_TYPE_RUNNING,
            segments = listOf(
                ExerciseSegment(start, start.plusSeconds(20), ExerciseSegment.EXERCISE_SEGMENT_TYPE_PAUSE),
                ExerciseSegment(start.plusSeconds(20), start.plusSeconds(30), ExerciseSegment.EXERCISE_SEGMENT_TYPE_REST)
            )
        )
        val sample = HealthRecordMapping.samples(record).single()
        assertEquals(1.5, sample.value, 0.0)
        assertEquals(start.toString(), sample.startAt)
        assertEquals(end.toString(), sample.endAt)
    }

    @Test
    fun rmssdIsNeverRelabeledAsSdnn() {
        val record = HeartRateVariabilityRmssdRecord(start, null, 42.0, metadata)
        val sample = HealthRecordMapping.samples(record).single()
        assertEquals("hrv_rmssd", sample.type)
        assertEquals("ms", sample.unit)
        assertEquals(42.0, sample.value, 0.0)
    }

    @Test
    fun instantaneousMetricsUseIPhoneNamesUnitsAndValues() {
        val records = listOf(
            RestingHeartRateRecord(start, null, 58, metadata),
            RespiratoryRateRecord(start, null, 14.0, metadata),
            OxygenSaturationRecord(start, null, Percentage(98.0), metadata),
            Vo2MaxRecord(start, null, metadata, 47.0)
        )
        val samples = records.flatMap(HealthRecordMapping::samples)
        assertEquals(listOf("resting_heart_rate", "respiratory_rate", "oxygen_saturation", "vo2max"), samples.map { it.type })
        assertEquals(listOf("count/min", "count/min", "%", "mL/min·kg"), samples.map { it.unit })
        assertEquals(listOf(58.0, 14.0, 98.0, 47.0), samples.map { it.value })
        assertTrue(samples.all { it.startAt == start.toString() && it.endAt == it.startAt })
    }

    @Test
    fun sleepStagesKeepTheirActualBoundaries() {
        val record = SleepSessionRecord(start, null, end, null, metadata, stages = listOf(
            SleepSessionRecord.Stage(start, start.plusSeconds(60), SleepSessionRecord.STAGE_TYPE_LIGHT),
            SleepSessionRecord.Stage(start.plusSeconds(60), end, SleepSessionRecord.STAGE_TYPE_REM)
        ))
        val samples = HealthRecordMapping.samples(record)
        assertEquals(listOf("sleep_core", "sleep_rem"), samples.map { it.type })
        assertEquals(start.plusSeconds(60).toString(), samples.first().endAt)
        assertTrue(samples.all { it.unit.isEmpty() && it.value == 1.0 })
    }

    @Test
    fun unknownSleepStagesDoNotBecomeUnspecifiedSleep() {
        val record = SleepSessionRecord(start, null, end, null, metadata, stages = listOf(
            SleepSessionRecord.Stage(start, end, SleepSessionRecord.STAGE_TYPE_UNKNOWN)
        ))
        assertTrue(HealthRecordMapping.samples(record).isEmpty())
    }

    @Test
    fun wristTemperatureNeedsABaselineAndTheWristLocation() {
        fun record(baseline: Temperature?, location: Int) = SkinTemperatureRecord(
            start, null, end, null, metadata,
            listOf(SkinTemperatureRecord.Delta(start, TemperatureDelta.celsius(.4))), baseline, location
        )
        val wrist = SkinTemperatureRecord.MEASUREMENT_LOCATION_WRIST
        assertTrue(HealthRecordMapping.samples(record(null, wrist)).isEmpty())
        assertTrue(HealthRecordMapping.samples(record(Temperature.celsius(35.0), SkinTemperatureRecord.MEASUREMENT_LOCATION_UNKNOWN)).isEmpty())
        val sample = HealthRecordMapping.samples(record(Temperature.celsius(35.0), wrist)).single()
        assertEquals("wrist_temperature", sample.type)
        assertEquals(35.4, sample.value, 0.00001)
        assertEquals("degC", sample.unit)
    }
}
