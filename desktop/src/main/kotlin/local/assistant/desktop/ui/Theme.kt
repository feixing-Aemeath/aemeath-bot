@file:OptIn(androidx.compose.ui.text.ExperimentalTextApi::class)

package local.assistant.desktop.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.sp
import com.sun.jna.Native
import com.sun.jna.platform.win32.Advapi32Util
import com.sun.jna.platform.win32.WinReg
import com.sun.jna.ptr.IntByReference
import com.sun.jna.win32.StdCallLibrary
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import local.assistant.core.parseAccent
import local.assistant.desktop.isWindows
import java.awt.GraphicsEnvironment
import local.assistant.desktop.InlineMarkdown
import local.assistant.desktop.InlineStyle

data class Palette(val dark: Boolean) {
    private fun pair(light: Long, dark: Long): Color = Color(0xFF000000 or if (this.dark) dark else light)
    val sidebar = pair(0xF2F0E9, 0x1F1E1C)
    val page = pair(0xFAF9F5, 0x262624)
    val card = pair(0xFFFFFF, 0x30302E)
    val hairline = pair(0xE6E3DA, 0x3E3E3A)
    val selection = pair(0xE2DFD5, 0x3A3A37)
    val hover = pair(0xEAE7DF, 0x2C2C2A)
    val bubble = pair(0xEFEDE5, 0x393937)
    val trail = pair(0xC9C4B7, 0x5E5C56)
    val clay = Color(0xFFD97757)
    val ink = pair(0x292722, 0xEFEEE8)
    val muted = pair(0x777369, 0xACA99F)
}

val LocalPalette = staticCompositionLocalOf { Palette(false) }
val LocalReducedMotion = staticCompositionLocalOf { false }
val LocalAnimationActive = staticCompositionLocalOf { true }
val LocalCardStyle = staticCompositionLocalOf { "plain" }
val LocalBotTint = staticCompositionLocalOf { Color(0xFFD97757) }

internal interface WindowsSettings : StdCallLibrary {
    fun SystemParametersInfoW(action: Int, parameter: Int, value: IntByReference, flags: Int): Boolean
}

private fun windowsDark(fallback: Boolean): Boolean = if (!isWindows()) fallback else runCatching {
    Advapi32Util.registryGetIntValue(
        WinReg.HKEY_CURRENT_USER,
        "Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
        "AppsUseLightTheme",
    ) == 0
}.getOrDefault(fallback)

private fun reducedMotion(): Boolean = if (!isWindows()) false else runCatching {
    val enabled = IntByReference(1)
    Native.load("user32", WindowsSettings::class.java)
        .SystemParametersInfoW(0x1042, 0, enabled, 0) && enabled.value == 0
}.getOrDefault(false)

@Composable
fun DesktopTheme(content: @Composable () -> Unit) {
    val systemDark = isSystemInDarkTheme()
    var dark by remember { mutableStateOf(systemDark) }
    var reduced by remember { mutableStateOf(false) }
    LaunchedEffect(systemDark) {
        while (true) {
            val settings = withContext(Dispatchers.IO) { windowsDark(systemDark) to reducedMotion() }
            dark = settings.first
            reduced = settings.second
            delay(2_000)
        }
    }
    val palette = remember(dark) { Palette(dark) }
    val colors = if (dark) darkColors() else lightColors()
    CompositionLocalProvider(LocalPalette provides palette, LocalReducedMotion provides reduced) {
        MaterialTheme(
            colors = colors.copy(
                primary = palette.clay,
                secondary = palette.clay,
                background = palette.page,
                surface = palette.card,
                onSurface = palette.ink,
                onBackground = palette.ink,
                onPrimary = Color.White,
            ),
            typography = Typography(defaultFontFamily = Fonts.body),
            content = content,
        )
    }
}

private object Fonts {
    private val installed = GraphicsEnvironment.getLocalGraphicsEnvironment().availableFontFamilyNames.toSet()
    private fun choose(vararg names: String): String = names.firstOrNull { it in installed } ?: names.last()
    val body = FontFamily(choose("Segoe UI", "Helvetica Neue", "SansSerif"))
    private val chineseBody = FontFamily(choose("Microsoft YaHei", "微软雅黑", "PingFang SC", "Noto Sans CJK SC", "SansSerif"))
    private val title = FontFamily(choose("Georgia", "Cambria", "Serif"))
    private val chineseTitle = FontFamily(choose("Noto Serif SC", "Source Han Serif SC", "Noto Serif CJK SC", "思源宋体", "SimSun", "宋体", "Songti SC", "Serif"))

    fun styled(text: String, serif: Boolean): AnnotatedString = buildAnnotatedString {
        append(text)
        addStyle(SpanStyle(fontFamily = if (serif) title else body), 0, text.length)
        Regex("[\\u2E80-\\uFFFF]+").findAll(text).forEach { run ->
            addStyle(SpanStyle(fontFamily = if (serif) chineseTitle else chineseBody), run.range.first, run.range.last + 1)
        }
    }
}

@Composable
fun Label(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = LocalPalette.current.ink,
    size: TextUnit = 14.sp,
    weight: FontWeight = FontWeight.Normal,
    serif: Boolean = false,
    maxLines: Int = Int.MAX_VALUE,
) {
    Text(
        remember(text, serif) { Fonts.styled(text, serif) },
        modifier = modifier,
        color = color,
        fontSize = size,
        fontWeight = weight,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )
}

@Composable
fun Muted(text: String, modifier: Modifier = Modifier) {
    Label(text, modifier, color = LocalPalette.current.muted, size = 12.sp)
}

@Composable
fun Serif(text: String, size: Int = 24, modifier: Modifier = Modifier) {
    Label(text, modifier, size = size.sp, weight = FontWeight.SemiBold, serif = true)
}

@Composable
fun InlineText(text: String, code: Boolean = false) {
    val palette = LocalPalette.current
    val annotated = remember(text, code, palette.dark) {
        buildAnnotatedString {
            for (run in InlineMarkdown.parse(text, InlineStyle(code = code))) {
                val start = length
                append(Fonts.styled(run.text, false))
                val end = length
                addStyle(SpanStyle(
                    fontWeight = if (run.style.bold) FontWeight.Bold else FontWeight.Normal,
                    fontStyle = if (run.style.italic) FontStyle.Italic else FontStyle.Normal,
                    background = if (run.style.code) palette.bubble else Color.Unspecified,
                    fontFamily = if (run.style.code) FontFamily.Monospace else null,
                    color = if (run.style.link != null) palette.clay else palette.ink,
                    textDecoration = if (run.style.link != null) TextDecoration.Underline else null,
                ), start, end)
                run.style.link?.let { addStringAnnotation("url", it, start, end) }
            }
        }
    }
    androidx.compose.foundation.text.ClickableText(
        annotated,
        style = MaterialTheme.typography.body1.copy(color = palette.ink, fontSize = 14.sp),
        onClick = { offset ->
            val target = annotated.getStringAnnotations("url", offset, offset).firstOrNull()?.item
            if (target != null) runCatching {
                val uri = java.net.URI(target)
                // No network request in the App; a browser opens only after an explicit link click.
                if (uri.scheme in listOf("http", "https") && java.awt.Desktop.isDesktopSupported()) {
                    java.awt.Desktop.getDesktop().browse(uri)
                }
            }
        },
    )
}

fun accent(hex: String): Color = parseAccent(hex)?.let { Color(it) } ?: Color(0xFF8C96A8)
