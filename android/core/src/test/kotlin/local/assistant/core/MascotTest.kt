package local.assistant.core

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.*

class MascotTest {
    private val seed = MascotMotion.seed("lily_veil")
    private val start = 1791536400.0

    private fun frame(t: Double = start, state: MascotState = MascotState.IDLE, cue: MascotCue? = null) =
        MascotMotion.frame(
            t,
            state,
            start,
            seed,
            cue
        )

    @Test
    fun seedStableAndDifferentForEachCharacter() {
        assertEquals(0xcbf29ce484222325uL, MascotMotion.seed(""))
        assertEquals(seed, MascotMotion.seed("lily_veil"))
        val names = listOf(
            "aemeath",
            "luosela",
            "moning",
            "shouanren",
            "lily_veil",
            "red_ribbon",
            "placeholder"
        )
        assertEquals(names.size, names.map { MascotMotion.seed(it) }.toSet().size)
    }

    @Test
    fun blinkBoundsTimingAndDutyCycle() {
        val samples = (0..120000).map { MascotMotion.blink(start + it * .005, seed) }
        assertTrue(samples.all { it in 0.0..1.0 })
        assertTrue(samples.any { it in 0.1..0.9 })
        val duty = samples.count { it >= .5 }.toDouble() / samples.size
        assertTrue(duty in 0.02..0.06)
        val spans = mutableListOf<Double>()
        var began: Double? = null
        samples.forEachIndexed { i, x ->
            if (x >= .5 && began == null) began = i * .005
            if (x < .5 && began != null) {
                spans += i * .005 - began!!
                began = null
            }
        }
        assertTrue(spans.size > 100)
        assertTrue(spans.all { it in 0.09..0.16 })
    }

    @Test
    fun blinkingIsDeterministicAndNotSynchronized() {
        var different = 0
        repeat(2000) {
            val t = start + it * .03
            assertEquals(MascotMotion.blink(t, seed), MascotMotion.blink(t, seed), 0.0)
            if (abs(MascotMotion.blink(t, seed) - MascotMotion.blink(t, MascotMotion.seed("aemeath"))) > .5) different++
        }
        assertTrue(different > 30)
    }

    @Test
    fun glancesAreSmoothMostlyAheadAndBounded() {
        val gaze = (0..24000).map { MascotMotion.glance(start + it * .01, seed) }
        assertTrue(gaze.all { hypot(it.x, it.y) <= 1.00000001 && abs(it.y) <= .60000001 })
        assertTrue(gaze.count { hypot(it.x, it.y) < .01 } > .4 * gaze.size)
        assertTrue(gaze.any { it.x > .3 })
        assertTrue(gaze.any { it.x < -.3 })
        assertTrue(gaze.zipWithNext().all { (a, b) -> hypot(a.x - b.x, a.y - b.y) < .1 })
    }

    @Test
    fun gazeTowardsPointsIsClampedAndSmoothed() {
        assertEquals(Gaze(1.0, 0.0), Gaze.toward(2.0, 0.0))
        assertEquals(-.25, Gaze.toward(0.0, -.15).y, 1e-9)
        val far = Gaze.toward(-30.0, 40.0)
        assertEquals(-.6, far.x, 1e-9)
        assertEquals(.8, far.y, 1e-9)
        assertEquals(.6, Gaze(3.0, 4.0).x, 1e-9)
        assertEquals(.8, Gaze(3.0, 4.0).y, 1e-9)
        var g = Gaze()
        val target = Gaze(1.0, 0.0)
        repeat(30) {
            val next = g.follow(target, 1.0 / 60)
            assertTrue(next.x > g.x)
            g = next
        }
        assertTrue(g.x > .99)
        assertEquals(Gaze(), Gaze().follow(target, 0.0))
        assertEquals(target, Gaze().follow(target, 5.0))
    }

    @Test
    fun targetFollowsPointerTalkAndState() {
        val pointer = Gaze(.8, -.2)
        assertEquals(
            pointer,
            MascotMotion.target(
                start,
                MascotState.WORKING,
                seed,
                pointer
            )
        )
        assertEquals(
            Gaze(),
            MascotMotion.target(
                start,
                MascotState.OFFLINE,
                seed,
                pointer
            )
        )
        assertEquals(
            Gaze(),
            MascotMotion.target(
                start + .3,
                MascotState.IDLE,
                seed,
                cue = MascotCue(CueKind.TALK, start)
            )
        )
        assertTrue(MascotMotion.target(start, MascotState.WORKING, seed).y > .4)
        assertTrue(MascotMotion.target(start, MascotState.ERROR, seed).x < 0)
    }

