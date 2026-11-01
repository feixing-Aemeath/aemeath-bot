package local.assistant.android.ui

import android.content.Intent
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import local.assistant.android.data.AppModel
import local.assistant.core.*
import java.io.File
import java.util.UUID

@Composable
fun KnowledgeScreen(
    model: AppModel,
    route: Route,
    revision: Long,
    onNavigate: (String, String) -> Unit
) {
    var overview by remember(route) { mutableStateOf<KnowledgeOverview?>(null) }
    var files by remember(route) { mutableStateOf<KnowledgeFiles?>(null) }
    var file by remember(route) { mutableStateOf<KnowledgeFile?>(null) }
    var loading by remember(route) { mutableStateOf(true) }
    var reload by remember(route) { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(route, revision, reload) {
        loading = true
        try {
            overview = model.backend.knowledge()
            if (route.page == "area") files = model.backend.knowledgeFiles(route.argument)
            if (route.page == "file") file = model.backend.knowledgeFile(route.argument)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if ((e as? APIError)?.category != "not_found") model.error("没能读到知识库：${(e as? APIError)?.display ?: "操作失败"}")
        }
        loading = false
    }
    if (loading && overview == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = androidx.compose.ui.Alignment.Center) {
            CircularProgressIndicator()
        }
        return
    }
    val library = overview
    if (library == null) {
        Muted("没有读到知识库。后端更新以后才有这一页。", Modifier.padding(16.dp))
        return
    }
    when (route.page) {
        "knowledge" -> KnowledgeHome(model, library, onNavigate) { reload++}
        "area" -> library.areas.find { it.path == route.argument }
            ?.let { area ->
                KnowledgeAreaPage(
                    model,
                    area,
                    files,
                    onNavigate
                ) { reload++}
            }
        "file" -> file?.let { KnowledgeFilePage(model, it, onNavigate) } ?: Muted("没能打开这个文件。", Modifier.padding(16.dp))
        "readers" -> {
            val area = library.areas.find { it.path == route.argument }
            val initial = if (route.argument == "@default") library.defaultReaders else area?.readers.orEmpty()
            key(route.argument) {
                ReaderPicker(
                    library.readers,
                    initial,
                    area?.locked.orEmpty(),
                    if (route.argument == "@default")
                        "只影响以后新建的主题，已有主题不变。"
                    else if (area?.isTopic == true)
                        "Bot 在对话里、Agent 用自己的令牌连接时，后端按这里限定。在后端电脑上直接读磁盘的 Agent 不受这里限制。"
                    else
                        "Bot 始终能读这个文件夹。Agent 要用自己的令牌连接，才按这里限定。",
                    model
                ) { names ->
                    if (route.argument == "@default") model.backend.updateKnowledge(
                        buildJsonObject { put("default_readers", Wire.encodeToJsonElement(names)) }
                    ).defaultReaders
                    else model.backend.setReaders(route.argument, names).readers
                }
            }
        }
    }
}

