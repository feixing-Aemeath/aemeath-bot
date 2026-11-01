package local.assistant.desktop.ui

import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.graphics.Color as ComposeColor
import androidx.compose.ui.graphics.Paint as ComposePaint
import androidx.compose.ui.graphics.PaintingStyle
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.nativeCanvas
import org.jetbrains.skia.Font
import org.jetbrains.skia.FontMgr
import org.jetbrains.skia.FontStyle

// Small vector drawing adapter for the existing mascot geometry, without Android dependencies.
internal class PropCanvas(private val scope: DrawScope) {
    private val canvas get() = scope.drawContext.canvas
    fun save() = canvas.save()
    fun restore() = canvas.restore()
    fun translate(x: Float, y: Float) = canvas.translate(x, y)
    fun scale(x: Float, y: Float) = canvas.scale(x, y)
    fun rotate(degrees: Float) = canvas.rotate(degrees)
    fun rotate(degrees: Float, x: Float, y: Float) {
        canvas.translate(x, y)
        canvas.rotate(degrees)
        canvas.translate(-x, -y)
    }
    fun clipRect(left: Float, top: Float, right: Float, bottom: Float) = canvas.clipRect(left, top, right, bottom)
    fun saveLayerAlpha(left: Float, top: Float, right: Float, bottom: Float, alpha: Int) {
        canvas.saveLayer(Rect(left, top, right, bottom), ComposePaint().apply { this.alpha = alpha / 255f })
    }
    fun drawPath(path: PropPath, paint: PropPaint) {
        canvas.drawPath(path.path, ComposePaint().apply {
            color = ComposeColor(paint.color)
            style = if (paint.style == PropPaint.Style.STROKE) PaintingStyle.Stroke else PaintingStyle.Fill
            strokeWidth = paint.strokeWidth
            strokeCap = StrokeCap.Round
            strokeJoin = StrokeJoin.Round
            isAntiAlias = true
        })
    }
    fun drawText(value: String, x: Float, y: Float, paint: PropPaint) {
        val nativePaint = org.jetbrains.skia.Paint().apply {
            color = paint.color
            isAntiAlias = true
        }
        paint.font().use { font ->
            canvas.nativeCanvas.drawString(value, x - font.measureTextWidth(value) / 2, y, font, nativePaint)
        }
        nativePaint.close()
    }
}

internal class PropPaint {
    enum class Style { FILL, STROKE }
    var color: Int = PropColor.WHITE
    var style = Style.FILL
    var strokeWidth = 2f
    var textSize = 14f
    fun font(): Font = Font(typeface, textSize)
    val fontMetrics: org.jetbrains.skia.FontMetrics
        get() = font().use { it.metrics }

    companion object {
        private val typeface by lazy {
            FontMgr.default.matchFamilyStyle("Microsoft YaHei", FontStyle.BOLD)
                ?: FontMgr.default.matchFamilyStyle("SansSerif", FontStyle.BOLD)
        }
    }
}

internal class PropPath {
    val path = androidx.compose.ui.graphics.Path()
    fun moveTo(x: Float, y: Float) = path.moveTo(x, y)
    fun lineTo(x: Float, y: Float) = path.lineTo(x, y)
    fun quadTo(x1: Float, y1: Float, x2: Float, y2: Float) = path.quadraticTo(x1, y1, x2, y2)
    fun cubicTo(x1: Float, y1: Float, x2: Float, y2: Float, x3: Float, y3: Float) = path.cubicTo(x1, y1, x2, y2, x3, y3)
    fun close() = path.close()
    fun addRoundRect(left: Float, top: Float, right: Float, bottom: Float, xRadius: Float, yRadius: Float) {
        path.addRoundRect(RoundRect(Rect(left, top, right, bottom), CornerRadius(xRadius, yRadius)))
    }
    fun addOval(left: Float, top: Float, right: Float, bottom: Float) = path.addOval(Rect(left, top, right, bottom))
    fun arcTo(left: Float, top: Float, right: Float, bottom: Float, start: Float, sweep: Float, move: Boolean) {
        path.arcTo(Rect(left, top, right, bottom), start, sweep, move)
    }
}

internal class PropPathMeasure(path: PropPath) {
    private val measure = androidx.compose.ui.graphics.PathMeasure().apply { setPath(path.path, false) }
    val length get() = measure.length
    fun getSegment(start: Float, stop: Float, destination: PropPath, move: Boolean) {
        measure.getSegment(start, stop, destination.path, move)
    }
}

internal object PropColor {
    val WHITE = 0xFFFFFFFF.toInt()
    val LTGRAY = 0xFFCCCCCC.toInt()
    fun rgb(red: Int, green: Int, blue: Int): Int = (0xFF000000 or (red.toLong() shl 16) or (green.toLong() shl 8) or blue.toLong()).toInt()
    fun parseColor(hex: String): Int = (0xFF000000 or hex.removePrefix("#").toLong(16)).toInt()
}
