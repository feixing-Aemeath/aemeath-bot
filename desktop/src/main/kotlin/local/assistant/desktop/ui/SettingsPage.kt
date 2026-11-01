package local.assistant.desktop.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*
import local.assistant.core.*
import local.assistant.desktop.*
import kotlin.math.roundToInt

@Composable
fun SettingsPage(
    store: AppStore,
    state: DesktopState,
    route: Route,
    navigate: (String, String) -> Unit,
    source: (String) -> Unit,
) {
    when (route.page) {
        "connection" -> ConnectionPage(store)
        "plugin" -> state.plugins.find { it.id == route.argument }?.let { PluginPage(store, it) }
            ?: Muted("这个插件已经不在了。", Modifier.padding(24.dp))
        "schedule" -> state.settings?.let { SchedulePage(store, it) }
            ?: Muted("还没读到运行设置。", Modifier.padding(24.dp))
        "runs" -> RunsPage(store, state)
        "stickers" -> StickerGallery(store)
        "style" -> StylePage(store, state)
        else -> LazyColumn(
            contentPadding = PaddingValues(24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            when (route.page) {
                "settings" -> items(settingsLinks(store.device.mock, state.online, state.plugins)) { link ->
                    CardBox { SettingRow(link.title, link.detail) { navigate(link.id, "") } }
                }
                "plugins" -> {
                    if (state.plugins.isEmpty()) item { Muted("没有读到插件列表。") }
                    items(state.plugins, key = { it.id }) { plugin ->
                        CardBox {
                            SettingRow(plugin.name, plugin.statusLine(state.runs, state.todos, state.clock, store.zone)) {
                                navigate("plugin", plugin.id)
                            }
                        }
                    }
                }
                "sources" -> {
                    val sources = store.bots().filter { it.kind == BotKind.SOURCE }
                    items(sources, key = { it.id }) { bot ->
                        CardBox { SettingRow(bot.name, bot.intro) { source(bot.id) } }
                    }
                    item { Muted(if (sources.isEmpty()) "没有消息来源插件。" else "点开看它读到的消息。开关和白名单在「插件」里。") }
                }
            }
        }
    }
}

@Composable
private fun StylePage(store: AppStore, state: DesktopState) {
    Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            listOf("plain" to "素雅", "accent" to "点彩", "soft" to "柔光").forEach { (id, title) ->
                TextButton(onClick = { store.setStyle(id) }) {
                    Label((if (state.cardStyle == id) "✓ " else "") + title)
                }
            }
        }
        Muted(when (state.cardStyle) {
            "accent" -> "卡片顶上一道 Bot 的颜色，标题前带一个小图标。"
            "soft" -> "整张卡片铺一层 Bot 的颜色，标题也用这个颜色。"
            else -> "白底细边框，和 Claude 的 App 一样安静。"
        })
        ServerCard(
            Card("stat", "上海今天", Wire.parseToJsonElement("""{"value":"9–21","unit":"°C","label":"多云 · 降水概率 10%","symbol":"cloud.sun.fill"}""")),
            store, accent("#E86A9A"),
        )
    }
}

