package local.assistant.desktop.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.PointerEventType
import androidx.compose.ui.input.pointer.onPointerEvent
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import local.assistant.core.*
import local.assistant.desktop.Resources
import java.time.LocalTime
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.min

private data class Artwork(
    val base: ImageBitmap,
    val open: ImageBitmap?,
    val closed: ImageBitmap?,
    val mask: ImageBitmap?,
    val origin: IntOffset,
    val bounds: IntSize,
)

private val artworkCache = ConcurrentHashMap<String, Artwork>()

private fun loadArtwork(name: String): Artwork? {
    artworkCache[name]?.let { return it }
    val whole = Resources.image("$name.png") ?: Resources.image("placeholder.png") ?: return null
    val base = Resources.image("$name/base.png")
    val open = Resources.image("$name/eyes_open.png")
    val closed = Resources.image("$name/eyes_closed.png")
    val layered = base != null && open != null && closed != null
    val image = if (layered) base!! else whole
    var left = image.width
    var top = image.height
    var right = 0
    var bottom = 0
    for (y in 0 until whole.height) {
        for (x in 0 until whole.width) {
            if ((whole.getRGB(x, y) ushr 24) > 8) {
                left = minOf(left, x)
                top = minOf(top, y)
                right = maxOf(right, x + 1)
                bottom = maxOf(bottom, y + 1)
            }
        }
    }
    if (right <= left || bottom <= top) return null
    return Artwork(
        image.toComposeImageBitmap(),
        if (layered) open!!.toComposeImageBitmap() else null,
        if (layered) closed!!.toComposeImageBitmap() else null,
        if (layered) Resources.image("$name/eyes_mask.png")?.toComposeImageBitmap() else null,
        IntOffset(left, top), IntSize(right - left, bottom - top),
    ).also { artworkCache[name] = it }
}

@Composable
fun Avatar(bot: Bot, modifier: Modifier = Modifier) {
    Box(
        modifier.clip(CircleShape).background(accent(bot.look.accent).copy(alpha = 0.15f)),
        contentAlignment = Alignment.Center,
    ) {
        if (bot.kind == BotKind.SOURCE) {
            Label(if (bot.id == "qq_screen") "QQ" else bot.name.take(2), color = accent(bot.look.accent))
        } else {
            Mascot(bot.look.sticker, modifier = Modifier.fillMaxSize().padding(3.dp), animated = false)
        }
    }
}