    @Test
    fun eyesAndHeadFollowGazeWithinTravelLimits() {
        val right = MascotMotion.frame(
            start,
            MascotState.IDLE,
            start,
            seed,
            look = Gaze(1.0, 0.0)
        )
        val left = MascotMotion.frame(
            start,
            MascotState.IDLE,
            start,
            seed,
            look = Gaze(-1.0, 0.0)
        )
        assertEquals(.016, right.eyeX, 1e-9)
        assertEquals(-.016, left.eyeX, 1e-9)
        assertEquals(6.0, right.rotation - left.rotation, 1e-9)
        assertTrue(right.x > left.x)
    }

    @Test
    fun reactionsStartAndEndWithoutChangingOtherFrames() {
        CueKind.entries.forEach { kind ->
            val cue = MascotCue(kind, start)
            assertNull(cue.progress(start - .01))
            assertEquals(0.0, cue.progress(start)!!, 0.0)
            assertNull(cue.progress(start + kind.duration + .01))
            assertEquals(frame(start - .01), frame(start - .01, cue = cue))
            assertEquals(frame(start + kind.duration + .01), frame(start + kind.duration + .01, cue = cue))
            assertNotEquals(frame(start + .2), frame(start + .2, cue = cue))
        }
    }

    @Test
    fun pokeSquashesHopsAndLands() {
        val cue = MascotCue(CueKind.POKE, start)
        val pairs = (0..59).map {
            val t = start + it * .01
            frame(t, cue = cue) to frame(t)
        }
        assertTrue(pairs.minOf { it.first.y - it.second.y } < -.07)
        assertTrue(pairs.take(15).minOf { it.first.scaleY / it.second.scaleY } < .92)
        assertTrue(pairs.maxOf { it.first.scaleY / it.second.scaleY } > 1.05)
        assertEquals(1.0, frame(start + .15, cue = cue).closure, 1e-9)
    }

    @Test
    fun reactionsComposeWithDoneErrorAndOffline() {
        val cue = MascotCue(CueKind.POKE, start)
        assertTrue(frame(start + .27, MascotState.DONE, cue).y < frame(start + .27, MascotState.DONE).y - .05)
        assertEquals(.4, frame(start + .3, MascotState.OFFLINE, cue).saturation, 0.0)
        assertEquals(0.0, frame(start + .3, MascotState.OFFLINE, cue).closure, 1e-9)
        assertEquals(
            frame(start + .25, MascotState.OFFLINE),
            frame(start + .25, MascotState.OFFLINE, MascotCue(CueKind.GREET, start))
        )
        assertTrue(frame(start + .3, MascotState.ERROR, cue).rotation < -2)
    }

    @Test
    fun reducedMotionIsStaticAndSmallAvatarsOnlyMoveEyes() {
        MascotState.entries.forEach { state ->
            assertEquals(
                MascotMotion.frame(
                    0.0,
                    state,
                    0.0,
                    seed,
                    reduced = true
                ),
                MascotMotion.frame(
                    start,
                    state,
                    start,
                    seed,
                    MascotCue(CueKind.POKE, start),
                    true
                )
            )
        }
        val still = MascotMotion.frame(
            0.0,
            MascotState.WORKING,
            0.0,
            seed,
            reduced = true
        )
        val subtle = MascotMotion.frame(
            start,
            MascotState.WORKING,
            start,
            seed,
            look = Gaze(-1.0, 0.0),
            subtle = true
        )
        assertEquals(still.y, subtle.y, 0.0)
        assertEquals(still.rotation, subtle.rotation, 0.0)
        assertEquals(-.016, subtle.eyeX, 1e-9)
    }

