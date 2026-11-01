@file:OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)

package local.assistant.android.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.*
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*
import local.assistant.android.data.AppModel
import local.assistant.core.*
import java.time.*

@Composable
fun TodoRow(
    todo: Todo,
    onEdit: () -> Unit,
    onToggle: () -> Unit,
    onDelete: () -> Unit
) {
    var menu by remember { mutableStateOf(false) }
    val done = todo.status == "completed"
    val tint = LocalAccent.current
    Row(
        Modifier
            .fillMaxWidth()
            .combinedClickable(onClick = onEdit, onLongClick = { menu = true })
            .padding(vertical = 5.dp),
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Box(
            Modifier
                .size(36.dp)
                .clickable(onClick = onToggle)
                .semantics {
                    contentDescription = if (done) "恢复待办" else "完成待办"
                    stateDescription = if (done) "已完成" else "未完成"
                },
            contentAlignment = Alignment.Center
        ) {
            Box(
                Modifier
                    .size(21.dp)
                    .background(if (done) tint else androidx.compose.ui.graphics.Color.Transparent, CircleShape)
                    .border(1.5.dp, tint, CircleShape),
                contentAlignment = Alignment.Center
            ) { if (done) Text("✓", color = androidx.compose.ui.graphics.Color.White, fontSize = 13.sp) }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                todo.title,
                fontSize = 15.sp,
                textDecoration = if (done) TextDecoration.LineThrough else null,
                color = if (done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface
            )
            val due = DueText.describe(todo, LocalAnimationTime.current ?: Instant.now())
            Text(
                due,
                fontSize = 12.sp,
                color = if (due.startsWith("已逾期"))
                    MaterialTheme.colorScheme.error
                else
                    MaterialTheme.colorScheme.onSurfaceVariant
            )
            Muted(todo.sourceText())
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(
                text = { Text(if (done) "恢复待办" else "完成") },
                onClick = {
                    menu = false
                    onToggle()
                }
            )
            DropdownMenuItem(
                text = { Text("编辑") },
                onClick = {
                    menu = false
                    onEdit()
                }
            )
            DropdownMenuItem(
                text = { Text("删除", color = MaterialTheme.colorScheme.error) },
                onClick = {
                    menu = false
                    onDelete()
                }
            )
        }
    }
}

@Composable
fun TodayTodos(
    todos: List<Todo>,
    onEdit: (Todo) -> Unit,
    onToggle: (Todo) -> Unit,
    onDelete: (Todo) -> Unit
) {
    CardBox("今日待办") {
        Text(
            if (todos.none { it.status == "pending" }) "今天的待办都完成了" else "还有 ${todos.count { it.status=="pending" }} 件",
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium
        )
        if (todos.isEmpty()) Muted("今天没有待办。")
        todos.filter { it.status != "deleted" }
            .forEach { todo ->
                TodoRow(
                    todo,
                    { onEdit(todo) },
                    { onToggle(todo) },
                    { onDelete(todo) }
                )
            }
    }
}

@Composable
fun TodoEditor(
    todo: Todo,
    zone: ZoneId,
    onDismiss: () -> Unit,
    onSave: (JsonObject) -> Unit
) {
    var draft by remember(todo.id) { mutableStateOf(TodoDraft.from(todo, zone = zone)) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Serif("编辑待办", 20) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = draft.title,
                    onValueChange = { draft = draft.copy(title = it) },
                    label = { Text("标题") },
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = draft.notes,
                    onValueChange = { draft = draft.copy(notes = it) },
                    label = { Text("备注") },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 2
                )
                ToggleRow("截止日期", draft.hasDue) { draft = draft.copy(hasDue = it) }
                if (draft.hasDue) {
                    DateButton(
                        draft.due.toLocalDate(),
                        { draft = draft.copy(due = it.atTime(draft.due.toLocalTime())) }
                    )
                    ToggleRow("具体时间", draft.hasTime) { draft = draft.copy(hasTime = it) }
                    if (draft.hasTime) {
                        val context = LocalContext.current
                        TextButton(
                            onClick = {
                                TimePickerDialog(
                                    context,
                                    { _, hour, minute ->
                                        draft = draft.copy(
                                            due = draft.due.withHour(hour).withMinute(minute).withSecond(0).withNano(0)
                                        )
                                    },
                                    draft.due.hour,
                                    draft.due.minute,
                                    true
                                )
                                    .show()
                            }
                        ) { Text(draft.due.toLocalTime().toString()) }
                    }
                }
                HorizontalDivider()
                Muted(todo.sourceText())
                if (todo.sourceExcerpt.isNotEmpty()) MarkdownText(todo.sourceExcerpt)
                todo.sources.orEmpty().forEach { Muted("${Names.plugin(it.plugin)} · ${it.chatName} · ${it.sender}") }
                Muted(todo.syncText())
                todo.syncError?.let { Muted(RunText.error(it).orEmpty()) }
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(draft.patch(todo, zone)) }, enabled = draft.valid) { Text("保存") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } }
    )
}

