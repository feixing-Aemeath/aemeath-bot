package local.assistant.android.health

import android.content.Context
import androidx.health.connect.client.*
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.*
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.request.*
import androidx.health.connect.client.time.TimeRangeFilter
import androidx.work.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import local.assistant.android.data.DeviceSettings
import local.assistant.core.*
import java.time.*
import java.util.concurrent.TimeUnit
import kotlin.reflect.KClass

class HealthSync(private val context: Context) {
    private val device = DeviceSettings(context)
    val availability get() = if (device.tablet) HealthConnectClient.SDK_UNAVAILABLE
        else HealthConnectClient.getSdkStatus(context)

    private fun client() = HealthConnectClient.getOrCreate(context)

    private fun supports(c: HealthConnectClient, feature: Int) =
        c.features.getFeatureStatus(feature) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE

    fun permissions(): Set<String> {
        if (device.tablet) return emptySet()
        val base = recordTypes()
            .map { HealthPermission.getReadPermission(it) }
            .toMutableSet()
        if (availability == HealthConnectClient.SDK_AVAILABLE) {
            val c = client()
            if (supports(c, HealthConnectFeatures.FEATURE_SKIN_TEMPERATURE)) base += HealthPermission.getReadPermission(
                SkinTemperatureRecord::class
            )
            if (supports(c, HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND))
                base += HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND
        }
        return base
    }

    suspend fun granted(): Set<String> = try {
        if (availability == HealthConnectClient.SDK_AVAILABLE)
            client().permissionController.getGrantedPermissions()
        else
            emptySet()
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        emptySet()
    }

