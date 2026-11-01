@file:OptIn(
    androidx.compose.material3.ExperimentalMaterial3Api::class,
    androidx.compose.foundation.ExperimentalFoundationApi::class
)

package local.assistant.android.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.*
import androidx.compose.foundation.*
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.*
import androidx.compose.ui.draw.*
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.*
import kotlinx.coroutines.launch
import local.assistant.android.data.*
import local.assistant.core.*
import kotlin.math.*

@Composable
fun BotScreen(
    model: AppModel,
    s: AppState,
    onSettings: () -> Unit,
    onKnowledge: () -> Unit
) {
    var drawerOpen by remember { mutableStateOf(false) }
    var drag by remember { mutableFloatStateOf(0f) }
    val keyboard = LocalSoftwareKeyboardController.current
    val bot = model.selected()
    val p = LocalPalette.current
    BackHandler(drawerOpen) {
        drawerOpen = false
        drag = 0f
    }
    BoxWithConstraints(
        Modifier
            .fillMaxSize()
            .background(p.sidebar)
    ) {
        val width = minOf(maxWidth * .82f, 340.dp)
        val px = with(LocalDensity.current) { width.toPx() }
        val base by animateFloatAsState(if (drawerOpen) px else 0f, tween(300), label = "drawer")
        val shift = (base + drag).coerceIn(0f, px)
        BotSidebar(
            model, s, bot.id,
            onChoose = {
                model.choose(it)
                drawerOpen = false
            },
            onSettings = {
                onSettings()
                drawerOpen = false
            },
            modifier = Modifier.width(width).fillMaxHeight()
                .offset { IntOffset(((shift - px) * .2f).roundToInt(), 0) }
        )
        Column(
            Modifier
                .fillMaxSize()
                .offset { IntOffset(shift.roundToInt(), 0) }
                .shadow(if (shift > 0) 16.dp else 0.dp, RoundedCornerShape(if (shift > 0) 28.dp else 0.dp))
                .clip(RoundedCornerShape(if (shift > 0) 28.dp else 0.dp))
                .background(p.page)
                .pointerInput(drawerOpen, px) {
                    detectHorizontalDragGestures(
                        onDragStart = { drag = 0f },
                        onDragEnd = {
                            drawerOpen = ((if (drawerOpen) px else 0f) + drag) > px / 2
                            drag = 0f
                        },
                        onDragCancel = { drag = 0f }
                    ) { change, amount ->
                        if (drawerOpen || amount > 0 || drag > 0) {
                            change.consume()
                            keyboard?.hide()
                            drag = (drag + amount).coerceIn(if (drawerOpen) -px else 0f, if (drawerOpen) 0f else px)
                        }
                    }
                }
        ) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .statusBarsPadding()
                    .height(52.dp)
                    .padding(horizontal = 16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                TextButton(
                    onClick = {
                        keyboard?.hide()
                        drawerOpen = true
                    }
                ) { Text("☰", fontSize = 23.sp, modifier = Modifier.semantics { contentDescription = "所有 Bot" }) }
                Text(
                    bot.name,
                    Modifier.weight(1f),
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Medium
                )
            }
            if (s.settled) key(bot.id) {
                BotPage(
                    model,
                    s,
                    bot,
                    onKnowledge,
                    Modifier.weight(1f)
                )
            }
            else Box(
                Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                contentAlignment = Alignment.Center
            ) { CircularProgressIndicator() }
        }
        if (shift > 0) Box(
            Modifier
                .fillMaxSize()
                .offset { IntOffset(shift.roundToInt(), 0) }
                .background(Color.Black.copy(alpha = .18f * shift / px))
                .clickable { drawerOpen = false }
        )
    }
}

