@file:OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class, androidx.compose.ui.ExperimentalComposeUiApi::class)

package local.assistant.desktop.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.drawscope.inset
import androidx.compose.ui.input.pointer.PointerEventType
import androidx.compose.ui.input.pointer.onPointerEvent
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import local.assistant.core.*
import local.assistant.desktop.AppStore
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import kotlin.math.abs
import kotlin.math.max

@Composable
fun CardBox(
    title: String? = null,
    modifier: Modifier = Modifier,
    index: Int = 0,
    symbol: String? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val palette = LocalPalette.current
    val style = LocalCardStyle.current
    val tint = LocalBotTint.current
    val reduced = LocalReducedMotion.current
    var shown by remember { mutableStateOf(reduced) }
    var hovering by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        if (!reduced) delay(index.coerceAtMost(8) * 50L)
        shown = true
    }
    val visibility by animateFloatAsState(if (shown) 1f else 0f, tween(if (reduced) 0 else 300))
    val lift by animateFloatAsState(if (hovering && !reduced) 1f else 0f, tween(150))
    Column(
        modifier.fillMaxWidth().graphicsLayer {
            alpha = visibility
            translationY = (1 - visibility) * 10 - lift * 2
            scaleX = 1 + lift * 0.006f
            scaleY = scaleX
        }.onPointerEvent(PointerEventType.Enter) { hovering = true }
            .onPointerEvent(PointerEventType.Exit) { hovering = false }
            .clip(RoundedCornerShape(12.dp))
            .background(if (style == "soft") androidx.compose.ui.graphics.lerp(palette.card, tint, 0.08f) else palette.card)
            .border(1.dp, if (style == "soft") tint.copy(alpha = 0.22f) else palette.hairline, RoundedCornerShape(12.dp))
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        if (style == "accent") Box(Modifier.fillMaxWidth().height(3.dp).background(tint))
        if (title != null) Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Label(
                (if (style == "accent") "▤  " else "") + title,
                Modifier.weight(1f),
                size = 15.sp,
                serif = true,
                weight = FontWeight.SemiBold,
                color = if (style == "soft") tint else palette.ink,
            )
            if (symbol != null) CardIllustration(symbol)
        }
        content()
    }
}

@Composable
fun SettingRow(title: String, detail: String? = null, action: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = action).padding(vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Label(title)
            if (!detail.isNullOrEmpty()) Muted(detail)
        }
        Muted("›")
    }
}

@Composable
fun ToggleRow(title: String, value: Boolean, enabled: Boolean = true, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Label(title, Modifier.weight(1f))
        Switch(value, change, enabled = enabled)
    }
}

@Composable
fun InfoRow(title: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Muted(title, Modifier.width(70.dp))
        Label(value, Modifier.weight(1f), size = 12.sp)
    }
}

@Composable
fun Tooltip(text: String, content: @Composable () -> Unit) {
    TooltipArea(tooltip = {
        Surface(elevation = 4.dp, shape = RoundedCornerShape(6.dp)) {
            Label(text.ifEmpty { "没有原文片段" }, Modifier.widthIn(max = 420.dp).padding(10.dp), size = 12.sp)
        }
    }) { content() }
}

@Composable
fun MarkdownText(text: String) {
    SelectionContainer {
        Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
            var fenced = false
            Markdown.lines(text).forEach { line ->
                when {
                    line.text.trimStart().startsWith("```") -> fenced = !fenced
                    fenced -> Label(line.text, Modifier.fillMaxWidth().background(LocalPalette.current.bubble).padding(4.dp), size = 12.sp)
                    line.heading -> Serif(line.text, 17)
                    line.text.startsWith("- ") -> Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        Label("•", color = LocalBotTint.current)
                        InlineText(line.text.removePrefix("- "))
                    }
                    else -> InlineText(line.text)
                }
            }
        }
    }
}

