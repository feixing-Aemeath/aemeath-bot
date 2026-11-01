package local.assistant.desktop.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import kotlin.math.cos
import kotlin.math.sin

/** Small native vectors in place of SF Symbols; no additional image or font assets. */
@Composable
fun CardIllustration(symbol: String) {
    val tint = LocalBotTint.current
    Canvas(Modifier.size(38.dp)) {
        val unit = size.width / 40
        if ("sun" in symbol) {
            val center = Offset(16 * unit, 14 * unit)
            drawCircle(Color(0xFFE8AE46), 7 * unit, center)
            repeat(8) { index ->
                val angle = index * Math.PI / 4
                val direction = Offset(cos(angle).toFloat(), sin(angle).toFloat())
                drawLine(Color(0xFFE8AE46), center + direction * (10 * unit), center + direction * (13 * unit), 2 * unit)
            }
        }
        if ("cloud" in symbol || "rain" in symbol || "snow" in symbol) {
            val cloud = Path().apply {
                moveTo(8 * unit, 29 * unit)
                cubicTo(-1 * unit, 27 * unit, 1 * unit, 17 * unit, 10 * unit, 17 * unit)
                cubicTo(12 * unit, 4 * unit, 29 * unit, 7 * unit, 30 * unit, 19 * unit)
                cubicTo(42 * unit, 18 * unit, 42 * unit, 31 * unit, 31 * unit, 30 * unit)
                close()
            }
            drawPath(cloud, Color(0xFFAABAC4))
            if ("rain" in symbol) repeat(3) { index ->
                drawLine(Color(0xFF729CC0), Offset((12 + index * 9) * unit, 33 * unit), Offset((9 + index * 9) * unit, 39 * unit), 2 * unit)
            }
            if ("snow" in symbol) repeat(3) { index ->
                drawCircle(Color(0xFF729CC0), 1.5f * unit, Offset((10 + index * 10) * unit, 36 * unit))
            }
        } else if ("moon" in symbol) {
            val moon = Path().apply {
                moveTo(27 * unit, 4 * unit)
                cubicTo(0f, -1 * unit, -2 * unit, 39 * unit, 31 * unit, 34 * unit)
                cubicTo(13 * unit, 30 * unit, 13 * unit, 12 * unit, 27 * unit, 4 * unit)
            }
            drawPath(moon, Color(0xFFBCA7D4))
        } else if ("sun" !in symbol) {
            drawRoundRect(tint.copy(alpha = 0.14f), Offset(4 * unit, 4 * unit), Size(32 * unit, 32 * unit), CornerRadius(7 * unit))
            drawRoundRect(tint, Offset(10 * unit, 10 * unit), Size(20 * unit, 22 * unit), CornerRadius(2 * unit), style = Stroke(1.5f * unit))
            repeat(3) { index ->
                drawLine(tint, Offset(14 * unit, (16 + index * 5) * unit), Offset(26 * unit, (16 + index * 5) * unit), 1.5f * unit)
            }
        }
    }
}
