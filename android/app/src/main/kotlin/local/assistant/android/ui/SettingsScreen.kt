package local.assistant.android.ui

import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.*
import androidx.compose.ui.unit.dp
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*
import local.assistant.android.data.*
import local.assistant.android.health.*
import local.assistant.core.*
import java.time.Instant

@Composable
fun SettingsScreen(
    model: AppModel,
    s: AppState,
    route: Route,
    onNavigate: (String, String) -> Unit,
    onSource: (String) -> Unit,
    onStyle: (String) -> Unit
) {
    when (route.page) {
        "settings" -> SettingsHome(model, s, onNavigate)
        "connection" -> ConnectionPage(model)
        "plugins" ->
            LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (s.plugins.isEmpty()) item { Muted("没有读到插件列表。") }
                items(s.plugins, key = { it.id }) { p ->
                    CardBox {
                        SettingRow(
                            p.name,
                            p.statusLine(
                                s.runs,
                                s.todos,
                                Instant.now(),
                                model.zone
                            )
                        ) { onNavigate("plugin", p.id) }
                    }
                }
            }
        "plugin" -> s.plugins.find { it.id == route.argument }?.let { PluginPage(model, it) } ?: Muted("这个插件已经不在了。")
        "sources" -> LazyColumn(contentPadding = PaddingValues(16.dp)) {
            items(model.bots().filter { it.kind == BotKind.SOURCE }, key = { it.id }) { bot ->
                SettingRow(bot.name, bot.intro) { onSource(bot.id) }
            }
            item {
                Muted(if (model.bots().none { it.kind == BotKind.SOURCE }) "没有消息来源插件。" else "点开看它读到的消息。开关和白名单在「插件」里。")
            }
        }
        "schedule" -> s.settings?.let { SchedulePage(model, it) } ?: Muted("还没读到运行设置。")
        "runs" -> LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item { TextButton(onClick = { model.runNow() }) { Text("立即运行一次") } }
            if (s.runs.isEmpty()) item { Muted("还没有运行记录") }
            items(s.runs, key = { it.id }) { run ->
                CardBox {
                    Text("${Names.job(run.job)} · ${RunText.status(run.status)}")
                    Muted(
                        "${Names.harness(run.harness)} · ${RelativeTime.describe(instant(run.startedAt),zone=model.zone)} · 用时 ${RunText.duration(run)}" +
                            (if (run.attempt > 1) " · 第 ${run.attempt} 次" else "")
                    )
                    RunText.error(run.error)?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                }
            }
            item { TextButton(onClick = { model.runNow("apple_sync") }) { Text("同步提醒事项") } }
        }
        "style" -> StylePage(model, onStyle)
        "stickers" -> StickerGallery(model)
        "health" -> HealthPage(model)
    }
}

@Composable
private fun SettingsHome(model: AppModel, s: AppState, navigate: (String, String) -> Unit) {
    val links = listOf(
        Triple("连接", "connection", if (model.device.mock) "模拟数据" else if (s.online) "Mac 在线" else "连不上"),
        Triple("插件", "plugins", "${s.plugins.count { it.enabled }} 个已启用"),
        Triple("消息来源", "sources", "${model.bots().count { it.kind==BotKind.SOURCE }} 个"),
        Triple("健康数据", "health", if (model.device.tablet) "由手机上传" else if (model.device.healthEnabled) "已开启" else "未开启"),
        Triple("运行时间表", "schedule", ""),
        Triple("运行记录", "runs", ""),
        Triple("卡片风格", "style", styleName(model.device.style)),
        Triple("贴图预览", "stickers", "")
    )
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        items(links) { (title, page, detail) ->
            CardBox { SettingRow(title, detail.takeIf { it.isNotEmpty() }) { navigate(page, "") } }
        }
    }
}