@Composable
internal fun BotPage(
    model: AppModel,
    s: AppState,
    bot: Bot,
    onKnowledge: () -> Unit,
    modifier: Modifier,
    wideLayout: Boolean = false
) {
    var cardsShown by rememberSaveable { mutableStateOf(true) }
    var draft by rememberSaveable { mutableStateOf("") }
    var days by rememberSaveable { mutableIntStateOf(2) }
    var editing by remember { mutableStateOf<Todo?>(null) }
    var deleting by remember { mutableStateOf<Todo?>(null) }
    var sending by remember { mutableStateOf(false) }
    val scroll = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val messages = model.messages(bot)
    val recent = BotTimeline.recent(messages, days, model.zone)
    val state = model.botState(bot)
    val cards = bot.pluginIds.flatMap { s.cards[it].orEmpty() }
        .filterNot { bot.id == "health" && it.type == "text" }
        .sortedByDescending { it.first }
    val count = when (bot.kind) {
        BotKind.ASSISTANT -> cards.size + 2
        BotKind.KNOWLEDGE -> cards.size + if (bot.joined.isNotEmpty()) 2 else 1
        else -> maxOf(1, cards.size + if (bot.id == "health") 1 else 0)
    }
    LaunchedEffect(bot.id) {
        model.loadBot(bot.id)
        model.device.markSeen(bot.id)
    }
    var previousCount by remember { mutableIntStateOf(s.chats[bot.id]?.size ?: -1) }
    LaunchedEffect(s.chats[bot.id]?.size) {
        val next = s.chats[bot.id]?.size ?: 0
        if (previousCount >= 0 && next > previousCount) scroll.animateScrollToItem(
            (scroll.layoutInfo.totalItemsCount - 1).coerceAtLeast(0)
        )
        previousCount = next
    }
    LaunchedEffect(messages.lastOrNull()?.at) { model.device.markSeen(bot.id) }
    CompositionLocalProvider(LocalAccent provides accent(bot.look.accent)) {
        Column(
            modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
        ) {
            PullToRefreshBox(
                isRefreshing = s.loading,
                onRefresh = { scope.launch { model.refresh() } },
                modifier = Modifier.weight(1f)
            ) {
                LazyColumn(
                    state = scroll,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = if (wideLayout) 24.dp else 16.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    item("hero") {
                        if (wideLayout) TabletHero(model, bot)
                        else Column(
                            Modifier.fillMaxWidth(),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            val tint = LocalAccent.current
                            if (bot.kind == BotKind.SOURCE) BotAvatar(bot, Modifier.size(92.8.dp))
                            else Box(
                                Modifier
                                    .size(116.dp)
                                    .background(
                                        Brush.linearGradient(listOf(tint.copy(alpha = .26f), tint.copy(alpha = .08f))),
                                        CircleShape
                                    )
                                    .border(1.5.dp, tint.copy(alpha = .45f), CircleShape),
                                contentAlignment = Alignment.Center
                            ) {
                                Box(
                                    Modifier
                                        .matchParentSize()
                                        .clip(OpenTopCircle),
                                    contentAlignment = Alignment.Center
                                ) {
                                    Mascot(
                                        bot.look.sticker,
                                        state,
                                        Modifier
                                            .requiredSize(139.2.dp)
                                            .offset(y = 6.96.dp),
                                        messages.lastOrNull { it.author == "bot" }?.let { it.id + it.at.toString() }
                                    )
                                }
                            }
                            Serif(bot.name)
                            Text(
                                bot.tag,
                                Modifier
                                    .background(tint.copy(alpha = .16f), CircleShape)
                                    .padding(horizontal = 10.dp, vertical = 3.dp),
                                fontSize = 13.sp,
                                color = tint
                            )
                            Row(
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text("●", color = stateColor(state), fontSize = 9.sp)
                                Muted(model.statusLine(bot))
                            }
                        }
                    }
                    if (cardsShown) {
                        val plugin = s.plugins.find { it.id == bot.id }
                        if (!bot.builtIn && plugin != null && plugin.state != "enabled") item {
                            CardBox {
                                Text(if (plugin.state == "disabled") "已停用，打开后才会有卡片。" else model.statusLine(bot))
                                if (plugin.state != "unsupported") TextButton(
                                    onClick = {
                                        model.updatePlugin(
                                            plugin.id,
                                            kotlinx.serialization.json.buildJsonObject {
                                                put("enabled", kotlinx.serialization.json.JsonPrimitive(true))
                                            }
                                        )
                                    }
                                ) { Text("启用") }
                            }
                        }
                        else {
                            if (bot.id == "health" && !wideLayout) item { CheckinCard(model, s.online) }
                            if (bot.kind == BotKind.KNOWLEDGE) {
                                item {
                                    CardBox("知识库") {
                                        Muted("把资料放进主题，每晚整理成笔记。每个主题单独设谁能读。")
                                        SettingRow("打开知识库", onClick = onKnowledge)
                                    }
                                }
                                if (bot.joined.isNotEmpty()) item {
                                    CardBox("资料来源") {
                                        s.plugins.filter { it.id in bot.joined }
                                            .forEach { input ->
                                                Row(
                                                    Modifier.fillMaxWidth(),
                                                    verticalAlignment = Alignment.CenterVertically
                                                ) {
                                                    Column(Modifier.weight(1f)) {
                                                        Text(Names.plugin(input.id))
                                                        Muted(
                                                            input.statusLine(
                                                                s.runs,
                                                                s.todos,
                                                                s.clock,
                                                                model.zone
                                                            )
                                                        )
                                                    }
                                                    Switch(
                                                        checked = input.enabled,
                                                        onCheckedChange = {
                                                            model.updatePlugin(
                                                                input.id,
                                                                kotlinx.serialization.json.buildJsonObject {
                                                                    put(
                                                                        "enabled",
                                                                        kotlinx.serialization.json.JsonPrimitive(it)
                                                                    )
                                                                }
                                                            )
                                                        },
                                                        enabled = input.state != "unsupported"
                                                    )
                                                }
                                            }
                                    }
                                }
                            }
                            if (wideLayout) item("card-grid") {
                                val contents = buildList<@Composable () -> Unit> {
                                    if (bot.id == "health") add { CheckinCard(model, s.online) }
                                    cards.forEach { card ->
                                        add {
                                            ServerCard(card, { editing = it }, model::toggle, { deleting = it })
                                        }
                                    }
                                }
                                TabletCardGrid(contents)
                            }
                            else {
                                items(CardPacking.phoneRows(cards.map { it.tile })) { row ->
                                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                        row.forEach { index ->
                                            Box(Modifier.weight(1f)) {
                                                ServerCard(
                                                    cards[index],
                                                    { editing = it },
                                                    model::toggle,
                                                    { deleting = it }
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                            if (bot.kind == BotKind.ASSISTANT) {
                                item {
                                    TodayTodos(
                                        s.today?.todos.orEmpty(),
                                        { editing = it },
                                        model::toggle,
                                        { deleting = it }
                                    )
                                }
                                item {
                                    CardBox("Agent") {
                                        Text(state.headline, fontWeight = FontWeight.Medium)
                                        s.status?.agents?.forEach { (id, a) ->
                                            InfoRow(Names.harness(id), if (a.available) "已找到命令（登录状态未检查）" else "未找到命令")
                                        }
                                        Muted(
                                            RunText.last(
                                                s.runs.firstOrNull() ?: s.status?.lastRun,
                                                s.clock,
                                                model.zone
                                            )
                                        )
                                        TextButton(
                                            onClick = { model.runNow() },
                                            enabled = s.online && s.runs.none {
                                                it.job == "extract_todos" && it.status == "running"
                                            }
                                        ) { Text("立即运行一次") }
                                    }
                                }
                            }
                            if (cards.isEmpty() && bot.kind == BotKind.CHARACTER && bot.id != "health") item {
                                Muted("${bot.name} 现在没有要显示的卡片。")
                            }
                        }
                    }
                    item("handle") {
                        Column(
                            Modifier
                                .fillMaxWidth()
                                .heightIn(min = 44.dp)
                                .clickable { cardsShown = !cardsShown },
                            horizontalAlignment = Alignment.CenterHorizontally
                        ) {
                            Muted("☰")
                            Text(
                                if (cardsShown) "收起卡片" else "展开卡片（$count）",
                                fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                    item("wave") { WaveDivider() }
                    if (recent.second > 0) item {
                        TextButton(onClick = { days += 3 }, modifier = Modifier.fillMaxWidth()) {
                            Muted("更早的消息（还有 ${recent.second} 天）")
                        }
                    }
                    if (messages.isEmpty()) item {
                        Muted(
                            "${bot.name} 还没有说过话。",
                            Modifier
                                .fillMaxWidth()
                                .padding(vertical = 24.dp)
                        )
                    }
                    itemsIndexed(recent.first, key = { _, m -> m.id }) { index, m ->
                        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            if (
                                index == 0 ||
                                    m.at.atZone(model.zone).toLocalDate() !=
                                    recent.first[index - 1].at.atZone(model.zone).toLocalDate()
                            )
                                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                                    Muted(BotTimeline.day(m.at, s.clock, model.zone))
                                }
                            MessageBubble(
                                m,
                                bot,
                                { editing = it },
                                model::toggle,
                                { deleting = it }
                            )
                        }
                    }
                    if (state == MascotState.WORKING) item { Muted("${bot.name} 正在输入 · · ·") }
                    item("bottom") { Spacer(Modifier.height(1.dp)) }
                }
            }
            if (bot.kind == BotKind.SOURCE) Muted("消息来源只负责读消息，不能对话。", Modifier.padding(12.dp))
            else Composer(
                model,
                bot,
                draft,
                { draft = it },
                sending || model.replying(bot)
            ) {
                val typed = draft
                sending = true
                scope.launch {
                    if (model.send(bot, typed)) draft = ""
                    sending = false
                }
            }
        }
    }
    editing?.let { todo ->
        TodoEditor(todo, model.zone, { editing = null }) {
            model.updateTodo(todo, it)
            editing = null
        }
    }
    deleting?.let { todo ->
        AlertDialog(
            onDismissRequest = { deleting = null },
            title = { Text("删除待办？") },
            text = { Text("同时删除对应的提醒事项。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        model.updateTodo(
                            todo,
                            kotlinx.serialization.json.buildJsonObject {
                                put("status", kotlinx.serialization.json.JsonPrimitive("deleted"))
                            }
                        )
                        deleting = null
                    }
                ) { Text("删除") }
            },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("取消") } }
        )
    }
}

@Composable
private fun Composer(
    model: AppModel,
    bot: Bot,
    draft: String,
    onDraft: (String) -> Unit,
    disabled: Boolean,
    onSend: () -> Unit
) {
    val p = LocalPalette.current
    var menu by remember { mutableStateOf(false) }
    var choice by remember { mutableStateOf(model.device.choice) }
    val options = ChatOption.build(model.state.value.status)
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp)
            .shadow(3.dp, RoundedCornerShape(20.dp))
            .clip(RoundedCornerShape(20.dp))
            .background(p.card)
            .border(1.dp, p.hairline, RoundedCornerShape(20.dp))
            .padding(10.dp)
    ) {
        androidx.compose.foundation.text.BasicTextField(
            value = draft,
            onValueChange = onDraft,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 32.dp, max = 140.dp)
                .padding(4.dp),
            textStyle = LocalTextStyle.current.copy(color = MaterialTheme.colorScheme.onSurface, fontSize = 16.sp),
            decorationBox = { field ->
                if (draft.isEmpty()) Muted(if (disabled) "${bot.name} 正在回复…" else "给 ${bot.name} 发消息")
                field()
            }
        )
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box {
                TextButton(
                    onClick = { menu = true },
                    colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurfaceVariant),
                    modifier = Modifier
                        .background(p.bubble, CircleShape)
                        .height(34.dp)
                ) { Text("✦ ${ChatOption.label(choice,options)} ⌄", fontSize = 13.sp) }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    var previous = ""
                    options.forEach { option ->
                        if (option.agent.isNotEmpty() && option.agent != previous) {
                            HorizontalDivider()
                            Muted(option.agent, Modifier.padding(horizontal = 12.dp, vertical = 5.dp))
                            previous = option.agent
                        }
                        DropdownMenuItem(
                            text = { Text((if (choice == option.choice) "✓ " else "") + option.title) },
                            onClick = {
                                choice = option.choice
                                model.device.choice = choice
                                menu = false
                            }
                        )
                    }
                }
            }
            Box(
                Modifier
                    .size(32.dp)
                    .clip(CircleShape)
                    .background(if (draft.isBlank() || disabled) p.trail else p.clay)
                    .clickable(enabled = draft.isNotBlank() && !disabled, onClick = onSend)
                    .semantics { contentDescription = "发送" },
                contentAlignment = Alignment.Center
            ) { Text("↑", color = Color.White, fontWeight = FontWeight.Bold) }
        }
    }
}