@Composable
private fun KnowledgeHome(
    model: AppModel,
    library: KnowledgeOverview,
    navigate: (String, String) -> Unit,
    reload: () -> Unit
) {
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<KnowledgeSearchResult>?>(null) }
    var naming by remember { mutableStateOf(false) }
    var newName by remember { mutableStateOf("") }
    var organizing by remember { mutableStateOf(false) }
    var agentName by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    LaunchedEffect(query) {
        results = null
        if (query.isNotBlank()) {
            delay(350)
            try {
                results = model.backend.searchKnowledge(query.trim())
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                results = emptyList()
                model.error("没能搜索，请检查连接")
            }
        }
    }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            OutlinedTextField(
                query,
                { query = it },
                placeholder = { Text("搜索资料和整理笔记") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true
            )
        }
        if (query.isNotBlank()) {
            if (results == null) item { CircularProgressIndicator() }
            if (results?.isEmpty() == true) item { Muted("没有搜到。") }
            items(results.orEmpty(), key = { it.path }) { r ->
                CardBox {
                    SettingRow(r.title, r.area) { navigate("file", r.path) }
                    Muted(r.excerpt)
                }
            }
        } else {
            item {
                Row(Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    Serif("主题", 19, Modifier.weight(1f))
                    TextButton(
                        onClick = {
                            newName = ""
                            naming = true
                        }
                    ) { Text("新建主题") }
                }
            }
            if (library.topics.isEmpty()) item { Muted("还没有主题。点右上角新建一个，或者在后端的「知识库」文件夹里建子文件夹。") }
            items(library.topics, key = { it.path }) { area -> AreaCard(area, navigate) }
            item { Muted("每个主题单独设谁能读。谁都不能读的主题只有你能看，也不会发给任何模型。") }
            if (library.looseFiles > 0) item {
                CardBox { Muted("知识库最外层有 ${library.looseFiles} 个文件不在任何主题里，Bot 和 Agent 都读不到。在后端把它们放进一个主题文件夹。") }
            }
            item {
                CardBox("整理") {
                    InfoRow(
                        "上次",
                        library.organize.lastRunAt?.let {
                            RelativeTime.describe(instant(it), LocalAnimationTime.current ?: java.time.Instant.now(), model.zone) + " · " + RunText.status(
                                library.organize.status.orEmpty()
                            )
                        } ?: "还没有运行过"
                    )
                    library.organize.nextRunAt?.let { InfoRow("下次", format(instant(it), "M月d日 HH:mm", model.zone)) }
                    Button(
                        onClick = {
                            organizing = true
                            scope.launch {
                                model.perform("没能开始整理") { model.backend.runJob("knowledge_organize") }
                                organizing = false
                                reload()
                            }
                        },
                        enabled = !organizing && library.organize.status != "running"
                    ) { Text(if (library.pending > 0) "立即整理（${library.pending} 份待整理）" else "立即整理") }
                    Muted("每晚把新放进来的资料整理成笔记，写在那个主题的「整理」文件夹里，只给能读这个主题的人看。笔记最后「我的批注」一节是你的，整理时不会改。")
                }
            }
            item { Serif("Bot 的文件", 19) }
            items(library.vaultFolders, key = { it.path }) { area -> AreaCard(area, navigate) }
            item { Muted("每个 Bot 都能读这些文件夹，它们的日常工作要用；这里只决定你自己运行的 Agent 能不能读。") }
            item {
                CardBox {
                    SettingRow("新主题默认谁能读", library.defaultReaders.joinToString("、").ifEmpty { "谁都不能" }) {
                        navigate("readers", "@default")
                    }
                }
            }
            item {
                CardBox("你自己运行的 Agent") {
                    library.readers.filter { it.kind == "agent" }
                        .forEach { reader ->
                            Row(Modifier.fillMaxWidth()) {
                                Text(reader.id, Modifier.weight(1f))
                                TextButton(
                                    onClick = {
                                        scope.launch {
                                            model.perform("没能保存 Agent") {
                                                model.backend.updateKnowledge(
                                                    buildJsonObject {
                                                        put(
                                                            "agents",
                                                            Wire.encodeToJsonElement(
                                                                library.readers.filter {
                                                                    it.kind == "agent" && it.id != reader.id
                                                                }.map { it.id }
                                                            )
                                                        )
                                                    }
                                                )
                                                reload()
                                            }
                                        }
                                    }
                                ) { Text("移除") }
                            }
                        }
                    OutlinedTextField(
                        agentName,
                        { agentName = it },
                        label = { Text("Agent 名称") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    TextButton(
                        onClick = {
                            scope.launch {
                                model.perform("没能保存 Agent") {
                                    model.backend.updateKnowledge(
                                        buildJsonObject {
                                            put(
                                                "agents",
                                                Wire.encodeToJsonElement(
                                                    (library.readers.filter { it.kind == "agent" }.map { it.id } +
                                                        agentName.trim()).distinct()
                                                )
                                            )
                                        }
                                    )
                                    agentName = ""
                                    reload()
                                }
                            }
                        },
                        enabled = agentName.isNotBlank()
                    ) { Text("添加 Agent") }
                }
            }
        }
    }
    if (naming) AlertDialog(
        onDismissRequest = { naming = false },
        title = { Text("新建主题") },
        text = {
            Column {
                OutlinedTextField(newName, { newName = it }, placeholder = { Text("比如：数据结构") })
                Muted("在后端的知识库里建一个文件夹，先按「新主题默认谁能读」设置，之后可以改。")
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    scope.launch {
                        if (model.perform("没能新建主题") { model.backend.createTopic(newName.trim()) }) {
                            naming = false
                            reload()
                        }
                    }
                },
                enabled = Knowledge.validTopic(newName)
            ) { Text("新建") }
        },
        dismissButton = { TextButton(onClick = { naming = false }) { Text("取消") } }
    )
}