@Composable
private fun ConnectionPage(model: AppModel) {
    var url by remember { mutableStateOf(model.device.baseUrl) }
    var token by remember { mutableStateOf("") }
    var mock by remember { mutableStateOf(model.device.mock) }
    var saving by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    Column(
        Modifier
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        CardBox("连接") {
            ToggleRow("使用模拟数据", mock) { mock = it }
            OutlinedTextField(
                url,
                {
                    url = it
                    mock = false
                },
                label = { Text("后端地址") },
                singleLine = true,
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    autoCorrectEnabled = false
                ),
                modifier = Modifier.fillMaxWidth()
            )
            OutlinedTextField(
                token,
                {
                    token = it
                    if (it.isNotEmpty()) mock = false
                },
                label = { Text("令牌") },
                placeholder = { Text(if (model.device.hasToken) "已加密保存（留空则不修改）" else "粘贴这台设备的令牌") },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    autoCorrectEnabled = false
                ),
                modifier = Modifier.fillMaxWidth()
            )
            Muted(
                if (model.device.mock)
                    "正在使用模拟数据"
                else if (model.state.value.online) "已连接 ${model.device.baseUrl}" else "连不上：检查地址、令牌和 Tailscale"
            )
            Button(
                onClick = {
                    saving = true
                    val typed = token
                    token = ""
                    scope.launch {
                        model.connect(url, typed, mock)
                        HealthSync(context).schedule()
                        saving = false
                    }
                },
                enabled = !saving
            ) { Text(if (saving) "连接中…" else "保存并连接") }
        }
    }
}

@Composable
private fun PluginPage(model: AppModel, plugin: PluginView) {
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(
        Modifier
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        CardBox {
            ToggleRow("启用", plugin.enabled, plugin.state != "unsupported") {
                model.updatePlugin(plugin.id, buildJsonObject { put("enabled", it) })
            }
            if (PluginConfigEdit.hasPreview(plugin)) {
                ToggleRow("仅预览", PluginConfigEdit.preview(plugin)) { next ->
                    model.updatePlugin(
                        plugin.id,
                        buildJsonObject {
                            put("config", PluginConfigEdit.setting(plugin, "preview_only", JsonPrimitive(next)))
                        }
                    )
                }
                Muted(
                    if (PluginConfigEdit.preview(plugin))
                        "只读会话列表里每个会话今天的最新一条，不点开聊天，不会标为已读。"
                    else
                        "会点开白名单会话、往上滚动读完整内容，消息会被标为已读；需要辅助功能权限。"
                )
            }
            Muted(plugin.description)
        }
        PluginConfigEdit.lists(plugin)
            .forEach { (key, title) ->
                CardBox(title) {
                    var typed by remember(plugin.id, key) { mutableStateOf("") }
                    fun save(names: List<String>) {
                        model.updatePlugin(
                            plugin.id,
                            buildJsonObject {
                                put("config", PluginConfigEdit.setting(plugin, key, Wire.encodeToJsonElement(names)))
                            }
                        )
                    }
                    val names = PluginConfigEdit.names(plugin, key)
                    names.forEach { name ->
                        Row(
                            Modifier.fillMaxWidth(),
                            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically
                        ) {
                            Text(name, Modifier.weight(1f))
                            TextButton(onClick = { save(names.filterNot { it == name }) }) { Text("移除") }
                        }
                    }
                    OutlinedTextField(
                        typed,
                        { typed = it },
                        placeholder = { Text("会话名称或 ID") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    TextButton(
                        onClick = {
                            save(PluginConfigEdit.adding(typed, names))
                            typed = ""
                        },
                        enabled = typed.isNotBlank()
                    ) { Text("添加") }
                }
            }
        CardBox("信息") {
            InfoRow(
                "状态",
                model.statusLine(
                    model.bots().find { it.id == plugin.id } ?: Bot(
                        plugin.id,
                        plugin.name,
                        plugin.name,
                        BotKind.CHARACTER,
                        plugin.mascot ?: PluginMascot()
                    )
                )
            )
            InfoRow("ID", plugin.id)
            InfoRow("版本", plugin.version)
            InfoRow("平台", plugin.platforms.joinToString("、"))
            InfoRow("提供", plugin.provides.joinToString("、"))
            InfoRow("权限", plugin.permissions.joinToString("、").ifEmpty { "无" })
            InfoRow("贴图", plugin.mascot?.sticker ?: "占位图（还没有贴图）")
            plugin.schedule?.let { InfoRow("时间表", CronText.describe(it)) }
        }
    }
}

@Composable
private fun SchedulePage(model: AppModel, settings: RuntimeSettings) {
    var source by remember(settings) { mutableStateOf(settings.sourceCron) }
    var extraction by remember(settings) { mutableStateOf(settings.extractionCron) }
    var threshold by remember(settings) { mutableFloatStateOf(settings.confidenceThreshold.toFloat()) }
    var first by remember(settings) { mutableStateOf(settings.harnessOrder.firstOrNull() ?: "claude_code") }
    var saved by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val patch = buildJsonObject {
        if (source != settings.sourceCron) put("source_cron", source)
        if (extraction != settings.extractionCron) put("extraction_cron", extraction)
        if (kotlin.math.abs(threshold - settings.confidenceThreshold) > .001) put(
            "confidence_threshold",
            (threshold * 100).toInt() / 100.0
        )
        if (first != settings.harnessOrder.firstOrNull()) put(
            "harness_order",
            Wire.encodeToJsonElement(listOf(first) + settings.harnessOrder.filterNot { it == first })
        )
    }
    Column(
        Modifier
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        CardBox("运行时间表") {
            OutlinedTextField(
                source,
                {
                    source = it
                    saved = false
                },
                label = { Text("读取消息") },
                modifier = Modifier.fillMaxWidth()
            )
            Muted(CronText.describe(source))
            OutlinedTextField(
                extraction,
                {
                    extraction = it
                    saved = false
                },
                label = { Text("提炼待办") },
                modifier = Modifier.fillMaxWidth()
            )
            Muted(CronText.describe(extraction))
            Text("待办最低置信度 %.2f".format(threshold))
            Slider(
                threshold,
                {
                    threshold = it
                    saved = false
                },
                valueRange = 0.5f..0.95f,
                steps = 8
            )
            Text("优先使用的 Agent")
            (settings.harnessOrder.ifEmpty { listOf("claude_code", "codex") }).forEach { id ->
                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(first == id, { first = id })
                    Text(Names.harness(id))
                }
            }
            Muted("时区 ${settings.timezone}；cron 五段依次是：分 时 日 月 星期。")
            Button(
                onClick = {
                    scope.launch {
                        saved = model.perform("没能保存时间表") {
                            model.backend.updateSettings(patch)
                            model.refresh()
                        }
                    }
                },
                enabled = CronText.valid(source) && CronText.valid(extraction) && patch.isNotEmpty()
            ) { Text("保存时间表") }
            if (saved) Muted("已保存")
        }
    }
}

fun styleName(style: String) = when (style) {
    "accent" -> "点彩"
    "soft" -> "柔光"
    else -> "素雅"
}

@Composable
private fun StylePage(model: AppModel, onStyle: (String) -> Unit) {
    var selected by remember { mutableStateOf(model.device.style) }
    Column(
        Modifier
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            listOf("plain", "accent", "soft")
                .forEach { style ->
                    FilterChip(
                        selected == style,
                        {
                            selected = style
                            onStyle(style)
                        },
                        label = { Text(styleName(style)) }
                    )
                }
        }
        Muted(
            when (selected) {
                "accent" -> "卡片顶上一道 Bot 的颜色，标题前带一个小图标。"
                "soft" -> "整张卡片铺一层 Bot 的颜色，标题也用这个颜色。"
                else -> "白底细边框，和 Claude 的 App 一样安静。"
            }
        )
        CompositionLocalProvider(LocalCardStyle provides selected) {
            ServerCard(
                Card(
                    "stat",
                    "上海今天",
                    Wire.parseToJsonElement(
                        """{"value":"9–21","unit":"°C","label":"多云 · 降水概率 10%","symbol":"cloud.sun.fill"}"""
                    )
                ),
                {},
                {},
                {}
            )
        }
    }
}

