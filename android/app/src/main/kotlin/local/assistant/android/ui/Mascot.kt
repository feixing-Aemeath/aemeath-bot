package local.assistant.android.ui

import android.graphics.*
import android.provider.Settings
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.drawscope.inset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.*
import local.assistant.core.*
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.*

private data class Artwork(
    val base: Bitmap,
    val open: Bitmap?,
    val closed: Bitmap?,
    val mask: Bitmap?,
    val bounds: Rect,
    val eyeBounds: Rect?
)

private val artworkCache = ConcurrentHashMap<String, Artwork>()

@Composable
fun BotAvatar(bot: Bot, modifier: Modifier = Modifier) {
    if (bot.kind != BotKind.SOURCE) {
        BotAvatar(bot.look, modifier)
        return
    }
    val tint = accent(bot.look.accent)
    Box(
        modifier
            .clip(CircleShape)
            .background(tint.copy(alpha = .15f))
            .border(1.dp, tint.copy(alpha = .45f), CircleShape)
    ) {
        Canvas(Modifier.fillMaxSize()) {
            inset(size.width * .23f, size.height * .23f) {
                drawRoundRect(
                    tint,
                    androidx.compose.ui.geometry.Offset(0f, size.height * .12f),
                    androidx.compose.ui.geometry.Size(size.width, size.height * .64f),
                    androidx.compose.ui.geometry.CornerRadius(size.width * .18f)
                )
                drawPath(
                    androidx.compose.ui.graphics.Path()
                        .apply {
                            moveTo(size.width * .2f, size.height * .65f)
                            lineTo(size.width * .15f, size.height * .9f)
                            lineTo(size.width * .48f, size.height * .65f)
                            close()
                        },
                    tint
                )
            }
        }
    }
}

@Composable
fun BotAvatar(look: PluginMascot, modifier: Modifier = Modifier) {
    val tint = accent(look.accent)
    Box(
        modifier
            .clip(CircleShape)
            .background(tint.copy(alpha = .15f))
            .border(1.dp, tint.copy(alpha = .45f), CircleShape)
    ) { Mascot(look.sticker, modifier = Modifier.fillMaxSize(), animated = false) }
}