@Composable
fun ToggleRow(
    title: String,
    checked: Boolean,
    enabled: Boolean = true,
    onChange: (Boolean) -> Unit
) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(title, Modifier.weight(1f), fontSize = 15.sp)
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
    }
}

@Composable
fun DateButton(day: LocalDate, onChange: (LocalDate) -> Unit, maxToday: Boolean = false) {
    val context = LocalContext.current
    TextButton(
        onClick = {
            DatePickerDialog(
                context,
                { _, y, m, d -> onChange(LocalDate.of(y, m + 1, d)) },
                day.year,
                day.monthValue - 1,
                day.dayOfMonth
            )
                .apply { if (maxToday) datePicker.maxDate = System.currentTimeMillis() }
                .show()
        }
    ) { Text("${day.year}年${day.monthValue}月${day.dayOfMonth}日") }
}

@Composable
fun CheckinCard(model: AppModel, online: Boolean) {
    val today = (LocalAnimationTime.current ?: Instant.now()).atZone(model.zone).toLocalDate()
    var dayText by rememberSaveable { mutableStateOf(today.toString()) }
    val day = LocalDate.parse(dayText)
    var apple by rememberSaveable { mutableStateOf("") }
    var perceived by rememberSaveable { mutableIntStateOf(0) }
    var sending by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf<String?>(null) }
    val parsed = ReadinessCheckin.parseApple(apple)
    val invalid = apple.isNotBlank() && parsed == null
    val entry = ReadinessCheckin(parsed, perceived.takeIf { it > 0 })
    val scope = rememberCoroutineScope()
    CardBox("今天的记录") {
        Muted("用来校准综合准备度：记满 3 周后，在后端运行 scripts.readiness_calibration，对比这两项和算出的分数，给出调整权重的建议。")
        DateButton(day, { dayText = it.toString() }, true)
        OutlinedTextField(
            apple,
            { apple = it },
            label = { Text("「健身」App 的准备指数") },
            placeholder = { Text("0–10") },
            isError = invalid,
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal
            ),
            modifier = Modifier.fillMaxWidth()
        )
        if (invalid) Text("请填 0 到 10 之间的数", fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
        Text("自己的感觉", fontSize = 14.sp)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            (0..5).forEach { n ->
                FilterChip(
                    selected = perceived == n,
                    onClick = { perceived = n },
                    label = { Text(if (n == 0) "不填" else "$n", fontSize = 12.sp) }
                )
            }
        }
        Muted("1 很累，3 一般，5 状态很好")
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    sending = true
                    scope.launch {
                        val ok = model.perform("没能保存这次记录") {
                            model.backend.uploadHealth(entry.batch(day, Instant.now(), model.zone))
                        }
                        if (ok) {
                            saved = "已保存 ${day.monthValue}月${day.dayOfMonth}日 的记录"
                            apple = ""
                            perceived = 0
                            runCatching { model.backend.runJob("plugin:health:refresh") }
                            kotlinx.coroutines.delay(3000)
                            model.loadBot("health")
                        }
                        sending = false
                    }
                },
                enabled = entry.valid && !invalid && !sending && online
            ) { Text("保存") }
            if (sending) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
        }
        saved?.let { Muted(it) }
    }
}
