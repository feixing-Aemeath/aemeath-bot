package local.assistant.android.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.*
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.*
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.*
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.*
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.*
import local.assistant.core.*
import kotlin.math.*

@Composable
fun MarkdownText(source: String, modifier: Modifier = Modifier) {
    val tint = LocalAccent.current
    val annotated = remember(source, tint) {
        buildAnnotatedString {
            Markdown.lines(source)
                .forEachIndexed { index, line ->
                    if (index > 0) append('\n')
                    val lineStyle =
                        if (line.heading) SpanStyle(fontWeight = FontWeight.SemiBold, fontSize = 17.sp) else SpanStyle()
                    withStyle(lineStyle) {
                        var cursor = 0
                        Regex("\\*\\*(.+?)\\*\\*|`([^`]+)`|\\[([^\\]]+)]\\(([^)]+)\\)|(?<!\\*)\\*([^*]+)\\*(?!\\*)")
                            .findAll(line.text)
                            .forEach { match ->
                                append(line.text.substring(cursor, match.range.first))
                                when {
                                    match.groups[1] != null -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                                        append(match.groupValues[1])
                                    }
                                    match.groups[2] != null -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) {
                                        append(match.groupValues[2])
                                    }
                                    match.groups[3] != null -> {
                                        val url = match.groupValues[4]
                                        if (url.startsWith("https://") || url.startsWith("http://")) withLink(
                                            LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = tint)))
                                        ) { append(match.groupValues[3]) }
                                        else append(match.groupValues[3])
                                    }
                                    else -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                                        append(match.groupValues[5])
                                    }
                                }
                                cursor = match.range.last + 1
                            }
                        append(line.text.substring(cursor))
                    }
                }
        }
    }
    SelectionContainer {
        Text(
            annotated,
            modifier,
            fontSize = 15.sp,
            lineHeight = 23.sp
        )
    }
}

@Composable
fun ServerCard(
    card: Card,
    onTodo: (Todo) -> Unit,
    onToggle: (Todo) -> Unit,
    onDelete: (Todo) -> Unit
) {
    val tint = LocalAccent.current
    CardBox(card.title) {
        when (val c = card.content) {
            is CardContent.Text -> {
                c.headline?.let { Text(it, fontWeight = FontWeight.Medium) }
                val table = c.table
                if (table != null) TableView(table) else MarkdownText(c.text)
                c.footnote?.let { Muted(it) }
            }
            is CardContent.Stat -> {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(
                            c.value,
                            fontSize = (if (c.wide) 38 else 30).sp,
                            fontWeight = FontWeight.SemiBold,
                            color = if (c.bar?.tone == "alert")
                                MaterialTheme.colorScheme.error
                            else if (c.wide) tint else MaterialTheme.colorScheme.onSurface
                        )
                        c.unit?.let { Muted(it) }
                    }
                    card.symbol?.let {
                        Text(
                            symbolText(it),
                            fontSize = 32.sp,
                            modifier = Modifier
                                .background(tint.copy(alpha = .12f), CircleShape)
                                .padding(10.dp)
                        )
                    }
                }
                c.bar?.let { bar ->
                    val colors = seriesColors(tint)
                    Canvas(
                        Modifier
                            .fillMaxWidth()
                            .height(7.dp)
                    ) {
                        drawRoundRect(
                            if (bar.tone == "alert") Color.Red.copy(alpha = .2f) else if (bar.tone == "warn") Color(
                                0xFFED9D43
                            ).copy(alpha = .2f) else Color.Gray.copy(alpha = .15f),
                            cornerRadius = CornerRadius(4.dp.toPx())
                        )
                        var x = 0f
                        bar.segments.forEachIndexed { i, s ->
                            val w =
                                if (bar.max > 0) (s.y.coerceAtLeast(0.0) / bar.max * size.width).toFloat().coerceAtMost(
                                    size.width - x
                                ) else 0f
                            if (w > 0) drawRect(colors[i % colors.size], Offset(x, 0f), Size(w, size.height))
                            x += w
                        }
                    }
                }
                c.label?.let { Muted(it) }
                c.delta?.let {
                    Muted(
                        (when (c.trend) {
                            "up" -> "↑ "
                            "down" -> "↓ "
                            "flat" -> "→ "
                            else -> ""
                        }) + it
                    )
                }
            }
            is CardContent.Items -> when (card.listStyle) {
                "rings" -> c.items.chunked(2)
                    .forEach { pair ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            pair.forEach { item -> RingItem(item, Modifier.weight(1f)) }
                            if (pair.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                "timeline" -> c.items.forEachIndexed { index, item -> TimelineItem(item, index == c.items.lastIndex) }
                else -> c.items.forEach { item ->
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Row(Modifier.fillMaxWidth()) {
                            Text(item.title, Modifier.weight(1f), fontSize = 14.sp)
                            item.detail?.let { Muted(it) }
                        }
                        item.subtitle?.let { Muted(it) }
                        item.progress?.let { p ->
                            LinearProgressIndicator(
                                progress = { p.fraction.toFloat() },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(5.dp),
                                color = toneColor(p.tone, tint),
                                trackColor = LocalPalette.current.hairline
                            )
                        }
                    }
                }
            }
            is CardContent.Chart -> ChartView(c)
            is CardContent.Photos -> c.photos.chunked(3)
                .forEach { photos ->
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        photos.forEach { photo ->
                            Column(Modifier.weight(1f)) {
                                Box(
                                    Modifier
                                        .fillMaxWidth()
                                        .aspectRatio(1f)
                                        .background(LocalPalette.current.bubble),
                                    contentAlignment = Alignment.Center
                                ) { Text("▧", fontSize = 30.sp) }
                                photo.caption?.let { Muted(it) }
                            }
                        }
                        repeat(3 - photos.size) { Spacer(Modifier.weight(1f)) }
                    }
                }
            is CardContent.Todos -> c.todos.forEach {
                TodoRow(
                    it,
                    { onTodo(it) },
                    { onToggle(it) },
                    { onDelete(it) }
                )
            }
            is CardContent.Unsupported -> Muted("这个版本还不能显示 ${c.type} 卡片。")
        }
    }
}

