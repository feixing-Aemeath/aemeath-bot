package local.assistant.core

import java.time.*
import kotlin.math.*

enum class MascotState {
    IDLE,
    WORKING,
    DONE,
    ERROR,
    OFFLINE;

    val headline
        get() = when (this) {
            IDLE -> "Mac 在线，空闲中"
            WORKING -> "正在运行"
            DONE -> "刚刚完成"
            ERROR -> "上次运行失败"
            OFFLINE -> "连不上 Mac"
        }

    companion object {
        fun combined(states: List<MascotState>) = states.maxByOrNull {
            when (it) {
                IDLE -> 0
                DONE -> 1
                WORKING -> 2
                ERROR -> 3
                OFFLINE -> 4
            }
        } ?: IDLE

        fun knowledge(inputs: List<PluginView>, runs: List<Run>, now: Instant): MascotState {
            val organizing = runs.filter { it.job == "knowledge_organize" }
            val states = inputs.map { plugin(it, runs, now) }
            return when {
                organizing.any { it.status == "running" } || WORKING in states -> WORKING
                ERROR in states -> ERROR
                else -> run(organizing.firstOrNull { it.harness == null }, now)
            }
        }

        fun run(run: Run?, now: Instant): MascotState = when (run?.status) {
            "running" -> WORKING
            "failed", "cancelled" -> ERROR
            "succeeded" ->
                if (run.finishedAt == null || Duration.between(instant(run.finishedAt), now).seconds < 3) DONE else IDLE
            else -> IDLE
        }

        fun plugin(plugin: PluginView, runs: List<Run>, now: Instant): MascotState = when (plugin.state) {
            "disabled", "unsupported" -> OFFLINE
            "error" -> ERROR
            else -> if (plugin.ownRuns(runs).any { it.status == "running" }) WORKING else run(
                plugin.ownRuns(runs).firstOrNull(),
                now
            )
        }

        fun assistant(status: Status?, runs: List<Run>, now: Instant): MascotState =
            if (status?.online != true)
                OFFLINE
            else if (runs.any { it.status == "running" })
                WORKING
            else
                run(runs.firstOrNull() ?: status.lastRun, now)
    }
}

enum class CueKind(val duration: Double) {
    POKE(0.6),
    GREET(0.5),
    TALK(1.2)
}

data class MascotCue(val kind: CueKind, val at: Double) {
    fun progress(time: Double) = ((time - at) / kind.duration).takeIf { it >= 0 && it < 1 }
}

data class Gaze(var x: Double = 0.0, var y: Double = 0.0) {
    init {
        val length = hypot(x, y)
        if (length > 1) {
            x /= length
            y /= length
        }
    }

    fun normalized(): Gaze {
        val length = hypot(x, y)
        return if (length > 1) Gaze(x / length, y / length) else this
    }

    fun follow(target: Gaze, elapsed: Double): Gaze {
        if (elapsed <= 0) return this
        if (elapsed >= 1) return target
        val step = 1 - exp(-elapsed / 0.08)
        return Gaze(x + (target.x - x) * step, y + (target.y - y) * step).normalized()
    }

    companion object {
        fun toward(dx: Double, dy: Double, reach: Double = .6): Gaze = if (reach <= 0) Gaze() else Gaze(
            dx / reach,
            dy / reach
        ).normalized()
    }
}

data class MascotFrame(
    var x: Double = 0.0,
    var y: Double = 0.0,
    var scaleX: Double = 1.0,
    var scaleY: Double = 1.0,
    var rotation: Double = 0.0,
    var eyeX: Double = 0.0,
    var eyeY: Double = 0.0,
    var closure: Double = 0.0,
    var saturation: Double = 1.0,
    val effects: List<String> = emptyList(),
    var effectPhase: Double = 0.0
)

data class MascotParticle(
    val heart: Boolean,
    val x: Double,
    val y: Double,
    val size: Double,
    val rotation: Double,
    val opacity: Double
)

