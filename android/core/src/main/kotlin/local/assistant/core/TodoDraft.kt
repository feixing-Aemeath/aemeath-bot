package local.assistant.core

import kotlinx.serialization.json.*
import java.time.*

data class TodoDraft(
    val title: String,
    val notes: String,
    val hasDue: Boolean,
    val hasTime: Boolean,
    val due: LocalDateTime
) {
    val valid get() = title.trim().isNotEmpty()

    fun patch(original: Todo, zone: ZoneId): JsonObject = buildJsonObject {
        require(valid)
        if (title.trim() != original.title) put("title", title.trim())
        if (notes != original.notes) put("notes", notes)
        if (!hasDue && original.dueAt != null) put("due_at", JsonNull)
        if (hasDue) {
            val next = (if (hasTime) due else due.toLocalDate().atStartOfDay()).atZone(zone).toInstant()
            if (original.dueAt?.let(::instant) != next || hasTime != original.hasTime) {
                put("due_at", next.toString())
                put("has_time", hasTime)
            }
        }
    }

    companion object {
        fun from(todo: Todo, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()) = TodoDraft(
            todo.title,
            todo.notes,
            todo.dueAt != null,
            todo.hasTime,
            todo.dueAt?.let(::instant)?.atZone(zone)?.toLocalDateTime() ?: now.atZone(zone).toLocalDate().atTime(18, 0)
        )
    }
}
