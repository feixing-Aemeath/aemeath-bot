package local.assistant.desktop.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.material.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*
import local.assistant.core.*
import local.assistant.desktop.*

@Composable
fun KnowledgePage(store: AppStore, route: Route, revision: Long, navigate: (String, String) -> Unit) {
    var library by remember { mutableStateOf<KnowledgeOverview?>(null) }
    var files by remember { mutableStateOf<KnowledgeFiles?>(null) }
    var file by remember { mutableStateOf<KnowledgeFile?>(null) }
    var loading by remember { mutableStateOf(true) }
    var reload by remember { mutableIntStateOf(0) }
    LaunchedEffect(route, revision, reload) {
        loading = true
        try {
            library = store.backend.knowledge()
            if (route.page == "area") files = store.backend.knowledgeFiles(route.argument)
            if (route.page == "file") file = store.backend.knowledgeFile(route.argument)
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            store.error("没能读到知识库：${(error as? APIError)?.display ?: "操作失败"}")
        }
        loading = false
    }
    val overview = library
    if (overview == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            if (loading) CircularProgressIndicator() else Muted("没有读到知识库。后端更新以后才有这一页。")
        }
        return
    }
    Column(Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        when (route.page) {
            "knowledge" -> KnowledgeHome(store, overview, navigate) { reload++ }
            "area" -> overview.areas.find { it.path == route.argument }?.let { area ->
                KnowledgeAreaPage(store, area, files, navigate) { reload++ }
            } ?: Muted("这个主题已经不在了。")
            "file" -> file?.let { KnowledgeFilePage(store, it, navigate) } ?: Muted("没能打开这个文件。")
            "readers" -> ReaderPicker(store, overview, route.argument)
        }
    }
}