    @Test
    fun runAndPluginStatesMatchPhone() {
        assertEquals(MascotState.WORKING, MascotState.run(testRun(status = "running"), testNow))
        assertEquals(MascotState.ERROR, MascotState.run(testRun(status = "cancelled"), testNow))
        assertEquals(MascotState.DONE, MascotState.run(testRun(ago = 2, seconds = 1), testNow))
        assertEquals(MascotState.IDLE, MascotState.run(testRun(), testNow))
        val p = PluginView(
            "health",
            "健康",
            state = "enabled",
            enabled = true
        )
        assertEquals(
            MascotState.WORKING,
            MascotState.plugin(p, listOf(testRun("plugin:health:morning_report", "running")), testNow)
        )
        assertEquals(MascotState.OFFLINE, MascotState.plugin(p.copy(state = "disabled"), emptyList(), testNow))
    }

    @Test
    fun knowledgeAndAssistantChooseActiveJobsThenErrors() {
        val p = PluginView("goodnotes", "笔记", state = "error")
        assertEquals(MascotState.ERROR, MascotState.knowledge(listOf(p), emptyList(), testNow))
        assertEquals(
            MascotState.WORKING,
            MascotState.knowledge(listOf(p), listOf(testRun("knowledge_organize", "running")), testNow)
        )
        assertEquals(MascotState.OFFLINE, MascotState.assistant(null, listOf(testRun(status = "running")), testNow))
        assertEquals(MascotState.OFFLINE, MascotState.combined(MascotState.entries))
        assertEquals(MascotState.IDLE, MascotState.combined(emptyList()))
    }

    @Test
    fun stateMachineTransitionsAndDoneTimeout() {
        val machine = MascotStateMachine()
        machine.handle(MascotEvent.STARTED, 10.0)
        assertEquals(MascotState.WORKING, machine.state(10.0))
        machine.handle(MascotEvent.FAILED, 11.0)
        assertEquals(MascotState.ERROR, machine.state(20.0))
        machine.handle(MascotEvent.SUCCEEDED, 21.0)
        assertEquals(MascotState.DONE, machine.state(23.9))
        assertEquals(MascotState.IDLE, machine.state(24.0))
    }

    @Test
    fun offlineWinsUntilReconnectAndRepeatedSuccessRestartsBounce() {
        val machine = MascotStateMachine()
        machine.handle(MascotEvent.DISCONNECTED, 1.0)
        machine.handle(MascotEvent.SUCCEEDED, 2.0)
        machine.handle(MascotEvent.STARTED, 3.0)
        assertEquals(MascotState.OFFLINE, machine.state(10.0))
        machine.handle(MascotEvent.CONNECTED, 11.0)
        assertEquals(MascotState.IDLE, machine.state(11.0))
        machine.handle(MascotEvent.SUCCEEDED, 12.0)
        machine.handle(MascotEvent.SUCCEEDED, 14.0)
        assertEquals(14.0, machine.since, 0.0)
        assertEquals(MascotState.DONE, machine.state(16.0))
    }

    @Test
    fun catalogContainsAll24ActsAndWorkingChoices() {
        assertEquals(24, MascotMotion.acts.size)
        assertEquals(24, MascotMotion.acts.map { it.id }.toSet().size)
        assertEquals("heartbeat", MascotAnimationPlayer.working("lily_veil"))
        assertEquals("wave", MascotAnimationPlayer.celebration("aemeath"))
        assertNull(MascotAnimationPlayer.working("orange_daisy"))
    }

    @Test
    fun nightBoundariesAndOfflineSleep() {
        listOf(0, 5, 23)
            .forEach { hour ->
                assertEquals(
                    "sleep",
                    MascotAnimationPlayer(seed).sample(
                        "lily_veil",
                        MascotState.IDLE,
                        0.0,
                        hour
                    )?.act?.id
                )
            }
        listOf(6, 22).forEach { hour ->
            assertNull(
                MascotAnimationPlayer(seed).sample(
                    "lily_veil",
                    MascotState.IDLE,
                    0.0,
                    hour
                )
            )
        }
        assertEquals(
            "sleep",
            MascotAnimationPlayer(seed).sample(
                "lily_veil",
                MascotState.OFFLINE,
                0.0,
                12
            )?.act?.id
        )
    }