@Composable
private fun StickerGallery(model: AppModel) {
    var name by remember { mutableStateOf("aemeath") }
    var state by remember { mutableStateOf(MascotState.IDLE) }
    var act by remember { mutableStateOf<String?>(null) }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Box(Modifier.fillMaxWidth(), contentAlignment = androidx.compose.ui.Alignment.Center) {
                Mascot(
                    name,
                    state,
                    Modifier.size(230.dp),
                    act = act
                )
            }
        }
        item {
            Text(model.catalog.characters[name] ?: name)
            Muted("点一下角色会有反应；系统关闭动画时显示静态头像。")
        }
        item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MascotState.entries.forEach { st ->
                    FilterChip(
                        state == st && act == null,
                        {
                            state = st
                            act = null
                        },
                        label = { Text(st.headline) }
                    )
                }
            }
        }
        items(MascotMotion.acts.filter { it.sticker == name }.chunked(2)) { pair ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                pair.forEach { a -> FilterChip(act == a.id, { act = a.id }, label = { Text(a.title) }) }
            }
        }
        items(model.catalog.names.sorted().chunked(3)) { row ->
            Row(Modifier.fillMaxWidth()) {
                row.forEach { n ->
                    Column(
                        Modifier
                            .weight(1f)
                            .clickable {
                                name = n
                                act = null
                            },
                        horizontalAlignment = androidx.compose.ui.Alignment.CenterHorizontally
                    ) {
                        Mascot(n, modifier = Modifier.size(72.dp), animated = false)
                        Muted(model.catalog.characters[n] ?: n)
                    }
                }
            }
        }
    }
}