@Composable
private fun KnowledgeHome(
    store: AppStore,
    library: KnowledgeOverview,
    navigate: (String, String) -> Unit,
    reload: () -> Unit,
) {
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<KnowledgeSearchResult>?>(null) }
    var naming by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var agentName by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    LaunchedEffect(query) {
        results = null
        if (query.isNotBlank()) {
            delay(350)
            try {
                results = store.backend.searchKnowledge(query.trim())
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                results = emptyList()
                store.error("没能搜索，请检查连接")
            }
        }
    }
    OutlinedTextField(query, { query = it }, placeholder = { Muted("搜索资料和整理笔记") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
    if (query.isNotBlank()) {
        if (results == null) CircularProgressIndicator()
        if (results?.isEmpty() == true) Muted("没有搜到。")
        results.orEmpty().forEach { result ->
            CardBox {
                SettingRow(result.title, result.area) { navigate("file", result.path) }
                Muted(result.excerpt)
            }
        }
    } else {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Serif("主题", 20, Modifier.weight(1f))
            TextButton(onClick = { naming = true }) { Label("新建主题") }
        }
        if (library.topics.isEmpty()) Muted("还没有主题。点右上角新建一个，或者在后端的「知识库」文件夹里建子文件夹。")
        library.topics.forEach { AreaCard(it, navigate) }
        Muted("每个主题单独设谁能读。谁都不能读的主题只有你能看，也不会发给任何模型。")
        if (library.looseFiles > 0) Muted("知识库最外层有 ${library.looseFiles} 个文件不在任何主题里，Bot 和 Agent 都读不到。在后端把它们放进一个主题文件夹。")
        CardBox("整理") {
            InfoRow("上次", library.organize.lastRunAt?.let { RelativeTime.describe(instant(it), zone = store.zone) + " · " + RunText.status(library.organize.status.orEmpty()) } ?: "还没有运行过")
            library.organize.nextRunAt?.let { InfoRow("下次", format(instant(it), "M月d日 HH:mm", store.zone)) }
            Button(onClick = {
                scope.launch {
                    store.runNow("knowledge_organize")
                    reload()
                }
            }, enabled = library.organize.status != "running") {
                Text(if (library.pending > 0) "立即整理（${library.pending} 份待整理）" else "立即整理")
            }
            Muted("每晚把新放进来的资料整理成笔记，写在那个主题的「整理」文件夹里，只给能读这个主题的人看。笔记最后「我的批注」一节是你的，整理时不会改。")
        }
        Serif("Bot 的文件", 20)
        library.vaultFolders.forEach { AreaCard(it, navigate) }
        Muted("每个 Bot 都能读这些文件夹，它们的日常工作要用；这里只决定你自己运行的 Agent 能不能读。")
        CardBox {
            SettingRow("新主题默认谁能读", library.defaultReaders.joinToString("、").ifEmpty { "谁都不能" }) { navigate("readers", "@default") }
        }
        CardBox("你自己运行的 Agent") {
            val agents = library.readers.filter { it.kind == "agent" }.map { it.id }
            fun saveAgents(names: List<String>) {
                scope.launch {
                    store.perform("没能保存 Agent") {
                        store.backend.updateKnowledge(buildJsonObject { put("agents", Wire.encodeToJsonElement(names)) })
                        reload()
                    }
                }
            }
            agents.forEach { agent ->
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Label(agent, Modifier.weight(1f))
                    TextButton(onClick = { saveAgents(agents - agent) }) { Label("移除") }
                }
            }
            OutlinedTextField(agentName, { agentName = it }, label = { Label("Agent 名称") }, singleLine = true)
            TextButton(onClick = {
                saveAgents((agents + agentName.trim()).distinct())
                agentName = ""
            }, enabled = agentName.isNotBlank()) { Label("添加 Agent") }
        }
    }
    if (naming) AlertDialog(
        onDismissRequest = { if (!saving) naming = false },
        title = { Serif("新建主题", 22) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(name, { name = it }, placeholder = { Muted("比如：数据结构") })
                Muted("在后端的知识库里建一个文件夹，先按「新主题默认谁能读」设置，之后可以改。")
            }
        },
        confirmButton = {
            TextButton(onClick = {
                saving = true
                scope.launch {
                    if (store.perform("没能新建主题") { store.backend.createTopic(name.trim()) }) {
                        naming = false
                        reload()
                    }
                    saving = false
                }
            }, enabled = Knowledge.validTopic(name) && !saving) { Label("新建") }
        },
        dismissButton = { TextButton(onClick = { naming = false }, enabled = !saving) { Label("取消") } },
    )
}

@Composable
private fun AreaCard(area: KnowledgeArea, navigate: (String, String) -> Unit) {
    CardBox {
        SettingRow(area.name, if (area.pending > 0) "${area.pending} 份待整理" else area.summary) { navigate("area", area.path) }
        Muted(area.readers.joinToString("、").ifEmpty { "谁都不能读" })
    }
}

@Composable
private fun KnowledgeAreaPage(
    store: AppStore,
    area: KnowledgeArea,
    files: KnowledgeFiles?,
    navigate: (String, String) -> Unit,
    reload: () -> Unit,
) {
    var uploading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    CardBox("谁能读") {
        Muted(area.readers.joinToString("、").ifEmpty { "谁都不能读" })
        SettingRow("修改谁能读") { navigate("readers", area.path) }
        if (area.isTopic && area.readers.isEmpty()) Muted("只有你能看。整理任务不会处理这个主题，里面的东西不会发给任何模型。")
    }
    if (area.isTopic) {
        TextButton(onClick = {
            scope.launch {
                val file = chooseFile() ?: return@launch
                uploading = true
                store.perform("没能添加资料") {
                    uploadFile(store.backend, area, file)
                    reload()
                }
                uploading = false
            }
        }, enabled = !uploading) { Label(if (uploading) "添加中…" else "从文件添加") }
        Serif("整理好的笔记", 19)
        if (files?.entries?.none { it.kind == "note" } == true) Muted(if (area.readers.isEmpty()) "这个主题不整理。" else "还没有。放进资料以后，晚上会整理出来。")
        files?.entries.orEmpty().filter { it.kind == "note" }.forEach { EntryCard(it, navigate) }
    }
    Serif(if (area.isTopic) "资料" else "文件", 19)
    if (files == null) CircularProgressIndicator()
    val entries = files?.entries.orEmpty().filter { !area.isTopic || it.kind != "note" }
    if (files != null && entries.isEmpty()) Muted(if (area.isTopic) "还没有资料。用上面的添加按钮，或在后端放进「知识库/${area.name}」。" else "空的。")
    entries.forEach { EntryCard(it, navigate) }
}

