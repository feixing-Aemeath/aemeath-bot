package local.assistant.desktop.ui

import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.SubcomposeLayout
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import local.assistant.core.Card
import local.assistant.core.CardPacking
import local.assistant.desktop.AppStore
import local.assistant.desktop.DesktopLayout

@Composable
fun CardGrid(cards: List<Card>, store: AppStore, tint: Color) {
    if (cards.isEmpty()) return
    SubcomposeLayout(Modifier.fillMaxWidth()) { constraints ->
        val width = constraints.maxWidth
        val gap = DesktopLayout.cardSpacing.dp.roundToPx()
        val columns = DesktopLayout.columns(width.toDp().value.toDouble())
        val columnWidth = ((width - gap * (columns - 1)) / columns).coerceAtLeast(1)
        // Probe slots have no animation and are never placed or exposed to accessibility.
        val heights = cards.mapIndexed { index, card ->
            subcompose("probe-$index") {
                CompositionLocalProvider(LocalReducedMotion provides true) {
                    ServerCard(card, store, tint)
                }
            }.single().measure(Constraints.fixedWidth(columnWidth)).height
        }
        val rows = if (columns == 1) {
            CardPacking.phoneRows(cards.map { it.tile })
        } else DesktopLayout.rows(heights.map(Int::toDouble), columns)
        var y = 0
        val placements = rows.flatMap { row ->
            val cardWidth = ((width - gap * (row.size - 1)) / row.size).coerceAtLeast(1)
            val natural = if (cardWidth == columnWidth) row.map { heights[it] } else row.map { index ->
                subcompose("wide-probe-$index") {
                    CompositionLocalProvider(LocalReducedMotion provides true) {
                        ServerCard(cards[index], store, tint)
                    }
                }.single().measure(Constraints.fixedWidth(cardWidth)).height
            }
            val rowHeight = maxOf(natural.maxOrNull() ?: 0, if (columns == 1 && row.size > 1) (cardWidth * 0.8).toInt() else 0)
            val result = row.mapIndexed { position, index ->
                val placeable = subcompose("card-$index") {
                    ServerCard(cards[index], store, tint, Modifier.fillMaxHeight(), index)
                }.single().measure(Constraints.fixed(cardWidth, rowHeight))
                Triple(placeable, position * (cardWidth + gap), y)
            }
            y += rowHeight + gap
            result
        }
        layout(width, (y - gap).coerceAtLeast(0)) {
            placements.forEach { (placeable, x, top) -> placeable.placeRelative(x, top) }
        }
    }
}