data class MascotAct(
    val id: String,
    val title: String,
    val sticker: String,
    val duration: Double = 3.0
)

object MascotMotion {
    val acts: List<MascotAct> = listOf(
        "lily_veil" to listOf(
            "sleep:蕾丝被子",
            "heartbeat:怦怦心跳",
            "stretch:早安伸懒腰",
            "training:训练完成"
        ),
        "shouanren" to listOf(
            "tokens:算 Token",
            "quota:额度仪表",
            "stars:星空闪烁",
            "savings:攒下星币"
        ),
        "red_ribbon" to listOf(
            "grading:红笔批卷",
            "ribbon:蝴蝶结弹跳",
            "report:展示成绩单",
            "medal:学分勋章"
        ),
        "moning" to listOf(
            "writing:写作业",
            "books:叠好课本",
            "alarm:上课铃响",
            "halo:晶石光环"
        ),
        "luosela" to listOf(
            "calendar:翻日历",
            "approval:请假已批准",
            "meteor:帽上流星",
            "earrings:月亮轻摇"
        ),
        "aemeath" to listOf(
            "umbrella:撑伞提醒",
            "checklist:勾好待办",
            "wave:挥手你好",
            "moon:月牙发光"
        ),
    )
        .flatMap { (sticker, rows) ->
            rows.map {
                val p = it.split(':')
                MascotAct(
                    p[0],
                    p[1],
                    sticker,
                    if (p[0] == "sleep") 6.0 else if (
                        p[0] in listOf(
                            "tokens",
                            "writing",
                            "grading",
                            "calendar",
                            "checklist"
                        )
                    ) 4.0 else 3.0
                )
            }
        }

    fun seed(name: String): ULong {
        var n = 0xcbf29ce484222325uL
        name.toByteArray().forEach { n = (n xor (it.toUByte().toULong())) * 0x100000001b3uL }
        return n
    }

    fun random(seed: ULong, slot: Long, salt: ULong): Double {
        var z = seed + slot.toULong() * 0x9e3779b97f4a7c15uL + salt * 0xd1b54a32d192ed03uL
        z = (z xor (z shr 30)) * 0xbf58476d1ce4e5b9uL
        z = (z xor (z shr 27)) * 0x94d049bb133111ebuL
        z = z xor (z shr 31)
        return (z shr 11).toDouble() / 9007199254740992.0
    }

    fun smooth(x: Double): Double {
        val t = x.coerceIn(0.0, 1.0)
        return t * t * (3 - 2 * t)
    }

    fun window(
        p: Double,
        from: Double,
        to: Double,
        fade: Double
    ) = smooth((p - from) / fade) * (1 - smooth((p - to + fade) / fade))

    fun blink(time: Double, seed: ULong): Double {
        val local = time - random(seed, 0, 1uL) * 4.25
        val slot = floor(local / 4.25).toLong()
        fun lid(t: Double, hold: Double): Double = when {
            t < 0 -> 0.0
            t < 0.04 -> smooth(t / 0.04)
            t < 0.04 + hold -> 1.0
            t < 0.1 + hold -> 1 - smooth((t - 0.04 - hold) / 0.06)
            else -> 0.0
        }
        return (slot - 1..slot).maxOf { k ->
            val start = k * 4.25 + random(seed, k, 2uL) * 1.75
            val hold = 0.07 + 0.03 * random(seed, k, 3uL)
            max(
                lid(local - start, hold),
                if (random(seed, k, 4uL) < 0.2) lid(local - start - 0.04 - hold - 0.06 - 0.08, hold * 0.8) else 0.0
            )
        }
    }

