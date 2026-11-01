package local.assistant.core

import java.time.Instant
import java.time.ZoneId

data class HealthInitialPage(val samples: List<HealthSample>, val nextPage: String? = null)

data class HealthChangePage(
    val samples: List<HealthSample>,
    val nextToken: String,
    val hasMore: Boolean = false,
    val expired: Boolean = false
)

/** One stream per record type. The platform adapter supplies Health Connect operations. */
object HealthIncrementalSync {
    suspend fun run(
        storedToken: String?,
        now: Instant,
        zone: ZoneId,
        newToken: suspend () -> String,
        initial: suspend (Instant, Instant, String?) -> HealthInitialPage,
        changes: suspend (String) -> HealthChangePage,
        upload: suspend (HealthBatch) -> HealthAccepted,
        saveToken: (String) -> Unit
    ): Int {
        var cursor = storedToken
        var page = cursor?.let { changes(it) }
        var sent = 0
        if (cursor == null || page?.expired == true) {
            // Reserve a changes token before scanning, closing the history/changes race.
            val reserved = newToken()
            val start = if (cursor == null) {
                HealthSamples.firstStart(now, zone)
            } else {
                now.atZone(zone).minusDays(30).toInstant()
            }
            var nextPage: String? = null
            do {
                val history = initial(start, now, nextPage)
                sent += HealthSamples.uploadPage(history.samples, null, upload, saveToken)
                nextPage = history.nextPage
            } while (nextPage != null)
            saveToken(reserved)
            cursor = reserved
            page = null
        }
        do {
            val current = page ?: changes(checkNotNull(cursor))
            check(!current.expired) { "Health changes token expired during sync" }
            sent += HealthSamples.uploadPage(current.samples, current.nextToken, upload, saveToken)
            cursor = current.nextToken
            page = null
        } while (current.hasMore)
        return sent
    }
}
