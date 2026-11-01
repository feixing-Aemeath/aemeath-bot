package local.assistant.android.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.*
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.*
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import kotlinx.coroutines.launch
import local.assistant.android.data.*
import local.assistant.android.health.*
import local.assistant.core.PadPresentation

data class Route(val page: String, val argument: String = "")

@Composable
fun AemeathApp(model: AppModel) {
    val state by model.state.collectAsStateWithLifecycle()
    val owner = LocalLifecycleOwner.current
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_START) {
                model.start()
                scope.launch { HealthSync(context).sync(false, false) }
            }
            if (event == Lifecycle.Event.ON_STOP) model.stop()
        }
        owner.lifecycle.addObserver(observer)
        if (owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) model.start()
        onDispose {
            owner.lifecycle.removeObserver(observer)
            model.stop()
        }
    }
    AppContent(model, state)
}

/** Also renders previews and screenshots without polling or accessing health services. */
@Composable
internal fun AppContent(model: AppModel, state: AppState, initialRoute: Route? = null) {
    val routes = remember { mutableStateListOf<Route>().apply { initialRoute?.let { add(it) } } }
    var cardStyle by remember { mutableStateOf(model.device.style) }
    CompositionLocalProvider(LocalCardStyle provides cardStyle) {
        Surface(Modifier.fillMaxSize(), color = LocalPalette.current.page) {
            BoxWithConstraints(Modifier.fillMaxSize()) {
                val wideLayout = maxWidth.value >= PadPresentation.MEDIUM_WIDTH
                Row(Modifier.fillMaxSize()) {
                    if (wideLayout) {
                        BotSidebar(
                            model, state, if (routes.isEmpty()) model.selected().id else null,
                            onChoose = {
                                routes.clear()
                                model.choose(it)
                            },
                            onSettings = {
                                routes.clear()
                                routes.add(Route("settings"))
                            },
                            modifier = Modifier.width(280.dp).fillMaxHeight()
                        )
                        VerticalDivider(color = LocalPalette.current.hairline)
                    }
                    Box(Modifier.weight(1f)) {
                        val route = routes.lastOrNull()
                        fun navigate(page: String, arg: String = "") {
                            routes.add(Route(page, arg))
                        }
                        BackHandler(routes.isNotEmpty()) { routes.removeAt(routes.lastIndex) }
                        if (route == null && wideLayout) TabletBotPage(model, state) { navigate("knowledge") }
                        else if (route == null) BotScreen(
                            model,
                            state,
                            onSettings = { navigate("settings") },
                            onKnowledge = { navigate("knowledge") }
                        )
                        else PageFrame(
                            title = when (route.page) {
                                "settings" -> "设置"
                                "connection" -> "连接"
                                "plugins" -> "插件"
                                "plugin" -> state.plugins.find { it.id == route.argument }?.name ?: "插件"
                                "sources" -> "消息来源"
                                "schedule" -> "运行时间表"
                                "runs" -> "运行记录"
                                "style" -> "卡片风格"
                                "stickers" -> "贴图预览"
                                "health" -> "健康数据"
                                "knowledge" -> "知识库"
                                "area" -> route.argument.substringAfterLast('/')
                                "file" -> route.argument.substringAfterLast('/')
                                "readers" -> "谁能读"
                                else -> "Aemeath Bot"
                            },
                            onBack = { routes.removeAt(routes.lastIndex) }
                        ) {
                            when (route.page) {
                                "knowledge", "area", "file", "readers" -> KnowledgeScreen(
                                    model,
                                    route,
                                    state.revision,
                                    onNavigate = { page, arg -> navigate(page, arg) }
                                )
                                else -> SettingsScreen(
                                    model,
                                    state,
                                    route,
                                    onNavigate = { page, arg -> navigate(page, arg) },
                                    onSource = { id ->
                                        routes.clear()
                                        model.choose(id)
                                    },
                                    onStyle = {
                                        model.device.style = it
                                        cardStyle = it
                                    }
                                )
                            }
                        }
                    }
                }
            }
            if (state.error != null) AlertDialog(
                onDismissRequest = model::dismissError,
                title = { Text("出错了") },
                text = { Text(state.error!!) },
                confirmButton = { TextButton(onClick = model::dismissError) { Text("好") } }
            )
        }
    }
}

@Composable
fun PageFrame(title: String, onBack: () -> Unit, content: @Composable () -> Unit) {
    Column(
        Modifier
            .fillMaxSize()
            .background(LocalPalette.current.page)
            .statusBarsPadding()
            .navigationBarsPadding()
            .imePadding()
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .height(54.dp)
                .padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            TextButton(onClick = onBack) { Text("‹ 返回") }
            Text(
                title,
                Modifier.weight(1f),
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                fontWeight = androidx.compose.ui.text.font.FontWeight.Medium
            )
            Spacer(Modifier.width(64.dp))
        }
        HorizontalDivider(color = LocalPalette.current.hairline)
        Box(Modifier.weight(1f)) { content() }
    }
}