@Composable
fun Mascot(
    name: String?,
    state: MascotState = MascotState.IDLE,
    modifier: Modifier = Modifier,
    talkKey: String? = null,
    animated: Boolean = true,
    act: String? = null
) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val resolved = name?.takeIf { Regex("[a-zA-Z0-9_-]+").matches(it) } ?: "placeholder"
    val artwork by produceState<Artwork?>(null, resolved) {
        value = withContext(Dispatchers.IO) {
            artworkCache[resolved] ?: run {
                fun load(path: String): Bitmap? = runCatching {
                    context.assets.open(path).use {
                        BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = 2 })
                    }
                }
                    .getOrNull()
                val whole = load("$resolved.png") ?: load("placeholder.png") ?: return@withContext null
                val base = load("$resolved/base.png")
                val open = load("$resolved/eyes_open.png")
                val closed = load("$resolved/eyes_closed.png")
                fun bounds(bitmap: Bitmap): Rect {
                    var left = bitmap.width
                    var top = bitmap.height
                    var right = 0
                    var bottom = 0
                    val pixels = IntArray(bitmap.width * bitmap.height)
                    bitmap.getPixels(
                        pixels,
                        0,
                        bitmap.width,
                        0,
                        0,
                        bitmap.width,
                        bitmap.height
                    )
                    pixels.forEachIndexed { i, p ->
                        if (p ushr 24 > 0) {
                            val x = i % bitmap.width
                            val y = i / bitmap.width
                            left = min(left, x)
                            top = min(top, y)
                            right = max(right, x + 1)
                            bottom = max(bottom, y + 1)
                        }
                    }
                    return if (right > left) Rect(
                        left,
                        top,
                        right,
                        bottom
                    ) else Rect(
                        0,
                        0,
                        bitmap.width,
                        bitmap.height
                    )
                }
                val layered = base != null && open != null && closed != null
                Artwork(
                    if (layered) base!! else whole,
                    if (layered) open else null,
                    if (layered) closed else null,
                    load("$resolved/eyes_mask.png"),
                    bounds(whole),
                    if (layered) bounds(open!!) else null
                )
                    .also { artworkCache[resolved] = it }

            }
        }
    }
    val fixedTime = LocalAnimationTime.current
    val reduced = fixedTime != null || remember {
        Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
    }
    var time by remember { mutableDoubleStateOf(fixedTime?.epochSecond?.toDouble() ?: (System.currentTimeMillis() / 1000.0)) }
    var cue by remember { mutableStateOf<MascotCue?>(null) }
    var since by remember { mutableDoubleStateOf(time) }
    val seed = remember(resolved) { MascotMotion.seed(resolved) }
    var gaze by remember(resolved) { mutableStateOf(Gaze()) }
    val player = remember(resolved) { MascotAnimationPlayer(seed) }
    var request by remember(resolved) { mutableStateOf<AnimationRequest?>(null) }
    LaunchedEffect(state) { since = time }
    LaunchedEffect(talkKey) { if (talkKey != null) cue = MascotCue(CueKind.TALK, time) }
    LaunchedEffect(act) { if (act != null) request = AnimationRequest(System.nanoTime(), time, act) }
    LaunchedEffect(
        owner,
        animated,
        reduced,
        state,
        resolved,
        cue
    ) {
        if (animated && !reduced) owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (isActive) {
                val next = System.currentTimeMillis() / 1000.0
                gaze = gaze.follow(
                    MascotMotion.target(
                        next,
                        state,
                        seed,
                        cue = cue
                    ),
                    next - time
                )
                time = next
                delay(33)
            }
        }
    }
    val playback = player.sample(
        resolved,
        state,
        time,
        fixedTime?.atZone(java.time.ZoneId.systemDefault())?.hour ?: java.time.LocalTime.now().hour,
        request,
        animated,
        reduced
    )
    val frame = playback?.applying(
        MascotMotion.frame(
            time,
            state,
            since,
            seed,
            cue,
            reduced || !animated,
            look = gaze
        )
    )
    ?: MascotMotion.frame(
        time,
        state,
        since,
        seed,
        cue,
        reduced || !animated,
        look = gaze
    )
    val chosenAct = playback?.act?.id
    Canvas(
        modifier
            .testTag(if (artwork == null) "artwork-loading" else "artwork-ready")
            .semantics { contentDescription = resolved }
            .clickable(enabled = animated && !reduced) {
                cue = MascotCue(CueKind.POKE, time)
                request = AnimationRequest(System.nanoTime(), time)
            }
    ) {
        val art = artwork ?: return@Canvas
        drawIntoCanvas { wrapper ->
            val c = wrapper.nativeCanvas
            val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
            val side = min(size.width, size.height)
            val bounds = art.bounds
            val eyes = art.eyeBounds
            val scaleUnit = if (eyes != null) {
                if (animated)
                    min(.29f * art.base.width / eyes.width(), .94f * art.base.width / bounds.width())
                else
                    .46f * art.base.width / eyes.width()
            } else if (animated)
                min(.78f * art.base.width / bounds.width(), .68f * art.base.height / bounds.height())
            else
                min(.92f * art.base.height / bounds.height(), 1.3f * art.base.width / bounds.width())
            val centerX =
                if (animated)
                    .5f + (.5f - bounds.exactCenterX() / art.base.width) * scaleUnit
                else
                    .5f + (.5f - (eyes ?: bounds).exactCenterX() / art.base.width) * scaleUnit
            val centerY =
                if (eyes != null)
                    (if (animated) .66f else .52f) + (.5f - eyes.exactCenterY() / art.base.height) * scaleUnit
                else
                    (if (animated) .87f else .56f) +
                        (.5f - (if (animated) bounds.bottom.toFloat() else bounds.exactCenterY()) /
                            art.base.height) * scaleUnit
            val scale = side * scaleUnit / art.base.width
            val tx = (size.width - side) / 2 + side * (centerX - .5f * scaleUnit)
            val ty = (size.height - side) / 2 + side * (centerY - .5f * scaleUnit)
            val placement = PropPlacement(scaleUnit.toDouble(), centerX.toDouble(), centerY.toDouble())
            val attached = chosenAct in listOf(
                "ribbon",
                "stars",
                "halo",
                "meteor",
                "earrings",
                "moon"
            )
            c.save()
            c.translate(frame.x.toFloat() * side, frame.y.toFloat() * side)
            c.rotate(frame.rotation.toFloat(), size.width * .5f, size.height * .92f)
            c.scale(
                frame.scaleX.toFloat(),
                frame.scaleY.toFloat(),
                size.width * .5f,
                size.height * .92f
            )
            c.save()
            c.translate(tx, ty)
            c.scale(scale, scale)
            paint.colorFilter = ColorMatrixColorFilter(
                ColorMatrix().apply { setSaturation(frame.saturation.toFloat()) }
            )
            c.drawBitmap(
                art.base,
                0f,
                0f,
                paint
            )
            if (art.open != null && art.closed != null) {
                val layer = c.saveLayer(
                    0f,
                    0f,
                    art.base.width.toFloat(),
                    art.base.height.toFloat(),
                    null
                )
                val dx = frame.eyeX.toFloat() * art.base.width
                val dy = frame.eyeY.toFloat() * art.base.height
                paint.alpha = ((1 - frame.closure) * 255).toInt().coerceIn(0, 255)
                c.drawBitmap(
                    art.open,
                    dx,
                    dy,
                    paint
                )
                paint.alpha = (frame.closure * 255).toInt().coerceIn(0, 255)
                c.drawBitmap(
                    art.closed,
                    dx,
                    dy,
                    paint
                )
                art.mask?.let { mask ->
                    paint.alpha = 255
                    paint.xfermode = PorterDuffXfermode(PorterDuff.Mode.DST_IN)
                    c.drawBitmap(
                        mask,
                        0f,
                        0f,
                        paint
                    )
                    paint.xfermode = null
                }
                c.restoreToCount(layer)
            }
            c.restore()
            if (attached && playback != null) drawMascotProps(
                c,
                side,
                playback,
                placement
            )
            c.restore()
            paint.alpha = 255
            paint.colorFilter = null
            if (!attached && playback != null) drawMascotProps(
                c,
                side,
                playback,
                placement
            )
            if (state == MascotState.ERROR || state == MascotState.OFFLINE && chosenAct != "sleep") {
                val symbol = if (state == MascotState.ERROR) "💧" else if (state == MascotState.OFFLINE) "Zzz" else ""
                paint.textSize = side * .18f
                paint.color = android.graphics.Color.rgb(128, 104, 178)
                c.drawText(
                    symbol,
                    side * .65f,
                    side * .22f,
                    paint
                )
            }
            if (!reduced) MascotMotion.burst(cue, time, seed = seed)
                .forEach { particle ->
                    paint.color =
                        if (particle.heart) android.graphics.Color.rgb(255, 111, 156) else android.graphics.Color.rgb(
                            255,
                            201,
                            74
                        )
                    paint.alpha = (particle.opacity * 255).toInt().coerceIn(0, 255)
                    paint.textSize = (side * particle.size).toFloat()
                    val x = (side * particle.x).toFloat()
                    val y = (side * particle.y).toFloat()
                    c.save()
                    c.rotate(particle.rotation.toFloat(), x, y)
                    c.drawText(
                        if (particle.heart) "♥" else "✦",
                        x,
                        y,
                        paint
                    )
                    c.restore()
                }
        }
    }
}