@Composable
private fun ConnectionPage(store: AppStore) {
    var url by remember { mutableStateOf(store.device.baseUrl) }
    var token by remember { mutableStateOf("") }
    var mock by remember { mutableStateOf(store.device.mock) }
    var clear by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(Modifier.padding(24.dp).verticalScroll(rememberScrollState())) {
        CardBox("连接") {
            ToggleRow("使用模拟数据", mock) { mock = it }
            OutlinedTextField(
                url, {
                    url = it
                    mock = false
                },
                label = { Label("后端地址") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                token, {
                    token = it
                    clear = false
                    if (it.isNotEmpty()) mock = false
                },
                label = { Label("令牌") },
                placeholder = { Muted(if (store.device.tokens.read().isNullOrEmpty()) "粘贴这台设备的令牌" else "已填写（留空则不修改）") },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true, modifier = Modifier.fillMaxWidth(),
            )
            ToggleRow("清除已保存的令牌", clear) { clear = it }
            Muted(if (isWindows()) "令牌由 Windows 当前用户加密保存。" else "开发调试：令牌只在这次运行期间有效，不保存到磁盘。")
            Muted(if (store.device.mock) "正在使用模拟数据" else if (store.state.value.online) "已连接 ${store.device.baseUrl}" else "连不上：检查地址、令牌和 Tailscale")
            Button(onClick = {
                saving = true
                val typed = token
                token = ""
                scope.launch {
                    store.connect(url, typed, mock, clear)
                    saving = false
                }
            }, enabled = !saving) { Text(if (saving) "连接中…" else "保存并连接") }
        }
    }
}

@Composable
private fun PluginPage(store: AppStore, plugin: PluginView) {
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    Column(
        Modifier.padding(24.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CardBox(plugin.name) {
            ToggleRow("启用", plugin.enabled, !busy && plugin.state != "unsupported") { next ->
                busy = true
                scope.launch {
                    store.updatePlugin(plugin, buildJsonObject { put("enabled", next) })
                    busy = false
                }
            }
            if (PluginConfigEdit.hasPreview(plugin)) {
                ToggleRow("仅预览", PluginConfigEdit.preview(plugin), !busy) { next ->
                    scope.launch {
                        store.updatePlugin(plugin, buildJsonObject { put("config", PluginConfigEdit.setting(plugin, "preview_only", JsonPrimitive(next))) })
                    }
                }
                Muted(if (PluginConfigEdit.preview(plugin)) "只读会话列表里每个会话今天的最新一条，不点开聊天，不会标为已读。" else "会点开白名单会话、往上滚动读完整内容，消息会被标为已读；需要辅助功能权限。")
            }
            Muted(plugin.description)
        }
        PluginConfigEdit.lists(plugin).forEach { (key, title) ->
            CardBox(title) {
                var typed by remember(plugin.id, key) { mutableStateOf("") }
                val names = PluginConfigEdit.names(plugin, key)
                fun save(next: List<String>) {
                    scope.launch {
                        store.updatePlugin(plugin, buildJsonObject {
                            put("config", PluginConfigEdit.setting(plugin, key, Wire.encodeToJsonElement(next)))
                        })
                    }
                }
                names.forEach { name ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Label(name, Modifier.weight(1f))
                        TextButton(onClick = { save(names.filterNot { it == name }) }) { Label("移除") }
                    }
                }
                OutlinedTextField(typed, { typed = it }, placeholder = { Muted("会话名称或 ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                TextButton(onClick = {
                    save(PluginConfigEdit.adding(typed, names))
                    typed = ""
                }, enabled = typed.isNotBlank()) { Label("添加") }
            }
        }
        CardBox("信息") {
            InfoRow("状态", plugin.statusLine(store.state.value.runs, store.state.value.todos, store.state.value.clock, store.zone))
            InfoRow("ID", plugin.id)
            InfoRow("版本", plugin.version)
            InfoRow("平台", plugin.platforms.joinToString("、"))
            InfoRow("提供", plugin.provides.joinToString("、"))
            InfoRow("权限", plugin.permissions.joinToString("、").ifEmpty { "无" })
            InfoRow("贴图", plugin.mascot?.sticker ?: "占位图（还没有贴图）")
            plugin.schedule?.let { InfoRow("时间表", CronText.describe(it)) }
            TextButton(onClick = { deleting = true }) { Label("删除插件", color = MaterialTheme.colors.error) }
        }
    }
    if (deleting) AlertDialog(
        onDismissRequest = { deleting = false },
        title = { Serif("删除插件？", 22) },
        text = { Label("删除「${plugin.name}」，其他插件照常运行。") },
        confirmButton = {
            TextButton(onClick = {
                scope.launch {
                    store.deletePlugin(plugin.id)
                    deleting = false
                }
            }) { Label("删除", color = MaterialTheme.colors.error) }
        },
        dismissButton = { TextButton(onClick = { deleting = false }) { Label("取消") } },
    )
}

@Composable
private fun SchedulePage(store: AppStore, settings: RuntimeSettings) {
    var draft by remember(settings) { mutableStateOf(ScheduleDraft.from(settings)) }
    var saving by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(Modifier.padding(24.dp).verticalScroll(rememberScrollState())) {
        CardBox("运行时间表") {
            OutlinedTextField(draft.source, {
                draft = draft.copy(source = it)
                saved = false
            }, label = { Label("读取消息") }, modifier = Modifier.fillMaxWidth())
            Muted(CronText.describe(draft.source))
            OutlinedTextField(draft.extraction, {
                draft = draft.copy(extraction = it)
                saved = false
            }, label = { Label("提炼待办") }, modifier = Modifier.fillMaxWidth())
            Muted(CronText.describe(draft.extraction))
            Label("待办最低置信度 %.2f".format(draft.threshold))
            Slider(draft.threshold.toFloat(), {
                draft = draft.copy(threshold = (it * 100).roundToInt() / 100.0)
                saved = false
            }, valueRange = 0f..1f)
            Label("优先使用的 Agent")
            (settings.harnessOrder + listOf("claude_code", "codex", "deepseek")).distinct().forEach { id ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(draft.preferred == id, {
                        draft = draft.copy(preferred = id)
                        saved = false
                    })
                    Label(Names.harness(id))
                }
            }
            Muted("时区 ${settings.timezone}；cron 五段依次是：分 时 日 月 星期。")
            Button(onClick = {
                saving = true
                scope.launch {
                    saved = store.updateSchedule(draft)
                    saving = false
                }
            }, enabled = draft.valid && !saving && draft.patch(settings).isNotEmpty()) { Text(if (saving) "保存中…" else "保存时间表") }
            if (saved) Muted("已保存")
        }
    }
}

@Composable
fun RunsPage(store: AppStore, state: DesktopState) {
    val scope = rememberCoroutineScope()
    Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            RunNowButton(store, state)
            TextButton(onClick = { scope.launch { store.runNow("apple_sync") } }, enabled = state.online) { Label("同步提醒事项") }
        }
        val headers = listOf("开始时间", "任务", "Agent", "用时", "结果", "原因")
        val widths = listOf(120, 170, 175, 100, 70, 230)
        val horizontal = rememberScrollState()
        Column(Modifier.horizontalScroll(horizontal).weight(1f)) {
            Row {
                headers.forEachIndexed { index, title -> Muted(title, Modifier.width(widths[index].dp).padding(8.dp)) }
            }
            Divider(color = LocalPalette.current.hairline)
            LazyColumn(Modifier.width(widths.sum().dp)) {
                if (state.runs.isEmpty()) item { Muted("还没有运行记录", Modifier.padding(12.dp)) }
                items(state.runs, key = { it.id }) { run ->
                    val cells = listOf(
                        format(instant(run.startedAt), "M月d日 HH:mm", store.zone),
                        Names.job(run.job),
                        Names.harness(run.harness) + if (run.attempt > 1) "（第 ${run.attempt} 次）" else "",
                        RunText.duration(run, state.clock),
                        RunText.status(run.status),
                        RunText.error(run.error).orEmpty(),
                    )
                    Row {
                        cells.forEachIndexed { index, value ->
                            Tooltip(value) {
                                Label(value, Modifier.width(widths[index].dp).padding(8.dp), color = if (run.status == "failed") MaterialTheme.colors.error else LocalPalette.current.ink, maxLines = 1)
                            }
                        }
                    }
                    Divider(color = LocalPalette.current.hairline)
                }
            }
        }
        HorizontalScrollbar(rememberScrollbarAdapter(horizontal), Modifier.fillMaxWidth())
    }
}

