package local.assistant.core

object CardPacking {
    fun columns(width: Double, minWidth: Double, spacing: Double): Int {
        if (!width.isFinite() || !minWidth.isFinite() || !spacing.isFinite() ||
            width <= 0 || minWidth <= 0 || spacing < 0
        ) return 1
        return ((width + spacing) / (minWidth + spacing)).toInt().coerceAtLeast(1)
    }

    const val SAME_SIZE = 0.75

    fun rows(heights: List<Double>, columns: Int): List<List<Int>> {
        val groups = mutableListOf<MutableList<Int>>()
        heights.indices.sortedWith(compareByDescending<Int> { heights[it] }.thenBy { it }).forEach { index ->
            val group = groups.lastOrNull()
            if (group != null && heights[index] >= heights[group.first()] * SAME_SIZE) {
                group += index
            } else {
                groups.add(mutableListOf(index))
            }
        }
        return groups.flatMap { it.sorted().chunked(columns.coerceAtLeast(1)) }
    }

    fun phoneRows(tiles: List<Boolean>): List<List<Int>> {
        val placed = mutableSetOf<Int>()
        return buildList {
            tiles.indices.forEach { i ->
                if (placed.add(i)) {
                    val partner =
                        if (tiles[i]) tiles.indices.firstOrNull { it > i && tiles[it] && it !in placed } else null
                    if (partner != null) {
                        placed += partner
                        add(listOf(i, partner))
                    } else add(listOf(i))
                }
            }
        }
    }
}
