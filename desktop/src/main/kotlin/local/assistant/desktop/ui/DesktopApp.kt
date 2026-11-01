package local.assistant.desktop.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import local.assistant.core.*
import local.assistant.desktop.*

data class Route(val page: String, val argument: String = "")

@Composable
fun DesktopApp(store: AppStore, state: DesktopState) {
    val initial = remember {
        if (store.device.chosen == "settings") Route("settings") else Route("bot", store.device.chosen)
    }
    var history by remember { mutableStateOf(listOf(initial)) }
    val route = history.last()
    val bots = store.bots()
    val selected = bots.find { it.id == route.argument } ?: bots.first()
    val scope = rememberCoroutineScope()

    fun select(id: String) {
        store.choose(id)
        history = listOf(Route("bot", id))
    }

    fun navigate(page: String, argument: String = "") {
        history = history + Route(page, argument)
    }

    fun settings() {
        store.device.chosen = "settings"
        history = listOf(Route("settings"))
    }

    CompositionLocalProvider(LocalCardStyle provides state.cardStyle) {
        Row(
            Modifier.fillMaxSize().background(LocalPalette.current.page).onPreviewKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) false else when {
                    event.isCtrlPressed && event.key == Key.R -> {
                        scope.launch { store.refresh() }
                        true
                    }
                    event.isCtrlPressed && event.key == Key.Comma -> {
                        settings()
                        true
                    }
                    event.isAltPressed && event.key == Key.DirectionLeft && history.size > 1 -> {
                        history = history.dropLast(1)
                        true
                    }
                    else -> false
                }
            },
        ) {
            Sidebar(store, state, if (route.page == "bot") selected.id else "settings", ::select, ::settings)
            Divider(Modifier.fillMaxHeight().width(1.dp), color = LocalPalette.current.hairline)
            Column(Modifier.weight(1f)) {
                Row(
                    Modifier.fillMaxWidth().height(52.dp).padding(horizontal = 18.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (history.size > 1) TextButton(onClick = { history = history.dropLast(1) }) { Label("‹ 返回") }
                    Label(routeTitle(route, selected, state), Modifier.weight(1f), weight = FontWeight.Medium)
                    if (route.page == "bot") {
                        if (selected.kind == BotKind.ASSISTANT) {
                            TextButton(onClick = { navigate("runs") }) { Muted("运行记录") }
                        } else if (!selected.builtIn) {
                            TextButton(onClick = { navigate("plugin", selected.id) }) { Muted("ⓘ 插件信息") }
                        }
                    }
                    TextButton(onClick = { scope.launch { store.refresh() } }, enabled = !state.loading) {
                        Muted(if (state.loading) "读取中…" else "刷新")
                    }
                }
                Divider(color = LocalPalette.current.hairline)
                Box(Modifier.weight(1f)) {
                    when (route.page) {
                        "bot" -> {
                            if (!state.settled && bots.none { it.id == route.argument }) {
                                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                            } else key(selected.id) {
                                CompositionLocalProvider(LocalBotTint provides accent(selected.look.accent)) {
                                    BotPage(selected, store, state, ::navigate)
                                }
                            }
                        }
                        "knowledge", "area", "file", "readers" -> key(route) {
                            KnowledgePage(store, route, state.revision, ::navigate)
                        }
                        else -> key(route) { SettingsPage(store, state, route, ::navigate, ::select) }
                    }
                }
            }
        }
        state.error?.let { message ->
            AlertDialog(
                onDismissRequest = store::dismissError,
                title = { Serif("出错了", 22) },
                text = { Label(message) },
                confirmButton = { TextButton(onClick = store::dismissError) { Label("好") } },
            )
        }
    }
}

private fun routeTitle(route: Route, bot: Bot, state: DesktopState): String = when (route.page) {
    "bot" -> bot.name
    "settings" -> "设置"
    "connection" -> "连接"
    "plugins" -> "插件"
    "plugin" -> state.plugins.find { it.id == route.argument }?.name ?: "插件信息"
    "sources" -> "消息来源"
    "schedule" -> "运行时间表"
    "runs" -> "运行记录"
    "stickers" -> "贴图预览"
    "style" -> "卡片风格"
    "knowledge" -> "知识库"
    "area", "file" -> Knowledge.fileName(route.argument)
    "readers" -> "谁能读"
    else -> "Aemeath Bot"
}