@Composable
private fun EntryCard(entry: KnowledgeEntry, navigate: (String, String) -> Unit) {
    CardBox {
        SettingRow(entry.title, Knowledge.statusText(entry.status)) { navigate("file", entry.path) }
    }
}

@Composable
private fun KnowledgeFilePage(store: AppStore, file: KnowledgeFile, navigate: (String, String) -> Unit) {
    var opening by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Serif(file.title, 26)
    Muted(listOfNotNull(file.area, file.organizedBy?.let { Names.harness(it) }, Knowledge.statusText(file.status)).joinToString(" · "))
    if (file.hasOriginal) TextButton(onClick = {
        opening = true
        scope.launch {
            store.perform("没能打开原件") { openOriginal(store.backend, file) }
            opening = false
        }
    }, enabled = !opening) { Label(if (opening) "正在打开…" else "打开原件") }
    CardBox("谁能读") { Muted(file.readers.joinToString("、").ifEmpty { "谁都不能读" }) }
    if (file.extracted) Muted("下面是后端从${if (file.format == "image") "图片" else if (file.format == "pdf") " PDF " else "文件"}里读出的文字。")
    MarkdownText(file.displayText)
    if (file.truncated) Muted("文字太长，只显示了开头。打开原件看全部。")
    if (file.sources.isNotEmpty()) Serif("来源", 19)
    file.sources.forEach { reference ->
        SettingRow(Knowledge.fileName(reference.path), reference.label) { navigate("file", reference.path) }
    }
    if (file.notes.isNotEmpty()) Serif("整理好的笔记", 19)
    file.notes.forEach { path -> SettingRow(Knowledge.fileName(path)) { navigate("file", path) } }
}

@Composable
private fun ReaderPicker(store: AppStore, library: KnowledgeOverview, path: String) {
    val area = library.areas.find { it.path == path }
    val initial = if (path == "@default") library.defaultReaders else area?.readers.orEmpty()
    val locked = area?.locked.orEmpty()
    val selection = remember(path) { ReaderSelection(initial) }
    var chosen by remember(path) { mutableStateOf(initial) }
    val scope = rememberCoroutineScope()
    for ((kind, title) in listOf("bot" to "Bot", "agent" to "你自己运行的 Agent")) {
        Serif(title, 19)
        library.readers.filter { it.kind == kind }.forEach { reader ->
            CardBox {
                ToggleRow(reader.id, reader.id in chosen || reader.id in locked, reader.id !in locked) { on ->
                    chosen = chosen.filterNot { it == reader.id } + if (on) listOf(reader.id) else emptyList()
                    scope.launch {
                        store.perform("没能保存") {
                            selection.set(reader.id, on) { names ->
                                if (path == "@default") {
                                    store.backend.updateKnowledge(buildJsonObject { put("default_readers", Wire.encodeToJsonElement(names)) }).defaultReaders
                                } else store.backend.setReaders(path, names).readers
                            }
                        }
                        chosen = selection.chosen
                    }
                }
                if (reader.id in locked) Muted("始终能读")
            }
        }
    }
    Muted(when {
        path == "@default" -> "只影响以后新建的主题，已有主题不变。"
        area?.isTopic == true -> "Bot 在对话里、Agent 用自己的令牌连接时，后端按这里限定。在后端电脑上直接读磁盘的 Agent 不受这里限制。"
        else -> "Bot 始终能读这个文件夹。Agent 要用自己的令牌连接，才按这里限定。"
    })
}
