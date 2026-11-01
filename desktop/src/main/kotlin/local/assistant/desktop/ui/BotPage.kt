package local.assistant.desktop.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.key.*
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import local.assistant.core.*
import local.assistant.desktop.*
import java.time.LocalDate
import kotlin.math.sin

@Composable
fun BotPage(bot: Bot, store: AppStore, state: DesktopState, navigate: (String, String) -> Unit) {
    var cardsShown by remember { mutableStateOf(true) }
    var days by remember { mutableIntStateOf(2) }
    val scroll = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val allMessages = store.messages(bot)
    val (messages, hidden) = BotTimeline.recent(allMessages, days, store.zone)
    val status = store.botState(bot)
    val reduced = LocalReducedMotion.current
    val count = state.chats[bot.id]?.size
    var previousCount by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(count) {
        val before = previousCount
        previousCount = count
        if (before != null && count != null && count > before) {
            val last = scroll.layoutInfo.totalItemsCount - 1
            if (last >= 0) {
                if (reduced) scroll.scrollToItem(last) else scroll.animateScrollToItem(last)
            }
        }
    }
    LaunchedEffect(scroll.firstVisibleItemIndex) {
        // Stable LazyColumn keys keep the visible conversation anchored when the offscreen cards disappear.
        if (cardsShown && scroll.firstVisibleItemIndex >= 3) cardsShown = false
    }
    LaunchedEffect(bot.id) { store.loadBot(bot.id) }
    LaunchedEffect(allMessages.lastOrNull()?.id) { store.markSeen(bot.id) }
    Column(Modifier.fillMaxSize()) {
        Box(Modifier.weight(1f)) {
            LazyColumn(
                state = scroll,
                contentPadding = PaddingValues(24.dp, 16.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                item("hero") { BotHero(bot, store, allMessages.lastOrNull()?.id) }
                if (cardsShown) item("cards") { BotCards(bot, store, state, navigate) }
                item("handle") {
                    TextButton(onClick = { cardsShown = !cardsShown }, modifier = Modifier.fillMaxWidth()) {
                        Muted(if (cardsShown) "≡  收起卡片" else "≡  展开卡片")
                    }
                }
                item("divider") { WavyDivider(accent(bot.look.accent)) }
                if (hidden > 0 || state.chats[bot.id].orEmpty().size >= 200) item("earlier") {
                    TextButton(onClick = {
                        days += 3
                        scope.launch { store.loadChat(bot.id, earlier = true) }
                    }, modifier = Modifier.fillMaxWidth()) { Muted(if (hidden > 0) "更早的消息（还有 $hidden 天）" else "读取更早的消息") }
                }
                if (messages.isEmpty()) item("empty") {
                    Muted("${bot.name} 还没有说过话。", Modifier.padding(vertical = 24.dp))
                }
                var previousDay: LocalDate? = null
                messages.forEach { message ->
                    val day = message.at.atZone(store.zone).toLocalDate()
                    if (day != previousDay) {
                        item("day-$day") {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                                Muted(BotTimeline.day(message.at, state.clock, store.zone))
                            }
                        }
                    }
                    previousDay = day
                    item("message-${message.id}") { MessageBubble(message, bot, store) }
                }
                if (status == MascotState.WORKING) item("busy") {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Avatar(bot, Modifier.size(30.dp))
                        Label("● ● ●   正在忙", Modifier.background(LocalPalette.current.bubble, RoundedCornerShape(12.dp)).padding(12.dp), color = LocalPalette.current.muted)
                    }
                }
                item("bottom") { Spacer(Modifier.height(1.dp)) }
            }
            VerticalScrollbar(rememberScrollbarAdapter(scroll), Modifier.align(Alignment.CenterEnd).fillMaxHeight())
        }
        if (bot.kind == BotKind.SOURCE) {
            Box(Modifier.fillMaxWidth().padding(14.dp), contentAlignment = Alignment.Center) {
                Muted("消息来源只负责读消息，不能对话。")
            }
        } else {
            ChatInput(bot, store, state)
        }
    }
}

@Composable
private fun BotHero(bot: Bot, store: AppStore, talkKey: String?) {
    val tint = accent(bot.look.accent)
    Row(
        Modifier.fillMaxWidth().height(150.dp).clip(RoundedCornerShape(16.dp)).background(
            Brush.linearGradient(listOf(tint.copy(alpha = 0.22f), tint.copy(alpha = 0.05f))),
            RoundedCornerShape(16.dp),
        ).padding(horizontal = 20.dp),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        if (bot.kind == BotKind.SOURCE) Avatar(bot, Modifier.padding(bottom = 24.dp).size(84.dp)) else {
            Mascot(bot.look.sticker, store.botState(bot), Modifier.size(150.dp).offset(y = 10.dp), talkKey)
        }
        Column(Modifier.weight(1f).padding(bottom = 20.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Serif(bot.name, 28)
                Label(bot.tag, Modifier.background(tint.copy(alpha = 0.16f), RoundedCornerShape(20.dp)).padding(horizontal = 8.dp, vertical = 3.dp), color = tint)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                StatusDot(stateColor(store.botState(bot)))
                Muted(store.statusLine(bot))
            }
        }
    }
}