@Composable
fun TodoList(todos: List<Todo>, store: AppStore, compact: Boolean = false) {
    var editing by remember { mutableStateOf<Todo?>(null) }
    var deleting by remember { mutableStateOf<Todo?>(null) }
    val scope = rememberCoroutineScope()
    if (todos.isEmpty()) Muted("今天没有要做的事。")
    todos.forEachIndexed { index, todo ->
        var saving by remember(todo.id) { mutableStateOf(false) }
        Row(
            Modifier.fillMaxWidth().padding(vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(7.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Checkbox(
                checked = todo.status == "completed",
                onCheckedChange = {
                    saving = true
                    scope.launch {
                        store.toggle(todo)
                        saving = false
                    }
                },
                enabled = !saving && store.state.value.online,
                modifier = Modifier.size(28.dp),
            )
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    todo.title,
                    color = if (todo.status == "completed") LocalPalette.current.muted else LocalPalette.current.ink,
                    textDecoration = if (todo.status == "completed") TextDecoration.LineThrough else null,
                    modifier = Modifier.clickable(enabled = !compact) { editing = todo },
                )
                BoxWithConstraints {
                    @Composable
                    fun due() {
                        Label(
                            DueText.describe(todo, store.state.value.clock, store.zone),
                            color = if (todo.status == "pending" && DueText.overdue(todo, store.state.value.clock, store.zone)) MaterialTheme.colors.error else LocalPalette.current.muted,
                            size = 12.sp,
                        )
                    }
                    if (!compact && maxWidth >= 550.dp) {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            due()
                            Tooltip(todo.sourceExcerpt) { Muted(todo.sourceText()) }
                            Muted(todo.syncText())
                        }
                    } else {
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            due()
                            if (!compact) {
                                Tooltip(todo.sourceExcerpt) { Muted(todo.sourceText()) }
                                Muted(todo.syncText())
                            }
                        }
                    }
                }
                if (!compact) {
                    if (todo.notes.isNotBlank()) Muted(todo.notes)
                }
            }
            if (!compact) {
                TextButton(onClick = { editing = todo }, contentPadding = PaddingValues(4.dp), modifier = Modifier.width(42.dp)) { Muted("编辑") }
                TextButton(onClick = { deleting = todo }, contentPadding = PaddingValues(4.dp), modifier = Modifier.width(42.dp)) { Muted("删除") }
            }
        }
        if (index < todos.lastIndex) Divider(color = LocalPalette.current.hairline)
    }
    editing?.let { todo -> TodoEditor(todo, store) { editing = null } }
    deleting?.let { todo ->
        var busy by remember { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { if (!busy) deleting = null },
            title = { Serif("删除这条待办？", 20) },
            text = { Label("提醒事项里对应的条目也会一起删除。") },
            confirmButton = {
                TextButton(onClick = {
                    busy = true
                    scope.launch {
                        if (store.updateTodo(todo, buildJsonObject { put("status", "deleted") })) deleting = null
                        busy = false
                    }
                }, enabled = !busy) { Label("删除", color = MaterialTheme.colors.error) }
            },
            dismissButton = { TextButton(onClick = { deleting = null }, enabled = !busy) { Label("取消") } },
        )
    }
}

