package local.assistant.android.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.*
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.*
import kotlinx.coroutines.launch
import local.assistant.android.data.*
import local.assistant.core.*

@Composable
fun BotSidebar(
    model: AppModel,
    state: AppState,
    selectedId: String?,
    onChoose: (String) -> Unit,
    onSettings: () -> Unit,
    modifier: Modifier = Modifier
) {
    val palette = LocalPalette.current
    val bots = PadPresentation.sidebar(model.bots(), state.plugins)
    Column(
        modifier
            .background(palette.sidebar)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 12.dp)
    ) {
        Serif("Aemeath Bot", 23, Modifier.padding(14.dp))
        LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            items(bots, key = { it.id }) { bot ->
                if (bot.kind == BotKind.KNOWLEDGE) {
                    HorizontalDivider(Modifier.padding(vertical = 12.dp), color = palette.hairline)
                }
                if (bot.kind == BotKind.SOURCE && bot.id == bots.firstOrNull { it.kind == BotKind.SOURCE }?.id) {
                    Muted("消息来源", Modifier.padding(horizontal = 10.dp, vertical = 8.dp))
                }
                val unread = model.unread(bot) > 0
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (selectedId == bot.id) palette.selection else Color.Transparent)
                        .clickable { onChoose(bot.id) }
                        .semantics { selected = selectedId == bot.id }
                        .padding(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Box {
                        BotAvatar(bot, Modifier.size(40.dp))
                        Box(
                            Modifier
                                .size(10.dp)
                                .align(Alignment.BottomEnd)
                                .background(stateColor(model.botState(bot)), CircleShape)
                                .border(2.dp, palette.sidebar, CircleShape)
                        )
                    }
                    Column(Modifier.weight(1f)) {
                        Text(
                            bot.name,
                            fontSize = 16.sp,
                            fontWeight = if (unread) FontWeight.Bold else FontWeight.Medium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        Text(
                            PadPresentation.preview(model.messages(bot), bot.intro),
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                    if (unread) Text("●", fontSize = 9.sp, color = accent(bot.look.accent))
                }
            }
        }
        HorizontalDivider(color = palette.hairline)
        Row(
            Modifier.padding(horizontal = 10.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Text("●", fontSize = 9.sp, color = if (state.online) Color(0xFF7CA58A) else Color.Gray)
            Muted(if (model.device.mock) "模拟数据" else if (state.online) "Mac 在线" else "连不上")
        }
        TextButton(onClick = onSettings, modifier = Modifier.fillMaxWidth()) { Text("⚙  设置") }
    }
}

@Composable
fun TabletBotPage(model: AppModel, state: AppState, onKnowledge: () -> Unit) {
    val bot = model.selected()
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier
                .fillMaxWidth()
                .statusBarsPadding()
                .height(52.dp)
                .padding(horizontal = 24.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(bot.name, Modifier.weight(1f), fontWeight = FontWeight.Medium)
            val scope = rememberCoroutineScope()
            TextButton(onClick = { scope.launch { model.refresh() } }) { Text("刷新") }
        }
        if (state.settled) key(bot.id) {
            BotPage(model, state, bot, onKnowledge, Modifier.weight(1f), wideLayout = true)
        }
    }
}

@Composable
fun TabletHero(model: AppModel, bot: Bot) {
    val tint = LocalAccent.current
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(18.dp))
            .background(Brush.linearGradient(listOf(tint.copy(alpha = .22f), tint.copy(alpha = .05f))))
            .padding(horizontal = 20.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        if (bot.kind == BotKind.SOURCE) BotAvatar(bot, Modifier.size(84.dp))
        else Mascot(bot.look.sticker, model.botState(bot), Modifier.size(136.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Serif(bot.name)
            Text(
                bot.tag,
                Modifier
                    .background(tint.copy(alpha = .16f), CircleShape)
                    .padding(horizontal = 10.dp, vertical = 3.dp),
                color = tint,
                fontSize = 13.sp
            )
            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("●", color = stateColor(model.botState(bot)), fontSize = 9.sp)
                Muted(model.statusLine(bot))
            }
        }
    }
}

/** Keep each card in a single composition so its edits and height stay in sync. */
@Composable
fun TabletCardGrid(contents: List<@Composable () -> Unit>) {
    if (contents.isEmpty()) return
    Layout(
        content = { contents.forEach { content -> Box(propagateMinConstraints = true) { content() } } },
        modifier = Modifier.fillMaxWidth()
    ) { measurables, constraints ->
        val gap = 12.dp.roundToPx()
        val columns = CardPacking.columns(
            constraints.maxWidth.toDouble(),
            PadPresentation.CARD_MINIMUM_WIDTH.dp.toPx().toDouble(),
            gap.toDouble()
        )
        val width = (constraints.maxWidth - gap * (columns - 1)) / columns
        val heights = measurables.map { it.maxIntrinsicHeight(width).toDouble() }
        val rows = CardPacking.rows(heights, columns)
        var y = 0
        val placed = rows.flatMap { row ->
            val rowWidth = (constraints.maxWidth - gap * (row.size - 1)) / row.size
            val rowHeight = row.maxOf { measurables[it].maxIntrinsicHeight(rowWidth) }
            val top = y
            y += rowHeight + gap
            row.mapIndexed { column, index ->
                val placeable = measurables[index].measure(Constraints.fixed(rowWidth, rowHeight))
                Triple(placeable, column * (rowWidth + gap), top)
            }
        }
        layout(constraints.maxWidth, (y - gap).coerceAtLeast(0)) {
            placed.forEach { (placeable, x, top) -> placeable.placeRelative(x, top) }
        }
    }
}