@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
@Composable
fun Mascot(
    name: String?,
    state: MascotState = MascotState.IDLE,
    modifier: Modifier = Modifier,
    talkKey: String? = null,
    animated: Boolean = true,
    act: String? = null,
) {
    val resolved = Resources.catalog.resolved(name)
    val reduced = LocalReducedMotion.current || !animated || !LocalAnimationActive.current
    val art by produceState<Artwork?>(null, resolved) {
        value = withContext(Dispatchers.IO) { loadArtwork(resolved) }
    }
    val seed = remember(resolved) { MascotMotion.seed(resolved) }
    val player = remember(resolved) { MascotAnimationPlayer(seed) }
    var time by remember { mutableDoubleStateOf(System.nanoTime() / 1e9) }
    var cue by remember(resolved) { mutableStateOf<MascotCue?>(null) }
    var pointer by remember { mutableStateOf<Gaze?>(null) }
    var gaze by remember { mutableStateOf(Gaze()) }
    var request by remember { mutableStateOf<AnimationRequest?>(null) }
    val since = remember(state) { System.nanoTime() / 1e9 }
    LaunchedEffect(reduced) {
        if (!reduced) {
            while (true) {
                time = System.nanoTime() / 1e9
                delay(33)
            }
        }
    }
    LaunchedEffect(talkKey) {
        if (talkKey != null) cue = MascotCue(CueKind.TALK, System.nanoTime() / 1e9)
    }
    LaunchedEffect(act) {
        if (act != null) request = AnimationRequest(System.nanoTime(), System.nanoTime() / 1e9, act)
    }
    val artwork = art ?: return
    val target = MascotMotion.target(time, state, seed, pointer, cue)
    // Updated by the frame clock, rather than writing Compose state while drawing.
    LaunchedEffect(time, target) { gaze = gaze.follow(target, 0.033) }
    val playback = player.sample(resolved, state, time, LocalTime.now().hour, request, reduced = reduced)
    val original = MascotMotion.frame(time, state, since, seed, cue, reduced, gaze)
    val frame = playback?.applying(original) ?: original
    Canvas(
        modifier.onPointerEvent(PointerEventType.Move) { event ->
            val point = event.changes.firstOrNull()?.position
            pointer = point?.let { Gaze.toward(it.x / size.width.toDouble() - 0.5, it.y / size.height.toDouble() - 0.5) }
        }.onPointerEvent(PointerEventType.Exit) { pointer = null }
            .clickable(enabled = animated) {
                val now = System.nanoTime() / 1e9
                cue = MascotCue(CueKind.POKE, now)
                request = AnimationRequest(System.nanoTime(), now)
            },
    ) {
        val scale = min(size.width / artwork.bounds.width, size.height / artwork.bounds.height) * 0.86f
        val width = (artwork.bounds.width * scale).toInt().coerceAtLeast(1)
        val height = (artwork.bounds.height * scale).toInt().coerceAtLeast(1)
        val offset = IntOffset(((size.width - width) / 2).toInt(), ((size.height - height) / 2).toInt())
        val filter = ColorFilter.colorMatrix(ColorMatrix().apply { setToSaturation(frame.saturation.toFloat()) })
        val side = min(size.width, size.height)
        val unit = artwork.base.width * scale / side
        val placement = PropPlacement(
            unit.toDouble(),
            ((offset.x - artwork.origin.x * scale + artwork.base.width * scale / 2 - (size.width - side) / 2) / side).toDouble(),
            ((offset.y - artwork.origin.y * scale + artwork.base.height * scale / 2 - (size.height - side) / 2) / side).toDouble(),
        )
        val attached = playback?.act?.id in listOf("ribbon", "stars", "halo", "meteor", "earrings", "moon")
        withTransform({
            translate((frame.x * size.width).toFloat(), (frame.y * size.height).toFloat())
            rotate(frame.rotation.toFloat(), Offset(size.width / 2, size.height * 0.92f))
            scale(frame.scaleX.toFloat(), frame.scaleY.toFloat(), Offset(size.width / 2, size.height * 0.92f))
        }) {
            drawImage(artwork.base, artwork.origin, artwork.bounds, offset, IntSize(width, height), colorFilter = filter)
            val eyeOffset = offset + IntOffset((frame.eyeX * width).toInt(), (frame.eyeY * height).toInt())
            drawContext.canvas.saveLayer(androidx.compose.ui.geometry.Rect(Offset.Zero, size), Paint())
            artwork.open?.let {
                drawImage(it, artwork.origin, artwork.bounds, eyeOffset, IntSize(width, height), alpha = (1 - frame.closure).toFloat().coerceIn(0f, 1f), colorFilter = filter)
            }
            artwork.closed?.let {
                drawImage(it, artwork.origin, artwork.bounds, eyeOffset, IntSize(width, height), alpha = frame.closure.toFloat().coerceIn(0f, 1f), colorFilter = filter)
            }
            artwork.mask?.let {
                drawImage(it, artwork.origin, artwork.bounds, offset, IntSize(width, height), blendMode = BlendMode.DstIn)
            }
            drawContext.canvas.restore()
            if (attached && playback != null) {
                withTransform({ translate((size.width - side) / 2, (size.height - side) / 2) }) {
                    drawMascotProps(side, playback, placement)
                }
            }
        }
        if (!attached && playback != null) {
            withTransform({ translate((size.width - side) / 2, (size.height - side) / 2) }) {
                drawMascotProps(side, playback, placement)
            }
        }
        if (!reduced) {
            MascotMotion.burst(cue, time, seed = seed).forEach { particle ->
                drawCircle(
                    if (particle.heart) Color(0xFFFF6F9C) else Color(0xFFFFC94A),
                    (particle.size * size.width / 3).toFloat(),
                    Offset((particle.x * size.width).toFloat(), (particle.y * size.height).toFloat()),
                    alpha = particle.opacity.toFloat(),
                )
            }
        }
        if (state == MascotState.ERROR) drawCircle(Color(0xFF89B5D3), size.width * 0.035f, Offset(size.width * 0.8f, size.height * 0.2f))
        if (state == MascotState.WORKING) {
            val progress = ((time % 1.5) / 1.5).toFloat()
            repeat(3) { index ->
                drawCircle(Color(0xFFD97757), size.width * 0.018f, Offset(size.width * (0.7f + index * 0.06f), size.height * 0.85f), alpha = if (reduced) 0.7f else (0.3f + (progress + index / 3f) % 1 * 0.7f))
            }
        }
    }
}