@Composable
private fun TodoEditor(todo: Todo, store: AppStore, close: () -> Unit) {
    val initial = remember(todo.id) { TodoDraft.from(todo, zone = store.zone) }
    var title by remember { mutableStateOf(initial.title) }
    var notes by remember { mutableStateOf(initial.notes) }
    var hasDue by remember { mutableStateOf(initial.hasDue) }
    var hasTime by remember { mutableStateOf(initial.hasTime) }
    var date by remember { mutableStateOf(initial.due.toLocalDate().toString()) }
    var time by remember { mutableStateOf(initial.due.toLocalTime().withSecond(0).withNano(0).toString()) }
    var saving by remember { mutableStateOf(false) }
    val parsed = runCatching {
        LocalDateTime.of(LocalDate.parse(date), if (hasTime) LocalTime.parse(time) else LocalTime.MIDNIGHT)
    }.getOrNull()
    val valid = title.isNotBlank() && (!hasDue || parsed != null)
    val scope = rememberCoroutineScope()
    AlertDialog(
        onDismissRequest = { if (!saving) close() },
        modifier = Modifier.width(540.dp),
        title = { Serif("编辑待办", 22) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(title, { title = it }, label = { Label("标题") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(notes, { notes = it }, label = { Label("备注") }, modifier = Modifier.fillMaxWidth())
                ToggleRow("有截止时间", hasDue) { hasDue = it }
                if (hasDue) {
                    OutlinedTextField(date, { date = it }, label = { Label("日期（yyyy-MM-dd）") }, singleLine = true)
                    ToggleRow("具体时间", hasTime) { hasTime = it }
                    if (hasTime) OutlinedTextField(time, { time = it }, label = { Label("时间（HH:mm）") }, singleLine = true)
                    if (parsed == null) Label("日期或时间格式不对", color = MaterialTheme.colors.error)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                saving = true
                scope.launch {
                    val draft = TodoDraft(title, notes, hasDue, hasTime, parsed ?: initial.due)
                    if (store.updateTodo(todo, draft.patch(todo, store.zone))) close()
                    saving = false
                }
            }, enabled = valid && !saving) { Label(if (saving) "保存中…" else "保存") }
        },
        dismissButton = { TextButton(onClick = close, enabled = !saving) { Label("取消") } },
    )
}

@Composable
fun ServerCard(card: Card, store: AppStore, tint: Color, modifier: Modifier = Modifier, index: Int = 0) {
    CompositionLocalProvider(LocalBotTint provides tint) {
        CardBox(card.title, modifier, index, card.symbol) {
            when (val content = card.content) {
                is CardContent.Items -> {
                    content.items.forEachIndexed { position, item ->
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
                            if (card.listStyle == "timeline") Label(if (item.state == "done") "✓" else "●", color = tint)
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Label(item.title, weight = if (item.state == "now") FontWeight.SemiBold else FontWeight.Normal)
                                item.subtitle?.let { Muted(it) }
                                item.detail?.let { Muted(it) }
                                item.progress?.let { progress ->
                                    val progressColor = when (progress.tone) {
                                        "warn" -> Color(0xFFFF9500)
                                        "alert" -> Color(0xFFFF3B30)
                                        else -> tint
                                    }
                                    if (card.listStyle == "rings") {
                                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                            Canvas(Modifier.size(32.dp)) {
                                                drawArc(progressColor.copy(alpha = 0.12f), -90f, 360f, false, style = Stroke(4.dp.toPx()))
                                                drawArc(progressColor, -90f, progress.fraction.toFloat() * 360, false, style = Stroke(4.dp.toPx()))
                                            }
                                            Muted("${progress.value} / ${progress.max}")
                                        }
                                    } else {
                                        LinearProgressIndicator(progress.fraction.toFloat(), Modifier.fillMaxWidth().height(4.dp), color = progressColor)
                                    }
                                }
                            }
                        }
                        if (position < content.items.lastIndex) Divider(color = LocalPalette.current.hairline)
                    }
                }
                is CardContent.Stat -> {
                    Label(content.value + content.unit?.let { " $it" }.orEmpty(), color = tint, size = 32.sp, serif = true)
                    content.label?.let { Muted(it) }
                    content.delta?.let { Muted((if (content.trend == "up") "↑ " else if (content.trend == "down") "↓ " else "") + it) }
                    content.bar?.let { bar ->
                        Canvas(Modifier.fillMaxWidth().height(8.dp)) {
                            drawRoundRect(tint.copy(alpha = 0.1f))
                            var x = 0f
                            bar.segments.forEachIndexed { segment, point ->
                                val width = if (bar.max > 0) (point.y / bar.max).coerceIn(0.0, 1.0).toFloat() * size.width else 0f
                                val visible = width.coerceAtMost((size.width - x).coerceAtLeast(0f))
                                drawRect(tint.copy(alpha = (1f - segment * 0.15f).coerceAtLeast(0.25f)), Offset(x, 0f), Size(visible, size.height))
                                x += visible
                            }
                        }
                        bar.segments.forEach { Muted("${it.x} ${it.y}") }
                    }
                }
                is CardContent.Chart -> ChartView(content, tint)
                is CardContent.Photos -> BoxWithConstraints {
                    val columns = ((maxWidth.value + 8) / 80).toInt().coerceAtLeast(1)
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        content.photos.chunked(columns).forEach { row ->
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                row.forEach { photo ->
                                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                                        Box(Modifier.fillMaxWidth().aspectRatio(1f).background(LocalPalette.current.bubble, RoundedCornerShape(8.dp)), contentAlignment = Alignment.Center) {
                                            Muted("▧")
                                        }
                                        photo.caption?.let { Label(it, color = LocalPalette.current.muted, size = 10.sp, maxLines = 1) }
                                    }
                                }
                                repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
                            }
                        }
                    }
                }
                is CardContent.Text -> {
                    content.headline?.let { Serif(it, 18) }
                    val table = content.table
                    if (table == null) MarkdownText(content.text) else CardTableView(table, tint)
                    content.footnote?.let { Muted(it) }
                }
                is CardContent.Todos -> TodoList(content.todos, store)
                is CardContent.Unsupported -> Muted("这张卡片（${content.type}）需要更新 App 才能显示")
            }
        }
    }
}