private fun toneColor(tone: String?, tint: Color) = when (tone) {
    "warn" -> Color(0xFFEA9A39)
    "alert" -> Color(0xFFD95850)
    else -> tint
}

@Composable
private fun RingItem(item: ListItem, modifier: Modifier) {
    val tint = LocalAccent.current
    val fraction = item.progress?.fraction ?: 0.0
    Column(
        modifier,
        verticalArrangement = Arrangement.spacedBy(6.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            Modifier
                .size(78.dp)
                .semantics { contentDescription = "${item.title} ${item.detail ?: "${(fraction*100).roundToInt()}%"}" },
            contentAlignment = Alignment.Center
        ) {
            Canvas(
                Modifier
                    .fillMaxSize()
                    .padding(4.dp)
            ) {
                drawCircle(Color.Gray.copy(alpha = .16f), style = Stroke(7.dp.toPx()))
                drawArc(
                    toneColor(item.progress?.tone, tint),
                    -90f,
                    (fraction * 360).toFloat(),
                    false,
                    style = Stroke(7.dp.toPx(), cap = StrokeCap.Round)
                )
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                val parts = item.detail?.split(" / ")
                Text(parts?.firstOrNull() ?: "${(fraction*100).roundToInt()}%", fontWeight = FontWeight.SemiBold)
                parts?.getOrNull(1)?.let { Muted("/ $it") }
            }
        }
        Text(
            item.title,
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center
        )
        item.subtitle?.let { Muted(it) }
    }
}