    fun schedule() {
        val manager = WorkManager.getInstance(context)
        if (!device.healthEnabled || device.mock || device.tablet) {
            manager.cancelUniqueWork(WORK)
            return
        }
        val request = PeriodicWorkRequestBuilder<HealthUploadWorker>(1, TimeUnit.HOURS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        manager.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    suspend fun sync(force: Boolean, background: Boolean): Boolean = withContext(Dispatchers.IO) {
        lock.withLock {
            if (device.mock || !device.healthEnabled || availability != HealthConnectClient.SDK_AVAILABLE)
                return@withLock true
            if (!force && System.currentTimeMillis() - device.healthLast < 300000) return@withLock true
            try {
                val c = client()
                val granted = c.permissionController.getGrantedPermissions()
                if (
                    background &&
                        (!supports(c, HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND) ||
                            HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND !in granted)
                )
                    return@withLock true
                if (
                    granted.none {
                        it in permissions() && it != HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND
                    }
                ) {
                    device.healthProblem = "尚未允许读取健康数据"
                    return@withLock true
                }
                val endpoint = device.baseUrl
                val token = device.token()
                val api = LiveBackend(endpoint, { token })
                val now = Instant.now()
                var sent = 0
                fun stillConnected() = !device.mock && device.healthEnabled && !device.tablet &&
                    device.baseUrl == endpoint && device.token() == token
                suspend fun <T : Record> syncType(type: KClass<T>) {
                    if (HealthPermission.getReadPermission(type) !in granted) return
                    val name = checkNotNull(type.qualifiedName)
                    sent += HealthIncrementalSync.run(
                        storedToken = device.healthCursor(name),
                        now = now,
                        zone = ZoneId.systemDefault(),
                        newToken = { c.getChangesToken(ChangesTokenRequest(setOf(type))) },
                        initial = { start, end, pageToken ->
                            val page = c.readRecords(
                                ReadRecordsRequest(
                                    type, TimeRangeFilter.between(start, end),
                                    pageSize = 1000, pageToken = pageToken
                                )
                            )
                            HealthInitialPage(page.records.flatMap(HealthRecordMapping::samples), page.pageToken)
                        },
                        changes = { cursor ->
                            val page = c.getChanges(cursor)
                            // HealthBatch has no deletion operation (same limitation as iPhone).
                            HealthChangePage(
                                page.changes.filterIsInstance<UpsertionChange>()
                                    .flatMap { HealthRecordMapping.samples(it.record) },
                                page.nextChangesToken, page.hasMore, page.changesTokenExpired
                            )
                        },
                        upload = { batch ->
                            check(stillConnected()) { "Health destination changed" }
                            api.uploadHealth(batch)
                        },
                        saveToken = { next ->
                            check(stillConnected()) { "Health destination changed" }
                            device.saveHealthCursor(name, next)
                        }
                    )
                }
                for (type in recordTypes()) syncType(type)
                check(stillConnected()) { "Health destination changed" }
                device.healthLast = System.currentTimeMillis()
                device.healthCount = sent
                device.healthProblem = null
                true
            } catch (e: CancellationException) {
                throw e
            } catch (e: SecurityException) {
                device.healthProblem = "健康数据权限已更改，请重新授权"
                true
            } catch (e: Exception) {
                device.healthProblem = (e as? APIError)?.let { "上传失败：${it.display}" } ?: "读取健康数据失败，请重试"
                false
            }
        }
    }

    private fun recordTypes(): Set<KClass<out Record>> {
        val types = mutableSetOf<KClass<out Record>>(
            HeartRateRecord::class,
            RestingHeartRateRecord::class,
            HeartRateVariabilityRmssdRecord::class,
            RespiratoryRateRecord::class,
            OxygenSaturationRecord::class,
            StepsRecord::class,
            ActiveCaloriesBurnedRecord::class,
            Vo2MaxRecord::class,
            SleepSessionRecord::class,
            ExerciseSessionRecord::class
        )
        if (availability == HealthConnectClient.SDK_AVAILABLE &&
            supports(client(), HealthConnectFeatures.FEATURE_SKIN_TEMPERATURE)
        ) types += SkinTemperatureRecord::class
        return types
    }

    companion object {
        private val lock = Mutex()
        const val WORK = "aemeath.health.upload"

        fun sleepType(stage: Int) = when (stage) {
            SleepSessionRecord.STAGE_TYPE_LIGHT -> "sleep_core"
            SleepSessionRecord.STAGE_TYPE_DEEP -> "sleep_deep"
            SleepSessionRecord.STAGE_TYPE_REM -> "sleep_rem"
            SleepSessionRecord.STAGE_TYPE_SLEEPING -> "sleep_asleep"
            SleepSessionRecord.STAGE_TYPE_AWAKE,
            SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED,
            SleepSessionRecord.STAGE_TYPE_OUT_OF_BED -> "sleep_awake"
            else -> null
        }

        fun activity(type: Int) = when (type) {
            ExerciseSessionRecord.EXERCISE_TYPE_RUNNING,
            ExerciseSessionRecord.EXERCISE_TYPE_RUNNING_TREADMILL -> "Running"
            ExerciseSessionRecord.EXERCISE_TYPE_WALKING -> "Walking"
            ExerciseSessionRecord.EXERCISE_TYPE_BIKING,
            ExerciseSessionRecord.EXERCISE_TYPE_BIKING_STATIONARY -> "Cycling"
            ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_POOL,
            ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_OPEN_WATER -> "Swimming"
            ExerciseSessionRecord.EXERCISE_TYPE_STRENGTH_TRAINING,
            ExerciseSessionRecord.EXERCISE_TYPE_WEIGHTLIFTING -> "TraditionalStrengthTraining"
            ExerciseSessionRecord.EXERCISE_TYPE_HIGH_INTENSITY_INTERVAL_TRAINING -> "HighIntensityIntervalTraining"
            ExerciseSessionRecord.EXERCISE_TYPE_HIKING -> "Hiking"
            ExerciseSessionRecord.EXERCISE_TYPE_YOGA -> "Yoga"
            ExerciseSessionRecord.EXERCISE_TYPE_PILATES -> "Pilates"
            ExerciseSessionRecord.EXERCISE_TYPE_ELLIPTICAL -> "Elliptical"
            ExerciseSessionRecord.EXERCISE_TYPE_ROWING, ExerciseSessionRecord.EXERCISE_TYPE_ROWING_MACHINE -> "Rowing"
            ExerciseSessionRecord.EXERCISE_TYPE_STAIR_CLIMBING,
            ExerciseSessionRecord.EXERCISE_TYPE_STAIR_CLIMBING_MACHINE -> "StairClimbing"
            ExerciseSessionRecord.EXERCISE_TYPE_BASKETBALL -> "Basketball"
            ExerciseSessionRecord.EXERCISE_TYPE_SOCCER -> "Soccer"
            ExerciseSessionRecord.EXERCISE_TYPE_BADMINTON -> "Badminton"
            ExerciseSessionRecord.EXERCISE_TYPE_TABLE_TENNIS -> "TableTennis"
            ExerciseSessionRecord.EXERCISE_TYPE_TENNIS -> "Tennis"
            ExerciseSessionRecord.EXERCISE_TYPE_VOLLEYBALL -> "Volleyball"
            ExerciseSessionRecord.EXERCISE_TYPE_DANCING -> "Dance"
            else -> "Other"
        }
    }
}

class HealthUploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = try {
        if (HealthSync(applicationContext).sync(true, true))
            Result.success()
        else if (runAttemptCount < 5) Result.retry() else Result.failure()
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        Result.failure()
    }
}