@Composable
private fun CardTableView(table: CardTable, tint: Color) {
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
            table.columns.forEachIndexed { index, value ->
                val modifier = if (index == 0) Modifier.width(36.dp) else Modifier.weight(1f)
                Label(value, modifier.padding(2.dp), color = if (index == table.highlight) tint else LocalPalette.current.muted, size = 10.sp, weight = FontWeight.SemiBold)
            }
        }
        table.rows.forEach { cells ->
            Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                cells.forEachIndexed { index, value ->
                    if (index == 0) {
                        Label(value, Modifier.width(36.dp).padding(vertical = 5.dp), color = LocalPalette.current.muted, size = 10.sp)
                    } else {
                        val today = index == table.highlight
                        val background = if (value.isBlank()) LocalPalette.current.muted.copy(alpha = if (today) 0.1f else 0.05f) else tint.copy(alpha = if (today) 0.24f else 0.12f)
                        Label(value, Modifier.weight(1f).heightIn(min = 40.dp).background(background, RoundedCornerShape(6.dp)).padding(4.dp), size = 10.sp, maxLines = 4)
                    }
                }
            }
        }
    }
}

@Composable
fun DataTable(columns: List<String>, rows: List<List<String>>) {
    Column(Modifier.horizontalScroll(rememberScrollState())) {
        Row {
            columns.forEach { Label(it, Modifier.width(140.dp).padding(6.dp), weight = FontWeight.SemiBold, size = 12.sp) }
        }
        rows.forEach { cells ->
            Divider(color = LocalPalette.current.hairline)
            Row {
                cells.forEach { Label(it, Modifier.width(140.dp).padding(6.dp), size = 12.sp) }
            }
        }
    }
}