@Composable
private fun BotCards(bot: Bot, store: AppStore, state: DesktopState, navigate: (String, String) -> Unit) {
    val scope = rememberCoroutineScope()
    val plugin = state.plugins.find { it.id == bot.id }
    val allCards = bot.pluginIds.flatMap { state.cards[it].orEmpty() }
        .filterNot { bot.id == "health" && it.type == "text" }
        .sortedBy { !it.first }
    val tint = accent(bot.look.accent)
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (bot.kind == BotKind.KNOWLEDGE) {
            CardBox("知识库") { SettingRow("打开知识库", "资料、整理笔记和谁能读") { navigate("knowledge", "") } }
            if (bot.joined.isNotEmpty()) CardBox("资料来源", index = 1) {
                bot.joined.forEach { id ->
                    state.plugins.find { it.id == id }?.let { input ->
                        SettingRow(input.name, input.statusLine(state.runs, state.todos, state.clock, store.zone)) { navigate("plugin", input.id) }
                    }
                }
            }
        }
        if (!bot.builtIn && plugin != null && plugin.state != "enabled") {
            CardBox(plugin.name) {
                Muted(plugin.statusLine(state.runs, state.todos, state.clock, store.zone))
                ToggleRow("启用", plugin.enabled, plugin.state != "unsupported") { next ->
                    scope.launch { store.updatePlugin(plugin, kotlinx.serialization.json.buildJsonObject { put("enabled", kotlinx.serialization.json.JsonPrimitive(next)) }) }
                }
            }
        } else {
            if (bot.id == "health") ReadinessCard(store)
            CardGrid(allCards, store, tint)
            if (allCards.isEmpty() && !bot.builtIn && bot.id != "health") Muted("${bot.name} 现在没有要显示的卡片。")
        }
        if (bot.kind == BotKind.ASSISTANT) {
            val remaining = state.today?.todos.orEmpty().count { it.status == "pending" }
            CardBox("今日待办（还有 $remaining 件）", index = allCards.size) {
                TodoList(state.today?.todos.orEmpty(), store)
            }
            CardBox("Agent", index = allCards.size + 1) {
                Muted(RunText.last(state.runs.firstOrNull { it.job == "extract_todos" } ?: state.status?.lastRun, state.clock, store.zone))
                state.status?.agents.orEmpty().toSortedMap().forEach { (id, agent) ->
                    Row(horizontalArrangement = Arrangement.spacedBy(7.dp), verticalAlignment = Alignment.CenterVertically) {
                        StatusDot(if (agent.available && state.online) Color(0xFF72967A) else LocalPalette.current.muted)
                        Label(Names.harness(id))
                        Muted(if (agent.available && state.online) "可用" else "不可用")
                    }
                }
                RunNowButton(store, state)
            }
        }
    }
}

@Composable
fun RunNowButton(store: AppStore, state: DesktopState, title: String = "立即运行一次") {
    var starting by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val running = state.runs.any { it.job == "extract_todos" && it.status == "running" }
    Button(onClick = {
        starting = true
        scope.launch {
            store.runNow()
            starting = false
        }
    }, enabled = state.online && !running && !starting) { Text(if (starting || running) "运行中…" else title) }
}

@Composable
private fun MessageBubble(message: BotMessage, bot: Bot, store: AppStore) {
    val reduced = LocalReducedMotion.current
    var shown by remember { mutableStateOf(reduced) }
    LaunchedEffect(Unit) { shown = true }
    val progress by animateFloatAsState(if (shown) 1f else 0f, tween(if (reduced) 0 else 230))
    Row(
        Modifier.fillMaxWidth().graphicsLayer {
            alpha = progress
            translationY = (1 - progress) * 12
        },
        horizontalArrangement = if (message.author == "me") Arrangement.End else Arrangement.Start,
    ) {
        if (message.author == "bot") Avatar(bot, Modifier.padding(end = 8.dp).size(30.dp))
        Column(
            Modifier.widthIn(max = 760.dp).weight(1f, fill = false)
                .background(if (message.author == "me") LocalPalette.current.bubble else Color.Transparent, RoundedCornerShape(14.dp))
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            message.title?.let { Serif(it, 17) }
            if (message.failed) Label(message.text, color = MaterialTheme.colors.error) else MarkdownText(message.text)
            if (message.todos.isNotEmpty()) TodoList(message.todos, store)
            Muted(format(message.at, "HH:mm", store.zone))
        }
    }
}