@Composable
private fun MessageBubble(
    m: BotMessage,
    bot: Bot,
    onEdit: (Todo) -> Unit,
    onToggle: (Todo) -> Unit,
    onDelete: (Todo) -> Unit
) {
    if (m.author == "app") {
        Muted(m.text)
        return
    }
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (m.author == "me") Arrangement.End else Arrangement.Start,
        verticalAlignment = Alignment.Top
    ) {
        if (m.author == "bot") BotAvatar(
            bot,
            Modifier
                .padding(end = 8.dp)
                .size(28.dp)
        )
        Column(
            Modifier
                .weight(1f, fill = false)
                .then(
                    if (m.author == "me") Modifier
                        .clip(RoundedCornerShape(16.dp))
                        .background(LocalPalette.current.bubble)
                        .padding(12.dp) else Modifier
                ),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            m.title?.let { Serif(it, 16) }
            MarkdownText(m.text)
            m.todos.forEach { todo ->
                TodoRow(
                    todo,
                    { onEdit(todo) },
                    { onToggle(todo) },
                    { onDelete(todo) }
                )
            }
        }
    }
}

@Composable
private fun WaveDivider() {
    val line = LocalPalette.current.trail
    val tint = LocalAccent.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Canvas(
            Modifier
                .weight(1f)
                .height(14.dp)
        ) {
            val path = Path()
            val wave = 30.dp.toPx()
            for (i in 0.. (size.width / 2).toInt().coerceAtLeast(1)) {
                val x = i * 2f
                val y = size.height / 2 + sin(x / wave * 2 * PI).toFloat() * 3.dp.toPx() * min(1f, x / (wave * 1.5f))
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            drawPath(path, line, style = Stroke(1.4.dp.toPx(), cap = StrokeCap.Round))
        }
        Canvas(
            Modifier
                .size(14.dp)
                .rotate(-8f)
                .offset(y = (-2).dp)
        ) {
            val plane = Path()
                .apply {
                    moveTo(0f, size.height * .38f)
                    lineTo(size.width, 0f)
                    lineTo(size.width * .64f, size.height)
                    lineTo(size.width * .43f, size.height * .62f)
                    lineTo(size.width * .8f, size.height * .2f)
                    lineTo(size.width * .31f, size.height * .5f)
                    close()
                }
            drawPath(plane, tint)
        }
    }
}

internal fun stateColor(state: MascotState) = when (state) {
    MascotState.OFFLINE -> Color.Gray
    MascotState.ERROR -> Color(0xFFD4685A)
    MascotState.WORKING -> Color(0xFFDD9A55)
    else -> Color(0xFF7CA58A)
}

private object OpenTopCircle : Shape {
    override fun createOutline(
        size: androidx.compose.ui.geometry.Size,
        layoutDirection: LayoutDirection,
        density: Density
    ): Outline {
        val circle = Path().apply { addOval(androidx.compose.ui.geometry.Rect(Offset.Zero, size)) }
        val top = Path().apply {
            addRect(
                androidx.compose.ui.geometry.Rect(
                    -size.width / 2,
                    -size.height,
                    size.width * 1.5f,
                    size.height * .5f
                )
            )
        }
        return Outline.Generic(Path.combine(PathOperation.Union, circle, top))
    }
}