@Composable
private fun ChartView(chart: CardContent.Chart, tint: Color) {
    if (chart.kind == "donut") {
        DonutChart(chart)
        return
    }
    chart.value?.let { Serif(it + chart.unit?.let { unit -> " $unit" }.orEmpty(), 26) }
    chart.label?.let { Muted(it) }
    val target = chart.target
    val all = chart.series.flatMap { it.points }
    val low = minOf(0.0, all.minOfOrNull { it.y } ?: 0.0)
    val categories = all.map { it.x }.distinct()
    val stacked = chart.kind == "bar" && chart.series.size > 1
    val highest = if (stacked) all.groupBy { it.x }.values.maxOfOrNull { points -> points.sumOf { it.y.coerceAtLeast(0.0) } } else all.maxOfOrNull { it.y }
    val high = max(1.0, max(highest ?: 1.0, target?.value ?: 0.0))
    val range = (high - low).coerceAtLeast(1e-6)
    val muted = LocalPalette.current.muted
    val colors = chartColors
    chart.unit?.let { Muted(it) }
    Tooltip(all.joinToString("\n") { "${it.label ?: it.x}：${it.y}${chart.unit.orEmpty()}" }) {
        Canvas(Modifier.fillMaxWidth().height(150.dp)) {
            inset(left = 32.dp.toPx(), top = 14.dp.toPx(), right = 8.dp.toPx(), bottom = 4.dp.toPx()) {
                fun y(value: Double): Float = ((high - value) / range * size.height).toFloat()
                val painter = PropCanvas(this)
                val textPaint = PropPaint().apply {
                    color = muted.toArgb()
                    textSize = 9.sp.toPx()
                }
                listOf(low, (low + high) / 2, high).forEach { value ->
                    drawLine(muted.copy(alpha = 0.12f), Offset(0f, y(value)), Offset(size.width, y(value)))
                    painter.drawText("%.1f".format(value), -18.dp.toPx(), y(value) + 3.dp.toPx(), textPaint)
                }
                target?.let { target ->
                    drawLine(muted.copy(alpha = 0.5f), Offset(0f, y(target.value)), Offset(size.width, y(target.value)), pathEffect = androidx.compose.ui.graphics.PathEffect.dashPathEffect(floatArrayOf(6f, 6f)))
                }
                val accumulated = mutableMapOf<String, Double>()
                drawLine(tint.copy(alpha = 0.15f), Offset(0f, y(0.0)), Offset(size.width, y(0.0)))
                chart.series.forEachIndexed { seriesIndex, series ->
                    val color = if (chart.series.size == 1) tint else colors[seriesIndex % colors.size]
                    val path = Path()
                    series.points.forEachIndexed { index, point ->
                        val step = size.width / max(1, categories.size)
                        val category = categories.indexOf(point.x)
                        val x = if (chart.kind == "bar") step * (category + 0.5f) else size.width * category / max(1, categories.lastIndex)
                        if (chart.kind == "bar") {
                            val base = if (stacked) accumulated[point.x] ?: 0.0 else 0.0
                            val end = base + point.y
                            val barWidth = (step * 0.7f).coerceAtLeast(1f)
                            drawRect(color, Offset(x - barWidth / 2, minOf(y(end), y(base))), Size(barWidth, abs(y(end) - y(base))))
                            if (!stacked && target != null && point.y > target.value) {
                                drawRect(Color(0xFFFF9500), Offset(x - barWidth / 2, y(point.y)), Size(barWidth, (y(target.value) - y(point.y)).coerceAtLeast(0f)))
                            }
                            if (!stacked) point.label?.let { painter.drawText(it, x, y(point.y) - 4.dp.toPx(), textPaint) }
                            accumulated[point.x] = end
                        } else {
                            if (index == 0) path.moveTo(x, y(point.y)) else path.lineTo(x, y(point.y))
                            drawCircle(color, 3f, Offset(x, y(point.y)))
                        }
                    }
                    if (chart.kind != "bar") drawPath(path, color, style = Stroke(2.dp.toPx()))
                }
            }
        }
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Muted(all.firstOrNull()?.x.orEmpty())
        Muted(all.lastOrNull()?.x.orEmpty())
    }
    if (chart.series.size > 1) chart.series.forEachIndexed { index, series ->
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            StatusDot(colors[index % colors.size])
            Muted(series.name)
        }
    }
    target?.label?.let { Muted(it) }
    if (chart.kind == "bar" && chart.series.size == 1 && target != null && all.any { it.y > target.value }) {
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            StatusDot(Color(0xFFFF9500))
            Muted("超出的部分")
        }
    }
}

private val chartColors = listOf(Color(0xFF007AFF), Color(0xFF34C759), Color(0xFFFF9500), Color(0xFFAF52DE), Color(0xFF30B0C7), Color(0xFFFF2D55), Color(0xFFFFCC00), Color(0xFF5856D6))

@Composable
private fun DonutChart(chart: CardContent.Chart) {
    val slices = chart.series.firstOrNull()?.points.orEmpty()
    val total = slices.sumOf { it.y.coerceAtLeast(0.0) }
    val colors = listOf(chartColors[2], chartColors[1], chartColors[0]) + chartColors.drop(3)
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(112.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.fillMaxSize().padding(10.dp)) {
                var start = -90f
                slices.forEachIndexed { index, point ->
                    val sweep = if (total > 0) (point.y.coerceAtLeast(0.0) / total * 360).toFloat() else 0f
                    drawArc(colors[index % colors.size], start, sweep, false, style = Stroke(16.dp.toPx()))
                    start += sweep
                }
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Serif(chart.value ?: "%.0f".format(total), 18)
                chart.unit?.let { Muted(it) }
            }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            slices.forEachIndexed { index, point ->
                val percent = if (total > 0) point.y / total * 100 else 0.0
                val share = if (percent > 0 && percent < 1) "<1%" else "${percent.toInt()}%"
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    StatusDot(colors[index % colors.size])
                    Column {
                        Label(point.x, size = 12.sp)
                        Muted("${point.label ?: point.y.toString()} · $share")
                    }
                }
            }
        }
    }
    chart.label?.let { Muted(it) }
}