@Composable
private fun StickerGallery(store: AppStore) {
    var name by remember { mutableStateOf("aemeath") }
    var state by remember { mutableStateOf(MascotState.IDLE) }
    var act by remember { mutableStateOf<String?>(null) }
    Column(Modifier.padding(24.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            key(name, act) { Mascot(name, state, Modifier.size(240.dp), act = act) }
        }
        Serif(store.catalog.characters[name] ?: name, 22)
        Muted("点一下角色会有反应；系统关闭动画时显示静态头像。")
        Row(Modifier.horizontalScroll(rememberScrollState())) {
            MascotState.entries.forEach { next ->
                TextButton(onClick = {
                    state = next
                    act = null
                }) { Label(next.headline, color = if (next == state) LocalPalette.current.clay else LocalPalette.current.muted) }
            }
        }
        Row(Modifier.horizontalScroll(rememberScrollState())) {
            MascotMotion.acts.filter { it.sticker == name }.forEach { next ->
                TextButton(onClick = { act = next.id }) { Label(next.title) }
            }
        }
        store.catalog.names.sorted().chunked(5).forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                row.forEach { next ->
                    Column(Modifier.weight(1f).clickable {
                        name = next
                        act = null
                    }, horizontalAlignment = Alignment.CenterHorizontally) {
                        Mascot(next, modifier = Modifier.size(84.dp), animated = false)
                        Muted(store.catalog.characters[next] ?: next)
                    }
                }
                repeat(5 - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}
