package local.assistant.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*

@Serializable
data class Card(val type: String, val title: String, val data: JsonElement = JsonNull) {
    val id get() = "$type:$title"
    val first get() = data.obj()["first"].flag() ?: false
    val wide get() = data.obj()["wide"].flag() ?: false
    val tile get() = type == "stat" && !wide
    val symbol get() = data.obj()["symbol"].text()
    val listStyle
        get() = data.obj()["style"].text()?.takeIf { it in listOf("timeline", "rings") } ?: "rows"
    val content: CardContent get() = CardParser.parse(this)
}

data class Progress(val value: Double, val max: Double, val tone: String? = null) {
    val fraction get() = if (max > 0) (value / max).coerceIn(0.0, 1.0) else 0.0
}

data class ListItem(
    val title: String,
    val subtitle: String? = null,
    val detail: String? = null,
    val progress: Progress? = null,
    val state: String? = null
)

data class Point(val x: String, val y: Double, val label: String? = null)

data class Series(val name: String, val points: List<Point>)

data class Target(val value: Double, val label: String?)

data class StatBar(val max: Double, val segments: List<Point>, val tone: String?)

data class CardTable(val columns: List<String>, val rows: List<List<String>>, val highlight: Int?)

data class Photo(val id: String, val caption: String?, val path: String?)

sealed interface CardContent {
    data class Items(val items: List<ListItem>) : CardContent

    data class Stat(
        val value: String,
        val unit: String?,
        val label: String?,
        val delta: String?,
        val trend: String?,
        val wide: Boolean,
        val bar: StatBar?
    ) : CardContent

    data class Chart(
        val kind: String,
        val unit: String?,
        val series: List<Series>,
        val value: String?,
        val label: String?,
        val target: Target?
    ) : CardContent

    data class Photos(val photos: List<Photo>) : CardContent

    data class Text(
        val text: String,
        val headline: String?,
        val table: CardTable?,
        val footnote: String?
    ) : CardContent

    data class Todos(val todos: List<Todo>) : CardContent

    data class Unsupported(val type: String, val reason: String) : CardContent
}

object CardParser {
    fun display(value: JsonElement?): String? {
        if (value is JsonNull || value !is JsonPrimitive) return null
        if (value.isString) return value.content
        value.booleanOrNull?.let { return if (it) "是" else "否" }
        val n = value.number() ?: return null
        return if (n % 1.0 == 0.0 && kotlin.math.abs(n) < 1e15) n.toLong().toString() else n.toString()
    }

    fun points(value: JsonElement?): List<Point>? = (value as? JsonArray)?.mapNotNull {
        val o = it.obj()
        val x = display(o["x"]) ?: return@mapNotNull null
        val y = o["y"].number() ?: return@mapNotNull null
        Point(x, y, o["label"].text())
    }

    fun table(value: JsonElement?): CardTable? {
        val o = value as? JsonObject ?: return null
        val columns = (o["columns"] as? JsonArray)?.mapNotNull(::display)?.takeIf { it.isNotEmpty() } ?: return null
        val rows =
            (o["rows"] as? JsonArray)?.map { it.array().mapNotNull(::display) }?.takeIf { it.isNotEmpty() }
                ?: return null
        if (rows.any { it.size != columns.size }) return null
        return CardTable(columns, rows, o["highlight"].number()?.toInt()?.takeIf { it in 1 until columns.size })
    }

    fun parse(card: Card): CardContent {
        val d = card.data.obj()
        fun broken(reason: String) = CardContent.Unsupported(card.type, reason)
        return when (card.type) {
            "text" -> d["text"].text()?.let {
                CardContent.Text(
                    it,
                    d["headline"].text(),
                    table(d["table"]),
                    d["footnote"].text()
                )
            } ?: broken("missing text")
            "stat" -> {
                val counters = d.filterValues { it.number() != null }
                val fallback = counters.entries.singleOrNull()
                val value = display(d["value"]) ?: fallback?.value?.let(::display) ?: return broken("missing value")
                val b = d["bar"].obj()
                CardContent.Stat(
                    value,
                    d["unit"].text(),
                    d["label"].text() ?: fallback?.key?.takeIf { d["value"] == null },
                    display(d["delta"]),
                    d["trend"].text()?.takeIf { it in listOf("up", "down", "flat") },
                    card.wide,
                    b["max"].number()?.let { StatBar(it, points(b["segments"]) ?: emptyList(), b["tone"].text()) }
                )
            }
            "list" -> {
                val a = d["items"] as? JsonArray ?: return broken("missing items")
                CardContent.Items(
                    a.mapNotNull { item ->
                        item.text()?.let { return@mapNotNull ListItem(it) }
                        val o = item.obj()
                        val p = o["progress"].obj()
                        val title = display(o["title"]) ?: return@mapNotNull null
                        val progress = p["value"].number()?.let { v ->
                            p["max"].number()?.let { Progress(v, it, p["tone"].text()) }
                        }
                        ListItem(
                            title,
                            display(o["subtitle"]),
                            display(o["detail"]),
                            progress,
                            o["state"].text()?.takeIf { it in listOf("done", "now") }
                        )
                    }
                )
            }
            "chart" -> {
                val series = points(d["points"])?.let { listOf(Series(d["name"].text() ?: "", it)) }
                ?: d["series"].array().mapNotNull { s ->
                    points(s.obj()["points"])?.let { Series(s.obj()["name"].text() ?: "", it) }
                }
                if (series.isEmpty()) return broken("missing points")
                val target = d["target"].obj()
                CardContent.Chart(
                    d["kind"].text()?.takeIf { it in listOf("line", "bar", "donut") } ?: "line",
                    d["unit"].text(),
                    series,
                    display(d["value"]),
                    d["label"].text(),
                    target["value"].number()?.let { Target(it, target["label"].text()) }
                )
            }
            "photo_grid" -> {
                val a = d["photos"] as? JsonArray ?: return broken("missing photos")
                CardContent.Photos(
                    a.mapIndexedNotNull { i, p ->
                        (p as? JsonObject)?.let {
                            Photo(it["id"].text() ?: "$i", it["caption"].text(), it["path"].text())
                        }
                    }
                )
            }
            "todo_list" -> try {
                CardContent.Todos(
                    Wire.decodeFromJsonElement<List<Todo>>(d["todos"] ?: return broken("missing or invalid todos"))
                )
            } catch (_: Exception) {
                broken("missing or invalid todos")
            }
            else -> broken("unknown card type")
        }
    }
}
