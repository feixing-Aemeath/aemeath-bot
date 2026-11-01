package local.assistant.core

import kotlin.math.*

enum class MascotEvent {
    CONNECTED,
    DISCONNECTED,
    STARTED,
    SUCCEEDED,
    FAILED,
    ENABLED,
    DISABLED
}

class MascotStateMachine(
    var stored: MascotState = MascotState.IDLE,
    var since: Double = 0.0,
    val doneDuration: Double = 3.0
) {
    fun state(time: Double) =
        if (stored == MascotState.DONE && time - since >= doneDuration) MascotState.IDLE else stored

    fun handle(event: MascotEvent, time: Double) {
        val current = state(time)
        val next = when (event) {
            MascotEvent.DISCONNECTED, MascotEvent.DISABLED -> MascotState.OFFLINE
            MascotEvent.CONNECTED,
            MascotEvent.ENABLED -> if (current == MascotState.OFFLINE) MascotState.IDLE else current
            else -> if (current == MascotState.OFFLINE) current else when (event) {
                MascotEvent.STARTED -> MascotState.WORKING
                MascotEvent.SUCCEEDED -> MascotState.DONE
                else -> MascotState.ERROR
            }
        }
        if (next != current || event == MascotEvent.SUCCEEDED && next == MascotState.DONE) since = time
        stored = next
    }
}

data class AnimationRequest(val id: Long, val at: Double, val act: String? = null)

data class AnimationPlayback(val act: MascotAct, val elapsed: Double, val looping: Boolean = false) {
    val phase get() = (elapsed.coerceAtLeast(0.0) / act.duration) % 1.0
    val visibility
        get() = min(1.0, elapsed.coerceAtLeast(0.0) / .25)
            .let { if (looping) it else min(it, ((act.duration - elapsed) / .3).coerceAtLeast(0.0)) }

    fun applying(frame: MascotFrame): MascotFrame = frame.copy()
        .also { f ->
            val wave = sin(phase * 2 * PI)
            when (act.id) {
                "sleep" -> {
                    f.x = 0.0
                    f.y = 0.0
                    f.scaleX = 1.0
                    f.scaleY = 1 + .012 * wave
                    f.rotation = 3.0
                    f.eyeX = 0.0
                    f.eyeY = 0.0
                    f.closure = min(1.0, elapsed / .5)
                }
                "stretch" -> {
                    f.closure = sin(phase * PI)
                    f.scaleY *= 1 + .055 * sin(phase * PI)
                    f.rotation += 4 * wave
                }
                "writing", "grading", "tokens", "calendar", "checklist", "approval" -> {
                    f.eyeX = 0.0
                    f.eyeY = .01
                    f.rotation *= .25
                }
                "wave" -> {
                    f.rotation += 5 * wave
                    f.closure = max(f.closure, max(0.0, sin(phase * 4 * PI)))
                }
                "training", "savings", "medal", "books", "report" -> f.y -= .025 * abs(wave)
            }
        }
}

/** Pure timing/selection port of MascotAnimationPlayer.swift. No timers or UI. */
class MascotAnimationPlayer(seed: ULong) {
    private var random = seed
    private var sticker: String? = null
    private var state: MascotState? = null
    private var night = false
    private var began = 0.0
    private var requested: Pair<MascotAct, Double>? = null
    private var lastRequest: Long? = null
    private var previous: String? = null
    var nextIdleAt: Double? = null
        private set

    private fun number(): Double {
        random = random * 6364136223846793005uL + 1442695040888963407uL
        return (random shr 11).toDouble() / 9007199254740991.0
    }

    private fun choose(choices: List<MascotAct>): MascotAct? {
        val pool = choices.filter { it.id != previous }.ifEmpty { choices }
        if (pool.isEmpty()) return null
        return pool[min(pool.lastIndex, (number() * pool.size).toInt())].also { previous = it.id }
    }

    fun sample(
        name: String,
        current: MascotState,
        now: Double,
        hour: Int,
        request: AnimationRequest? = null,
        enabled: Boolean = true,
        reduced: Boolean = false,
        active: Boolean = true
    ): AnimationPlayback? {
        val acts = MascotMotion.acts.filter { it.sticker == name }
        if (!enabled || reduced || !active || acts.isEmpty()) {
            state = null
            requested = null
            nextIdleAt = null
            lastRequest = request?.id
            return null
        }
        val sleeping = name == "lily_veil" && (hour >= 23 || hour < 6)
        if (sticker != name || state != current || night != sleeping) {
            sticker = name
            state = current
            night = sleeping
            began = now
            requested = null
            nextIdleAt = now + 20 + 20 * number()
        }
        if (request != null && request.id != lastRequest) {
            lastRequest = request.id
            val age = now - request.at
            val act = request.act?.let { id -> MascotMotion.acts.find { it.id == id } }
            if (age >= 0 && age < (act?.duration ?: 6.0)) {
                val selected = if (request.act == null) choose(acts) else act
                if (selected != null && selected.sticker == name) {
                    requested = selected to request.at
                    previous = selected.id
                    nextIdleAt = now + 20 + 20 * number()
                }
            }
        }
        requested?.let { (act, at) ->
            if (now - at < act.duration) return AnimationPlayback(act, now - at)
            requested = null
        }
        val elapsed = (now - began).coerceAtLeast(0.0)
        fun play(id: String?, loop: Boolean = false) = acts.find { it.id == id }?.let {
            AnimationPlayback(it, elapsed, loop)
        }
        if (current == MascotState.OFFLINE || current == MascotState.IDLE && sleeping)
            return if (name == "lily_veil") play("sleep", true) else null
        if (current == MascotState.WORKING) return play(working(name), true)
        if (current == MascotState.DONE) return play(celebration(name))?.takeIf { elapsed < it.act.duration }
        if (current == MascotState.IDLE && now >= (nextIdleAt ?: now)) choose(
            acts.filter { it.id != working(name) && it.id != "sleep" }
        )
            ?.let {
                requested = it to now
                nextIdleAt = now + 20 + 20 * number()
                return AnimationPlayback(it, 0.0)
            }
        return null
    }

    companion object {
        fun working(name: String) = mapOf(
            "lily_veil" to "heartbeat",
            "shouanren" to "tokens",
            "red_ribbon" to "grading",
            "moning" to "writing",
            "luosela" to "calendar",
            "aemeath" to "checklist"
        )[name]

        fun celebration(name: String) = mapOf(
            "lily_veil" to "training",
            "shouanren" to "savings",
            "red_ribbon" to "medal",
            "moning" to "books",
            "luosela" to "approval",
            "aemeath" to "wave"
        )[name]
    }
}
