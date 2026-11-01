package local.assistant.android.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.*
import local.assistant.core.parseAccent

data class AppPalette(
    val sidebar: Color,
    val page: Color,
    val card: Color,
    val hairline: Color,
    val selection: Color,
    val bubble: Color,
    val trail: Color,
    val clay: Color = Color(0xFFD97757)
)

val LocalPalette = staticCompositionLocalOf {
    AppPalette(
        Color(0xFFF2F0E9),
        Color(0xFFFAF9F5),
        Color.White,
        Color(0xFFE6E3DA),
        Color(0xFFE2DFD5),
        Color(0xFFEFEDE5),
        Color(0xFFC9C4B7)
    )
}

val LocalCardStyle = staticCompositionLocalOf { "plain" }

/** Fixed time disables motion for previews without changing character placement. */
val LocalAnimationTime = staticCompositionLocalOf<java.time.Instant?> { null }

val LocalAccent = staticCompositionLocalOf { Color(0xFFE86A9A) }

@Composable
fun AemeathTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val p = if (dark) AppPalette(
        Color(0xFF1F1E1C),
        Color(0xFF262624),
        Color(0xFF30302E),
        Color(0xFF3E3E3A),
        Color(0xFF3A3A37),
        Color(0xFF393937),
        Color(0xFF5E5C56)
    ) else LocalPalette.current
    val scheme = if (dark) darkColorScheme(
        primary = p.clay,
        background = p.page,
        surface = p.card,
        surfaceVariant = p.sidebar,
        outline = p.hairline,
        onSurface = Color(0xFFECEAE4),
        onBackground = Color(0xFFECEAE4)
    ) else lightColorScheme(
        primary = p.clay,
        background = p.page,
        surface = p.card,
        surfaceVariant = p.sidebar,
        outline = p.hairline,
        onSurface = Color(0xFF292822),
        onBackground = Color(0xFF292822)
    )
    val neutral = scheme.copy(
        secondary = p.clay,
        tertiary = p.clay,
        primaryContainer = p.selection,
        secondaryContainer = p.selection,
        tertiaryContainer = p.selection,
        onPrimaryContainer = scheme.onSurface,
        onSecondaryContainer = scheme.onSurface,
        onTertiaryContainer = scheme.onSurface,
        onSurfaceVariant = if (dark) Color(0xFFB2B0A8) else Color(0xFF6B6962),
        outlineVariant = p.hairline
    )
    CompositionLocalProvider(LocalPalette provides p) {
        MaterialTheme(colorScheme = neutral, typography = Typography(), content = content)
    }
}

fun accent(hex: String?) = Color(parseAccent(hex) ?: 0xFF8C96A8.toInt())

@Composable
fun Serif(
    text: String,
    size: Int = 24,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.onSurface
) {
    Text(
        text,
        modifier,
        fontSize = size.sp,
        fontFamily = FontFamily.Serif,
        fontWeight = FontWeight.SemiBold,
        color = color
    )
}

@Composable
fun Muted(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier,
        fontSize = 12.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant
    )
}

@Composable
fun CardBox(title: String? = null, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val p = LocalPalette.current
    val tint = LocalAccent.current
    val style = LocalCardStyle.current
    val shape = RoundedCornerShape(12.dp)
    Column(
        modifier
            .fillMaxWidth()
            .clip(shape)
            .background(p.card)
            .then(if (style == "soft") Modifier.background(tint.copy(alpha = .08f)) else Modifier)
            .border(1.dp, if (style == "soft") tint.copy(alpha = .22f) else p.hairline, shape)
    ) {
        if (style == "accent") Box(
            Modifier
                .fillMaxWidth()
                .height(3.dp)
                .background(tint)
        )
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (title != null) Serif(
                (if (style == "accent") "▧  " else "") + title,
                15,
                color = if (style == "soft") tint else MaterialTheme.colorScheme.onSurface
            )
            content()
        }
    }
}

@Composable
fun SettingRow(title: String, detail: String? = null, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 15.dp, horizontal = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text(title, Modifier.weight(1f), fontSize = 16.sp)
        detail?.let { Muted(it) }
        Muted("›")
    }
}

@Composable
fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Muted(label, Modifier.width(64.dp))
        Text(value, Modifier.weight(1f), fontSize = 14.sp)
    }
}