@Composable
private fun AreaCard(area: KnowledgeArea, navigate: (String, String) -> Unit) {
    CardBox {
        SettingRow(area.name, if (area.pending > 0) "${area.pending} 份待整理" else null) { navigate("area", area.path) }
        Muted(area.summary)
        Muted(area.readers.joinToString("、").ifEmpty { "谁都不能读" })
    }
}

@Composable
private fun KnowledgeAreaPage(
    model: AppModel,
    area: KnowledgeArea,
    files: KnowledgeFiles?,
    navigate: (String, String) -> Unit,
    reload: () -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var uploading by remember { mutableStateOf(false) }
    fun upload(uri: android.net.Uri) {
        scope.launch {
            uploading = true
            model.perform("没能添加资料") {
                val pair = withContext(Dispatchers.IO) {
                    val raw = context.contentResolver.query(
                        uri,
                        arrayOf(OpenableColumns.DISPLAY_NAME),
                        null,
                        null,
                        null
                    )
                        ?.use { cursor ->
                            if (cursor.moveToFirst())
                                cursor.getString(0)
                            else
                                null
                        } ?: "照片-${System.currentTimeMillis()}.jpg"
                    val name = raw.replace(Regex("[/\\\\:]"), "_").take(180)
                    val type = Knowledge.contentType(name) ?: if (area.readers.isEmpty()) when (name.substringAfterLast(
                        '.'
                    ).lowercase()) {
                        "csv" -> "text/csv"
                        "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        "zip" -> "application/zip"
                        else -> null
                    } else null
                    if (type == null) throw APIError("invalid_request")
                    val data = context.contentResolver.openInputStream(uri)
                        ?.use { input ->
                            val output = java.io.ByteArrayOutputStream()
                            val buffer = ByteArray(8192)
                            var size = 0
                            while (true) {
                                val n = input.read(buffer)
                                if (n < 0) break
                                size += n
                                if (size > 50 * 1024 * 1024) throw APIError("invalid_request")
                                output.write(buffer, 0, n)
                            }
                            output.toByteArray()
                        } ?: throw APIError("invalid_request")
                    Triple(name, type, data)
                }
                model.backend.uploadKnowledge("${area.path}/${pair.first}", pair.third, pair.second)
                reload()
            }
            uploading = false
        }
    }
    val importer = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(::upload)
    }
    val photo = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        uri?.let(::upload)
    }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            CardBox("谁能读") {
                Muted(area.readers.joinToString("、").ifEmpty { "谁都不能读" })
                SettingRow("修改谁能读") { navigate("readers", area.path) }
                if (area.isTopic && area.readers.isEmpty()) Muted("只有你能看。整理任务不会处理这个主题，里面的东西不会发给任何模型。")
            }
        }
        if (area.isTopic) {
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(
                        onClick = {
                            importer.launch(
                                (Knowledge.uploadTypes.values.toList() + if (area.readers.isEmpty()) listOf(
                                    "text/csv",
                                    "application/zip",
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                ) else emptyList()).distinct()
                                    .toTypedArray()
                            )
                        },
                        enabled = !uploading
                    ) { Text(if (uploading) "添加中…" else "从文件添加") }
                    TextButton(
                        onClick = {
                            photo.launch(
                                androidx.activity.result.PickVisualMediaRequest(
                                    ActivityResultContracts.PickVisualMedia.ImageOnly
                                )
                            )
                        },
                        enabled = !uploading
                    ) { Text("从照片添加") }
                }
            }
            item { Serif("整理好的笔记", 18) }
            if (files?.entries?.none { it.kind == "note" } == true) item {
                Muted(if (area.readers.isEmpty()) "这个主题不整理。" else "还没有。放进资料以后，晚上会整理出来。")
            }
            items(files?.entries.orEmpty().filter { it.kind == "note" }, key = { it.path }) { entry ->
                EntryCard(entry, model) { navigate("file", entry.path) }
            }
        }
        item { Serif(if (area.isTopic) "资料" else "文件", 18) }
        if (files == null) item { CircularProgressIndicator() }
        if (files?.entries?.none { it.kind != "note" } == true) item {
            Muted(if (area.isTopic) "还没有资料。用上面的添加按钮，或在后端放进「知识库/${area.name}」。" else "空的。")
        }
        items(files?.entries.orEmpty().filter { !area.isTopic || it.kind != "note" }, key = { it.path }) { entry ->
            EntryCard(entry, model) { navigate("file", entry.path) }
        }
    }
}