@Composable
private fun ChatInput(bot: Bot, store: AppStore, state: DesktopState) {
    var text by remember(bot.id) { mutableStateOf("") }
    var choosing by remember { mutableStateOf(false) }
    var choice by remember { mutableStateOf(store.device.choice) }
    val replying = store.replying(bot)
    val options = ChatOption.build(state.status)
    val scope = rememberCoroutineScope()
    val canSend = !replying && state.online && text.isNotBlank() && text.trim().length <= 2_000 &&
        (bot.builtIn || state.plugins.any { it.id == bot.id && it.enabled })
    fun send() {
        if (!canSend) return
        val sending = text
        text = ""
        scope.launch { if (!store.send(bot, sending)) text = sending }
    }
    Column(
        Modifier.padding(horizontal = 24.dp, vertical = 12.dp).fillMaxWidth()
            .background(LocalPalette.current.card, RoundedCornerShape(20.dp))
            .border(1.dp, LocalPalette.current.hairline, RoundedCornerShape(20.dp)).padding(12.dp),
    ) {
        OutlinedTextField(
            text, { if (it.length <= 2_000) text = it },
            placeholder = { Muted(if (replying) "${bot.name} 正在回复…" else "给 ${bot.name} 发消息") },
            modifier = Modifier.fillMaxWidth().onPreviewKeyEvent { event ->
                if (event.key == Key.Enter && event.type == KeyEventType.KeyDown && !event.isShiftPressed) {
                    send()
                    true
                } else false
            },
            maxLines = 6,
            colors = TextFieldDefaults.outlinedTextFieldColors(focusedBorderColor = Color.Transparent, unfocusedBorderColor = Color.Transparent),
        )
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box {
                TextButton(onClick = { choosing = true }) { Muted("✦ ${ChatOption.label(choice, options)} ⌄") }
                DropdownMenu(choosing, { choosing = false }) {
                    var previousAgent = ""
                    options.forEach { option ->
                        if (option.agent.isNotEmpty() && option.agent != previousAgent) {
                            Divider()
                            Muted(option.agent, Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
                        }
                        previousAgent = option.agent
                        DropdownMenuItem(onClick = {
                            choice = option.choice
                            store.device.choice = choice
                            choosing = false
                        }) { Label((if (choice == option.choice) "✓ " else "") + option.title) }
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            Button(onClick = ::send, enabled = canSend, shape = androidx.compose.foundation.shape.CircleShape, contentPadding = PaddingValues(0.dp), modifier = Modifier.size(34.dp)) {
                Text("↑", fontSize = 20.sp)
            }
        }
    }
}

@Composable
private fun WavyDivider(tint: Color) {
    val trail = LocalPalette.current.trail
    Canvas(Modifier.fillMaxWidth().height(16.dp)) {
        val path = Path()
        for (step in 0..size.width.toInt() step 2) {
            val x = step.toFloat()
            val y = size.height / 2 + sin(x / 30f * Math.PI * 2).toFloat() * 3
            if (step == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        drawPath(path, trail, style = Stroke(1.4f, pathEffect = PathEffect.dashPathEffect(floatArrayOf(5f, 3f))))
        drawLine(tint, Offset(size.width - 12, 3f), Offset(size.width, size.height / 2), 2f)
    }
}

@Composable
private fun ReadinessCard(store: AppStore) {
    var apple by remember { mutableStateOf("") }
    var perceived by remember { mutableIntStateOf(0) }
    var day by remember { mutableStateOf(LocalDate.now(store.zone).toString()) }
    var saving by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val checkin = ReadinessCheckin(ReadinessCheckin.parseApple(apple), perceived.takeIf { it > 0 })
    val date = runCatching { LocalDate.parse(day) }.getOrNull()
    val appleInvalid = apple.isNotBlank() && checkin.appleReadiness == null
    CardBox("今天的记录") {
        Muted("用来校准综合准备度：记满 3 周后，在 Mac 上运行 scripts.readiness_calibration，对比这两项和算出的分数，给出调整权重的建议。")
        OutlinedTextField(day, {
            day = it
            saved = false
        }, label = { Label("日期（yyyy-MM-dd）") }, singleLine = true)
        OutlinedTextField(apple, {
            apple = it
            saved = false
        }, label = { Label("「健身」App 的准备指数（0–10）") }, singleLine = true)
        if (appleInvalid) Label("请填 0 到 10 之间的数", color = MaterialTheme.colors.error)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Label("自己的感觉")
            (0..5).forEach { value ->
                TextButton(onClick = {
                    perceived = value
                    saved = false
                }) { Label(if (value == 0) "不填" else "$value", color = if (perceived == value) LocalPalette.current.clay else LocalPalette.current.muted) }
            }
        }
        Muted("1 很累，3 一般，5 状态很好")
        Button(onClick = {
            saving = true
            scope.launch {
                saved = store.perform("没能保存记录") {
                    store.backend.uploadHealth(checkin.batch(date!!, java.time.Instant.now(), store.zone))
                    store.refresh()
                }
                saving = false
            }
        }, enabled = store.state.value.online && checkin.valid && !appleInvalid && date != null && date <= LocalDate.now(store.zone) && !saving) { Text(if (saving) "保存中…" else "保存") }
        if (saved) Muted("已保存 $day 的记录")
    }
}