    fun glance(time: Double, seed: ULong): Gaze {
        val local = time - random(seed, 0, 5uL) * 6.5
        val slot = floor(local / 6.5).toLong()
        if (random(seed, slot, 6uL) >= 0.75) return Gaze()
        val elapsed = local - (slot * 6.5 + 0.5 + 2.5 * random(seed, slot, 7uL))
        val hold = 0.9 + 1.2 * random(seed, slot, 8uL)
        if (elapsed < 0 || elapsed >= 0.25 + hold + 0.35) return Gaze()
        val weight = if (elapsed < 0.25 + hold) smooth(elapsed / 0.25) else 1 - smooth((elapsed - 0.25 - hold) / 0.35)
        val angle = 2 * PI * random(seed, slot, 9uL)
        val reach = (0.55 + 0.45 * random(seed, slot, 10uL)) * weight
        return Gaze(cos(angle) * reach, sin(angle) * reach * 0.6)
    }

    fun target(
        time: Double,
        state: MascotState,
        seed: ULong,
        pointer: Gaze? = null,
        cue: MascotCue? = null
    ): Gaze = when {
        state == MascotState.OFFLINE -> Gaze()
        pointer != null -> pointer.normalized()
        cue?.kind == CueKind.TALK && cue.progress(time) != null -> Gaze()
        state == MascotState.WORKING -> Gaze(glance(time, seed).x * .5, .55)
        state == MascotState.ERROR -> Gaze(-.35, .75)
        else -> glance(time, seed)
    }

    fun burst(
        cue: MascotCue?,
        time: Double,
        origin: Gaze = Gaze(.5, .4),
        seed: ULong = 0uL
    ): List<MascotParticle> {
        if (cue == null || cue.kind != CueKind.POKE) return emptyList()
        val age = time - cue.at
        if (age < 0 || age >= .9) return emptyList()
        val q = age / .9
        val slot = (cue.at * 1000).roundToLong()
        return (0..5).map { index ->
            fun vary(salt: ULong) = random(seed, slot, salt + index.toULong() * 8uL)
            val angle = -PI / 2 + (index - 2.5) / 2.5 * .95 + (vary(20uL) - .5) * .3
            val distance = (.2 + .12 * vary(21uL)) * (1 - (1 - q) * (1 - q))
            val pop = min(1.0, q / .12) * (1 - .3 * q)
            MascotParticle(
                index % 2 == 0,
                origin.x + cos(angle) * distance,
                origin.y + sin(angle) * distance + .04 * q * q,
                (.06 + .035 * vary(22uL)) * pop,
                (vary(23uL) - .5) * 40 + (vary(24uL) - .5) * 120 * q,
                if (q < .6) 1.0 else 1 - (q - .6) / .4
            )
        }
    }