@Composable
private fun TimelineItem(item: ListItem, last: Boolean) {
    val tint = LocalAccent.current
    val times = item.subtitle?.split(Regex("[–~-]"))?.takeIf { it.size == 2 && it.all { t -> t.trim().length == 5 } }
    Row(
        Modifier
            .fillMaxWidth()
            .alpha(if (item.state == "done") .45f else 1f),
        horizontalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Column(Modifier.width(46.dp), horizontalAlignment = Alignment.End) {
            Text(times?.get(0).orEmpty(), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Muted(times?.get(1).orEmpty())
        }
        Canvas(
            Modifier
                .width(11.dp)
                .height(if (last) 20.dp else 66.dp)
        ) {
            drawCircle(
                tint,
                radius = 5.dp.toPx(),
                center = Offset(size.width / 2, 8.dp.toPx()),
                style = Stroke(2.dp.toPx())
            )
            if (item.state == "now") drawCircle(
                tint,
                radius = 2.dp.toPx(),
                center = Offset(size.width / 2, 8.dp.toPx())
            )
            if (!last) drawLine(
                tint.copy(alpha = .28f),
                Offset(size.width / 2, 14.dp.toPx()),
                Offset(size.width / 2, size.height),
                2.dp.toPx()
            )
        }
        Column(
            Modifier
                .weight(1f)
                .padding(bottom = if (last) 0.dp else 14.dp)
        ) {
            Text(
                item.title,
                fontSize = 14.sp,
                fontWeight = if (item.state == "now") FontWeight.SemiBold else FontWeight.Normal
            )
            if (times == null) item.subtitle?.let { Muted(it) }
            item.detail?.let { Muted(it) }
            if (item.state == "now") Text("正在进行", fontSize = 11.sp, color = tint)
        }
    }
}

@Composable
private fun TableView(table: CardTable) {
    val tint = LocalAccent.current
    val line = LocalPalette.current.hairline
    Column(Modifier.horizontalScroll(rememberScrollState())) {
        (listOf(table.columns) + table.rows).forEachIndexed { row, cells ->
            Row {
                cells.forEachIndexed { col, text ->
                    Box(
                        Modifier
                            .width(if (col == 0) 62.dp else 110.dp)
                            .heightIn(min = 42.dp)
                            .background(if (col == table.highlight) tint.copy(alpha = .12f) else Color.Transparent)
                            .border(.5.dp, line)
                            .padding(6.dp)
                    ) {
                        Text(
                            text,
                            fontSize = 12.sp,
                            fontWeight = if (row == 0 || col == 0) FontWeight.Medium else FontWeight.Normal
                        )
                    }
                }
            }
        }
    }
}

private fun seriesColors(tint: Color) = listOf(
    tint,
    Color(0xFF599DBF),
    Color(0xFFC7A65B),
    Color(0xFF83B79D),
    Color(0xFFAD83B4),
    Color(0xFFE99A73)
)

@Composable
private fun ChartView(chart: CardContent.Chart) {
    val tint = LocalAccent.current
    val colors = seriesColors(tint)
    val line = LocalPalette.current.hairline
    val ink = MaterialTheme.colorScheme.onSurfaceVariant
    val description = chart.series.joinToString("；") { s ->
        s.name + "：" + s.points.joinToString { "${it.x} ${it.label ?: it.y.toString()} ${chart.unit.orEmpty()}" }
    }
    val points = chart.series.firstOrNull()?.points.orEmpty()
    if (chart.kind == "donut") {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            val total = points.sumOf { it.y.coerceAtLeast(0.0) }
            Box(
                Modifier
                    .size(116.dp)
                    .semantics { contentDescription = description },
                contentAlignment = Alignment.Center
            ) {
                Canvas(
                    Modifier
                        .fillMaxSize()
                        .padding(9.dp)
                ) {
                    var angle = -90f
                    points.forEachIndexed { i, p ->
                        val sweep = if (total > 0) (p.y.coerceAtLeast(0.0) / total * 360).toFloat() else 0f
                        drawArc(
                            colors[i % colors.size],
                            angle,
                            sweep,
                            false,
                            style = Stroke(16.dp.toPx())
                        )
                        angle += sweep
                    }
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        chart.value ?: CardParser.display(kotlinx.serialization.json.JsonPrimitive(total)).orEmpty(),
                        fontSize = 19.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                    chart.unit?.let { Muted(it) }
                }
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                points.forEachIndexed { i, p ->
                    Text(
                        "● ${p.x}  ${p.label.orEmpty()}  ${if(total>0) (p.y/total*100).roundToInt() else 0}%",
                        fontSize = 12.sp,
                        color = colors[i % colors.size]
                    )
                }
            }
        }
    } else {
        val xs = chart.series.flatMap { it.points.map(Point::x) }.distinct()
        val stacks = xs.map { x ->
            chart.series.sumOf { s -> s.points.find { it.x == x }?.y?.coerceAtLeast(0.0) ?: 0.0 }
        }
        val maximum = max(
            1.0,
            max(
                chart.target?.value ?: 0.0,
                if (chart.kind == "bar") stacks.maxOrNull() ?: 0.0 else chart.series.flatMap { it.points }.maxOfOrNull {
                    it.y
                } ?: 0.0
            )
        ) * 1.12
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(170.dp)
                .semantics { contentDescription = description }
        ) {
            val w = size.width
            val h = size.height - 24.dp.toPx()
            val labelPaint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG)
                .apply {
                    color = ink.toArgb()
                    textSize = 10.sp.toPx()
                    textAlign = android.graphics.Paint.Align.CENTER
                }
            repeat(4) { i ->
                val y = h * i / 3
                drawLine(
                    line,
                    Offset(0f, y),
                    Offset(w, y),
                    1.dp.toPx()
                )
            }
            val target = chart.target
            if (target != null) {
                val y = h * (1 - target.value / maximum).toFloat()
                drawLine(
                    Color(0xFFD88B50),
                    Offset(0f, y),
                    Offset(w, y),
                    1.dp.toPx(),
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 5f))
                )
            }
            val step = w / max(1, xs.size)
            if (chart.kind == "bar")
                xs.forEachIndexed { index, x ->
                    var y = h
                    chart.series.forEachIndexed { i, s ->
                        val p = s.points.find { it.x == x }
                        val height = ((p?.y?.coerceAtLeast(0.0) ?: 0.0) / maximum * h).toFloat()
                        y -= height
                        drawRect(
                            if (chart.series.size == 1 && target != null && (p?.y ?: 0.0) > target.value)
                                Color(0xFFDD9751)
                            else
                                colors[i % colors.size],
                            Offset(index * step + step * .2f, y),
                            Size(step * .6f, height)
                        )
                    }
                    if (chart.series.size == 1) chart.series[0].points.find { it.x == x }
                        ?.label?.let { label ->
                            drawContext.canvas.nativeCanvas.drawText(
                                label,
                                index * step + step * .5f,
                                y - 4.dp.toPx(),
                                labelPaint
                            )
                        }
                }
            else
                chart.series.forEachIndexed { i, s ->
                    val path = Path()
                    s.points.forEachIndexed { j, p ->
                        val x = (xs.indexOf(p.x) + .5f) * step
                        val y = h * (1 - p.y / maximum).toFloat()
                        if (j == 0) path.moveTo(x, y) else path.lineTo(x, y)
                        drawCircle(colors[i % colors.size], 3.dp.toPx(), Offset(x, y))
                    }
                    drawPath(path, colors[i % colors.size], style = Stroke(2.dp.toPx()))
                }
            xs.forEachIndexed { i, x ->
                if (xs.size < 10 || i % 2 == 0) drawContext.canvas.nativeCanvas.drawText(
                    x,
                    i * step + step * .5f,
                    size.height - 3.dp.toPx(),
                    labelPaint
                )
            }
        }
        chart.target?.label?.let { Muted("┄ $it") }
        if (chart.series.size > 1) Text(
            chart.series.mapIndexed { i, s -> "● ${s.name}" }.joinToString("  "),
            fontSize = 11.sp,
            color = ink
        )
        chart.unit?.let { Muted(it) }
    }
    chart.label?.let { Muted(it) }
}

private fun symbolText(s: String) = when {
    s.contains("rain") || s.contains("drizzle") -> "🌧"
    s.contains("bolt") -> "⛈"
    s.contains("snow") -> "❄"
    s.contains("cloud") -> "⛅"
    s.contains("moon") -> "☽"
    s.contains("sun") -> "☀"
    s.contains("wind") -> "≋"
    s.contains("gift") -> "🎁"
    else -> "✦"
}