@Composable
private fun Sidebar(
    store: AppStore,
    state: DesktopState,
    selected: String,
    select: (String) -> Unit,
    settings: () -> Unit,
) {
    val bots = store.bots()
    Column(
        Modifier.width(DesktopLayout.sidebarWidth.dp).fillMaxHeight().background(LocalPalette.current.sidebar)
            .onPreviewKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown || event.key !in listOf(Key.DirectionUp, Key.DirectionDown)) false else {
                    val step = if (event.key == Key.DirectionUp) -1 else 1
                    val index = bots.indexOfFirst { it.id == selected }
                    select(bots[(index + step).coerceIn(0, bots.lastIndex)].id)
                    true
                }
            }.focusable(),
    ) {
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(10.dp)) {
            item { Muted("Bots", Modifier.padding(8.dp)) }
            items(bots.filter { it.kind in listOf(BotKind.ASSISTANT, BotKind.CHARACTER) }, key = { it.id }) { bot ->
                SidebarRow(bot, store, selected == bot.id) { select(bot.id) }
            }
            item { Divider(Modifier.padding(horizontal = 8.dp, vertical = 8.dp), color = LocalPalette.current.hairline) }
            items(bots.filter { it.kind == BotKind.KNOWLEDGE }, key = { it.id }) { bot ->
                SidebarRow(bot, store, selected == bot.id) { select(bot.id) }
            }
            if (bots.any { it.kind == BotKind.SOURCE }) {
                item { Muted("消息来源", Modifier.padding(start = 8.dp, top = 18.dp, bottom = 6.dp)) }
                items(bots.filter { it.kind == BotKind.SOURCE }, key = { it.id }) { bot ->
                    SidebarRow(bot, store, selected == bot.id) { select(bot.id) }
                }
            }
        }
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                StatusDot(if (state.online) Color(0xFF72967A) else LocalPalette.current.muted)
                Muted(if (store.device.mock) "模拟数据" else if (state.online) "Mac 在线" else "连不上 Mac")
            }
            Row(
                Modifier.fillMaxWidth().background(if (selected == "settings") LocalPalette.current.selection else Color.Transparent, RoundedCornerShape(8.dp))
                    .clickable(onClick = settings).padding(10.dp),
            ) { Label("⚙  设置") }
        }
    }
}

@Composable
private fun SidebarRow(bot: Bot, store: AppStore, selected: Boolean, action: () -> Unit) {
    val unread = store.unread(bot)
    val state = store.botState(bot)
    Row(
        Modifier.fillMaxWidth().background(if (selected) LocalPalette.current.selection else Color.Transparent, RoundedCornerShape(8.dp))
            .clickable(onClick = action).padding(horizontal = 8.dp, vertical = 9.dp),
        horizontalArrangement = Arrangement.spacedBy(9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box {
            Avatar(bot, Modifier.size(if (bot.kind == BotKind.SOURCE) 30.dp else 38.dp))
            Box(Modifier.align(Alignment.BottomEnd)) { StatusDot(stateColor(state)) }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Label(bot.name, weight = if (unread) FontWeight.Bold else FontWeight.Medium, maxLines = 1)
            val preview = store.messages(bot).lastOrNull()?.preview ?: bot.intro
            Label(preview, color = LocalPalette.current.muted, size = 12.sp, maxLines = 1)
        }
        if (unread) StatusDot(accent(bot.look.accent))
        if (bot.kind == BotKind.ASSISTANT) {
            val remaining = store.state.value.today?.todos.orEmpty().count { it.status == "pending" }
            if (remaining > 0) {
                Label("$remaining", Modifier.background(accent(bot.look.accent), CircleShape).padding(horizontal = 6.dp, vertical = 2.dp), color = Color.White)
            }
        }
    }
}

@Composable
fun StatusDot(color: Color) {
    Box(Modifier.size(7.dp).background(color, CircleShape))
}

fun stateColor(state: MascotState): Color = when (state) {
    MascotState.IDLE, MascotState.DONE -> Color(0xFF72967A)
    MascotState.WORKING -> Color(0xFFD99A43)
    MascotState.ERROR -> Color(0xFFD96659)
    MascotState.OFFLINE -> Color(0xFF98968F)
}