    fun frame(
        time: Double,
        state: MascotState,
        since: Double,
        seed: ULong,
        cue: MascotCue? = null,
        reduced: Boolean = false,
        look: Gaze = Gaze(),
        subtle: Boolean = false
    ): MascotFrame {
        val f = MascotFrame(
            rotation = if (state == MascotState.ERROR) -8.0 else if (state == MascotState.OFFLINE) 4.0 else 0.0,
            saturation = if (state == MascotState.OFFLINE) 0.4 else 1.0,
            effects = when (state) {
                MascotState.ERROR -> listOf("sweat")
                MascotState.OFFLINE -> listOf("zzz")
                else -> emptyList()
            },
            effectPhase = if (reduced) .5 else 0.0
        )
        if (reduced) return f
        val elapsed = (time - since).coerceAtLeast(0.0)
        when (state) {
            MascotState.IDLE -> {
                f.y = 0.02 * sin(2 * PI * time / 3)
                f.scaleX = 1 + 0.01 * cos(2 * PI * time / 3)
                f.scaleY = f.scaleX
            }
            MascotState.WORKING -> {
                f.rotation = 6 * sin(2 * PI * time / 1.2)
                f.y = -0.015 * abs(sin(2 * PI * time / 1.2))
            }
            MascotState.DONE -> {
                f.y = if (elapsed < 0.6) -0.08 * sin(PI * elapsed / 0.6) else 0.02 * sin(2 * PI * (elapsed - 0.6) / 3)
                if (elapsed < .6 && elapsed / .6 > .8) {
                    f.scaleY = .94
                    f.scaleX = 1.03
                }
            }
            MascotState.ERROR -> {
                f.y = 0.04
                f.rotation += 1.5 * sin(2 * PI * time / 2.5)
                f.scaleY = 0.97
                f.effectPhase = time / 2 % 1
            }
            MascotState.OFFLINE -> {
                f.scaleX = 1 + 0.015 * sin(2 * PI * time / 4)
                f.scaleY = f.scaleX
                f.effectPhase = time / 3 % 1
            }
        }
        val gaze = if (state == MascotState.OFFLINE) Gaze() else look.normalized()
        if (subtle) {
            val still = frame(
                0.0,
                state,
                0.0,
                seed,
                reduced = true
            )
            still.eyeX = gaze.x * .016
            still.eyeY = gaze.y * .01
            still.closure = if (state == MascotState.OFFLINE) 1.0 else blink(time, seed)
            return still
        }
        f.eyeX = gaze.x * 0.016
        f.eyeY = gaze.y * 0.01
        f.closure = if (state == MascotState.OFFLINE) 1.0 else blink(time, seed)
        if (state != MascotState.OFFLINE) {
            f.rotation += gaze.x * 3
            f.x += gaze.x * 0.006
            f.y += gaze.y * 0.004
        }
        val p = cue?.progress(time) ?: return f
        val amount = if (state == MascotState.ERROR || state == MascotState.OFFLINE) 0.5 else 1.0
        if (state == MascotState.OFFLINE && cue.kind != CueKind.POKE) return f
        when (cue.kind) {
            CueKind.POKE -> {
                if (p < 0.15) {
                    val s = sin(PI * p / 0.15)
                    f.scaleY *= 1 - 0.1 * s * amount
                    f.scaleX *= 1 + 0.06 * s * amount
                } else if (p < 0.75) {
                    val q = (p - 0.15) / 0.6
                    f.y -= 0.08 * sin(PI * q) * amount
                    val s = if (q < 0.5) sin(2 * PI * q) else 0.0
                    f.scaleY *= 1 + 0.06 * s * amount
                    f.scaleX *= 1 - 0.04 * s * amount
                } else {
                    val s = sin(PI * (p - 0.75) / 0.25)
                    f.scaleY *= 1 - 0.07 * s * amount
                    f.scaleX *= 1 + 0.045 * s * amount
                }
                f.rotation += (if (gaze.x < 0) -5 else 5) * sin(4 * PI * p) * (1 - p) * amount
                f.closure = if (state == MascotState.OFFLINE) 1 - window(
                    p,
                    0.0,
                    1.0,
                    0.15
                ) else max(
                    f.closure,
                    window(
                        p,
                        0.02,
                        0.45,
                        0.04
                    )
                )
            }
            CueKind.GREET -> {
                val bump = sin(PI * p)
                f.y -= 0.035 * bump * amount
                f.x += (if (gaze.x < 0) -.01 else .01) * bump * amount
                f.scaleY *= 1 + .02 * bump * amount
                f.scaleX *= 1 - .01 * bump * amount
                f.rotation += (if (gaze.x < 0) -5 else 5) * bump * amount
                f.closure = max(
                    f.closure,
                    window(
                        p,
                        0.2,
                        0.75,
                        0.06
                    )
                )
            }
            CueKind.TALK -> {
                val e = smooth(p / 0.12) * (1 - smooth((p - 0.8) / 0.2))
                val n = (1 - cos(6 * PI * p)) / 2
                f.y += 0.018 * n * e * amount
                f.scaleY *= 1 - 0.02 * n * e * amount
                f.scaleX *= 1 + .012 * n * e * amount
                f.rotation += 2.5 * sin(3 * PI * p) * e * amount
            }
        }
        return f
    }
}