@Composable
private fun HealthPage(model: AppModel) {
    if (model.device.tablet) {
        Muted("平板只显示健康简报，健康数据由手机上传。", Modifier.padding(20.dp))
        return
    }
    val context = LocalContext.current
    val sync = remember { HealthSync(context) }
    val scope = rememberCoroutineScope()
    var revision by remember { mutableIntStateOf(0) }
    var sending by remember { mutableStateOf(false) }
    var granted by remember { mutableStateOf<Set<String>>(emptySet()) }
    val launcher =
        rememberLauncherForActivityResult(PermissionController.createRequestPermissionResultContract()) { permissions ->
            granted = permissions
            model.device.healthEnabled = permissions.isNotEmpty()
            sync.schedule()
            scope.launch {
                sending = true
                sync.sync(true, false)
                sending = false
                revision++
            }
        }
    LaunchedEffect(revision) { granted = sync.granted() }
    Column(
        Modifier
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        CardBox {
            when (sync.availability) {
                HealthConnectClient.SDK_UNAVAILABLE -> Text("这台设备没有 Health Connect。")
                HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> {
                    Text("请安装或更新 Health Connect 后再授权。")
                    TextButton(
                        onClick = {
                            runCatching {
                                context.startActivity(
                                    Intent(
                                        Intent.ACTION_VIEW,
                                        android.net.Uri.parse("market://details?id=com.google.android.apps.healthdata")
                                    )
                                )
                            }
                        }
                    ) { Text("打开 Health Connect 商店页面") }
                }
                else -> {
                    TextButton(onClick = { launcher.launch(sync.permissions()) }) {
                        Text(if (granted.isEmpty()) "允许读取健康数据" else "更改健康数据权限")
                    }
                    if (granted.isNotEmpty()) Muted(
                        "已允许 ${granted.count { it!=androidx.health.connect.client.permission.HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND }} 类数据"
                    )
                    ToggleRow("后台自动上传", model.device.healthEnabled) {
                        model.device.healthEnabled = it
                        sync.schedule()
                        revision++
                    }
                    InfoRow(
                        "上次上传",
                        if (model.device.healthLast == 0L) "还没上传过" else RelativeTime.describe(
                            Instant.ofEpochMilli(model.device.healthLast)
                        ) + if (model.device.healthCount > 0) "，${model.device.healthCount} 条" else "，没有新数据"
                    )
                    Button(
                        onClick = {
                            sending = true
                            scope.launch {
                                sync.sync(true, false)
                                sending = false
                                revision++
                            }
                        },
                        enabled = !sending && !model.device.mock && model.device.healthEnabled
                    ) { Text(if (sending) "上传中…" else "立即上传") }
                }
            }
            model.device.healthProblem?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
        Muted(
            if (model.device.mock)
                "现在用的是模拟数据，连上后端之后才会上传。"
            else
                "读取 Health Connect 中已经授权的指标，只上传到你填写的后端。第一次补传最近 14 天，之后只传新增样本；心率保留每次读数、睡眠保留各阶段、训练按次上传。"
        )
        Muted("后台上传需要单独授权；不支持后台读取的设备只在打开 App 时补传。Health Connect 设置可以随时撤销权限。")
        TextButton(
            onClick = {
                runCatching { context.startActivity(Intent(HealthConnectClient.ACTION_HEALTH_CONNECT_SETTINGS)) }
            }
        ) { Text("打开 Health Connect 设置") }
    }
}

@Composable
fun PrivacyPage(onBack: () -> Unit) {
    PageFrame("健康数据使用说明", onBack) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Serif("你的健康数据", 24)
            Text("Aemeath Bot 只读取你授权的 Health Connect 项目，逐条通过 Bearer 令牌上传到你自己填写的后端地址，用于显示健康趋势和简报。")
            Text("上传内容包括心率等逐条样本、睡眠阶段和每次训练的起止时间、时长与来源；不接入广告、统计或崩溃上报服务。缺少权限或设备未提供的指标不会补造。")
            Text("令牌由 Android Keystore 加密，App 禁用备份。你可以在 Health Connect 中撤销权限，也可以在 App 的健康数据页关闭自动上传。")
        }
    }
}