@Composable
private fun EntryCard(entry: KnowledgeEntry, model: AppModel, open: () -> Unit) {
    CardBox {
        SettingRow(entry.title, entry.modifiedAt?.let { format(instant(it), "M月d日 HH:mm", model.zone) }, open)
        Knowledge.statusText(entry.status)?.let { Muted(it) }
    }
}

@Composable
private fun KnowledgeFilePage(model: AppModel, file: KnowledgeFile, navigate: (String, String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var opening by remember { mutableStateOf(false) }
    var preview by remember(file.path) { mutableStateOf<File?>(null) }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item {
            Serif(file.title, 24)
            Muted(
                listOfNotNull(file.area, file.organizedBy?.let { Names.harness(it) }, Knowledge.statusText(file.status))
                    .joinToString(" · ")
            )
        }
        if (file.hasOriginal)
            item {
                TextButton(
                    onClick = {
                        opening = true
                        scope.launch {
                            model.perform("没能打开原件") {
                                val original = withContext(Dispatchers.IO) {
                                    val directory = File(context.cacheDir, "originals").apply { mkdirs() }
                                    directory.listFiles()?.filter {
                                        System.currentTimeMillis() - it.lastModified() > 86400000
                                    }?.forEach { it.delete() }
                                    val target = File(
                                        directory,
                                        "${UUID.randomUUID()}-${file.name.replace(Regex("[^\\p{L}\\p{N}._-]"),"_")}"
                                    )
                                    try {
                                        target.outputStream().use { model.backend.copyOriginal(file.path, it) }
                                        target
                                    } catch (e: Exception) {
                                        target.delete()
                                        throw e
                                    }
                                }
                                if (file.format == "pdf" || file.format == "image") {
                                    preview = original
                                } else {
                                    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", original)
                                    val mime =
                                        if (file.format == "image")
                                            Knowledge.contentType(file.name) ?: "image/*"
                                        else
                                            Knowledge.contentType(file.name) ?: "application/octet-stream"
                                    context.startActivity(
                                        Intent.createChooser(
                                            Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime).addFlags(
                                                Intent.FLAG_GRANT_READ_URI_PERMISSION
                                            ),
                                            "打开原件"
                                        )
                                    )
                                }
                            }
                            opening = false
                        }
                    },
                    enabled = !opening
                ) { Text(if (opening) "正在打开…" else "打开原件") }
            }
        item { CardBox("谁能读") { Muted(file.readers.joinToString("、").ifEmpty { "谁都不能读" }) } }
        if (file.extracted) item {
            Muted("下面是后端从${if(file.format=="image") "图片" else if(file.format=="pdf") " PDF " else "文件"}里读出的文字。")
        }
        item { MarkdownText(file.displayText) }
        if (file.truncated) item { Muted("文字太长，只显示了开头。打开原件看全部。") }
        if (file.sources.isNotEmpty()) item { Serif("来源", 18) }
        items(file.sources, key = { it.path }) { ref ->
            SettingRow(Knowledge.fileName(ref.path), ref.label) { navigate("file", ref.path) }
        }
        if (file.notes.isNotEmpty()) item { Serif("整理好的笔记", 18) }
        items(file.notes) { path -> SettingRow(Knowledge.fileName(path)) { navigate("file", path) } }
    }
    preview?.let { original ->
        OriginalPreview(original, pdf = file.format == "pdf") { preview = null }
    }
}

@Composable
private fun ReaderPicker(
    readers: List<KnowledgeReader>,
    initial: List<String>,
    locked: List<String>,
    note: String,
    model: AppModel,
    save: suspend (List<String>) -> List<String>
) {
    val selection = remember { ReaderSelection(initial) }
    var chosen by remember { mutableStateOf(initial) }
    val scope = rememberCoroutineScope()
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        listOf("bot" to "Bot", "agent" to "你自己运行的 Agent")
            .forEach { (kind, title) ->
                item { Serif(title, 18) }
                items(readers.filter { it.kind == kind }, key = { it.id }) { reader ->
                    CardBox {
                        ToggleRow(reader.id, reader.id in chosen || reader.id in locked, reader.id !in locked) { on ->
                            chosen = chosen.filterNot { it == reader.id } + if (on) listOf(reader.id) else emptyList()
                            scope.launch {
                                model.perform("没能保存") { selection.set(reader.id, on, save) }
                                chosen = selection.chosen
                            }
                        }
                        if (reader.id in locked) Muted("始终能读")
                    }
                }
            }
        item { Muted(note) }
    }
}