    @Test
    fun idleGapsAreVariedAndNeverRepeatConsecutively() {
        val player = MascotAnimationPlayer(seed)
        player.sample(
            "aemeath",
            MascotState.IDLE,
            0.0,
            12
        )
        var now = 0.0
        var previous: String? = null
        val gaps = mutableSetOf<Int>()
        repeat(30) {
            val next = player.nextIdleAt!!
            gaps += (next - now).toInt()
            assertTrue(next - now in 20.0..40.0)
            now = next
            val act = player.sample(
                "aemeath",
                MascotState.IDLE,
                now,
                12
            )!!
            assertNotEquals(previous, act.act.id)
            previous = act.act.id
            player.sample(
                "aemeath",
                MascotState.IDLE,
                now + act.act.duration + .01,
                12
            )
        }
        assertTrue(gaps.size > 2)
    }

    @Test
    fun previewPokeAndStateInterruptions() {
        val p = MascotAnimationPlayer(seed)
        assertEquals(
            "writing",
            p.sample(
                "moning",
                MascotState.WORKING,
                10.0,
                12
            )?.act?.id
        )
        assertEquals(
            "alarm",
            p.sample(
                "moning",
                MascotState.WORKING,
                11.0,
                12,
                AnimationRequest(1, 11.0, "alarm")
            )?.act?.id
        )
        assertEquals(
            "books",
            p.sample(
                "moning",
                MascotState.DONE,
                12.0,
                12
            )?.act?.id
        )
        assertNotNull(
            p.sample(
                "moning",
                MascotState.DONE,
                13.0,
                12,
                AnimationRequest(2, 13.0)
            )
        )
    }

    @Test
    fun disabledReducedInactiveUnknownStaleAndWrongRequestsAreGated() {
        assertNull(
            MascotAnimationPlayer(seed).sample(
                "unknown",
                MascotState.WORKING,
                10.0,
                12
            )
        )
        assertNull(
            MascotAnimationPlayer(seed).sample(
                "aemeath",
                MascotState.WORKING,
                10.0,
                12,
                reduced = true
            )
        )
        assertNull(
            MascotAnimationPlayer(seed).sample(
                "aemeath",
                MascotState.WORKING,
                10.0,
                12,
                active = false
            )
        )
        assertNull(
            MascotAnimationPlayer(seed).sample(
                "aemeath",
                MascotState.IDLE,
                10.0,
                12,
                AnimationRequest(1, 0.0, "wave")
            )
        )
        assertNull(
            MascotAnimationPlayer(seed).sample(
                "aemeath",
                MascotState.IDLE,
                10.0,
                12,
                AnimationRequest(2, 10.0, "alarm")
            )
        )
    }

    @Test
    fun playbackPhaseVisibilityAndFrames() {
        val act = MascotMotion.acts.first { it.id == "sleep" }
        val loop = AnimationPlayback(act, 6.0, true)
        assertEquals(0.0, loop.phase, 1e-9)
        assertEquals(1.0, loop.visibility, 0.0)
        assertEquals(0.0, AnimationPlayback(act, 6.0).visibility, 0.0)
        assertEquals(1.0, loop.applying(MascotFrame()).closure, 0.0)
        assertEquals(3.0, loop.applying(MascotFrame()).rotation, 0.0)
    }

    @Test
    fun pokeBurstIsDeterministicFliesOutAndFades() {
        val cue = MascotCue(CueKind.POKE, start)
        assertTrue(MascotMotion.burst(cue, start - .01).isEmpty())
        assertTrue(MascotMotion.burst(cue, start + .901).isEmpty())
        val early = MascotMotion.burst(cue, start + .1)
        val late = MascotMotion.burst(cue, start + .6)
        assertEquals(6, early.size)
        assertEquals(setOf(true, false), early.map { it.heart }.toSet())
        assertEquals(early, MascotMotion.burst(cue, start + .1))
        early.zip(late)
            .forEach { (a, b) ->
                assertTrue(hypot(b.x - .5, b.y - .4) > hypot(a.x - .5, a.y - .4))
                assertTrue(b.y < .4)
                assertTrue(b.opacity < 1)
                assertTrue(a.size > 0)
            }
    }

    @Test
    fun statePosesHaveNoSidewaysDriftAndKeepTheirEffects() {
        MascotState.entries.forEach { state -> assertEquals(0.0, frame(state = state).x, 0.0) }
        assertEquals(listOf("sweat"), frame(state = MascotState.ERROR).effects)
        assertEquals(listOf("zzz"), frame(state = MascotState.OFFLINE).effects)
    }
}
