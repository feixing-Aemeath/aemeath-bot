package local.assistant.desktop

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.*
import local.assistant.core.BotKind
import local.assistant.desktop.ui.*
import java.awt.Dimension
import java.awt.EventQueue
import java.awt.GraphicsEnvironment
import java.awt.Point
import java.awt.event.WindowAdapter
import java.awt.event.WindowEvent

fun main(arguments: Array<String>) = application {
    val scope = rememberCoroutineScope()
    val device = remember { DeviceSettings() }
    val store = remember {
        if ("--mock" in arguments) device.mock = true
        AppStore(device, Resources.catalog, scope)
    }
    val state by store.state.collectAsState()
    var mainVisible by remember { mutableStateOf(true) }
    var popupVisible by remember { mutableStateOf(false) }
    var popupPoint by remember { mutableStateOf(Point(800, 500)) }
    var trayAvailable by remember { mutableStateOf(false) }
    val icon = remember { Resources.image("aemeath.png")?.toComposeImageBitmap()?.let { BitmapPainter(it) } }
    val mainState = rememberWindowState(width = 1180.dp, height = 820.dp)
    val assistant = store.bots().first { it.kind == BotKind.ASSISTANT }
    val mascotState = store.botState(assistant)
    val trayText = TrayText.from(state.today, state.online, mascotState)
    val openMain: () -> Unit = {
        popupVisible = false
        mainVisible = true
        mainState.isMinimized = false
    }
    val quit: () -> Unit = {
        store.stop()
        exitApplication()
    }
    val tray = remember {
        DesktopTray(
            openPopup = { point ->
                EventQueue.invokeLater {
                    popupPoint = popupPosition(point)
                    popupVisible = !popupVisible
                }
            },
            openMain = { EventQueue.invokeLater { openMain() } },
            quit = { EventQueue.invokeLater { quit() } },
            availability = { trayAvailable = it },
        )
    }
    DisposableEffect(store) {
        store.start()
        onDispose {
            store.stop()
            tray.close()
        }
    }
    LaunchedEffect(trayText, mascotState) { tray.update(trayText, mascotState) }

    Window(
        onCloseRequest = { if (trayAvailable) mainVisible = false else quit() },
        title = "Aemeath Bot",
        state = mainState,
        visible = mainVisible,
        icon = icon,
    ) {
        LaunchedEffect(Unit) { window.minimumSize = Dimension(820, 600) }
        LaunchedEffect(mainVisible) {
            if (mainVisible) {
                window.toFront()
                window.requestFocus()
            }
        }
        DesktopTheme {
            CompositionLocalProvider(LocalAnimationActive provides mainVisible) { DesktopApp(store, state) }
        }
    }
    if (popupVisible) {
        val popupState = rememberWindowState(
            width = 380.dp,
            height = 500.dp,
            position = WindowPosition.Absolute(popupPoint.x.dp, popupPoint.y.dp),
        )
        Window(
            onCloseRequest = { popupVisible = false },
            title = "今日待办 · Aemeath Bot",
            state = popupState,
            undecorated = true,
            alwaysOnTop = true,
            resizable = false,
            icon = icon,
        ) {
            DisposableEffect(window) {
                val listener = object : WindowAdapter() {
                    override fun windowLostFocus(event: WindowEvent) { popupVisible = false }
                }
                window.addWindowFocusListener(listener)
                onDispose { window.removeWindowFocusListener(listener) }
            }
            DesktopTheme {
                Column(
                    Modifier.fillMaxSize().background(LocalPalette.current.page)
                        .border(1.dp, LocalPalette.current.hairline).padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Mascot(assistant.look.sticker, mascotState, Modifier.size(64.dp))
                        Column {
                            Serif(trayText.headline, 18)
                            Muted(if (state.online) mascotState.headline else "连不上 Mac")
                        }
                    }
                    Divider(color = LocalPalette.current.hairline)
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                        TodoList(state.today?.todos.orEmpty().take(6), store, compact = true)
                    }
                    Divider(color = LocalPalette.current.hairline)
                    RunNowButton(store, state, "立即提炼一次")
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        TextButton(onClick = openMain) { Label("打开主窗口") }
                        TextButton(onClick = quit) { Muted("退出") }
                    }
                }
            }
        }
    }
}

private fun popupPosition(point: Point): Point {
    val screens = GraphicsEnvironment.getLocalGraphicsEnvironment().screenDevices
    val bounds = screens.map { it.defaultConfiguration.bounds }.find { it.contains(point) }
        ?: screens.first().defaultConfiguration.bounds
    return Point(
        (point.x - 360).coerceIn(bounds.x, maxOf(bounds.x, bounds.x + bounds.width - 380)),
        (point.y - 500).coerceIn(bounds.y, maxOf(bounds.y, bounds.y + bounds.height - 500)),
    )
}
