package local.assistant.core

import org.junit.Test
import org.junit.Assert.*

class CardsTest {
    private fun card(type: String, data: String) =
        Wire.decodeFromString<Card>("""{"type":"$type","title":"x","data":$data}""")

    @Test
    fun textHeadline() {
        val c = card("text", """{"text":"睡得不错","headline":"10月15日"}""").content as CardContent.Text
        assertEquals("睡得不错", c.text)
        assertEquals("10月15日", c.headline)
    }

    @Test
    fun numberStatDisplay() {
        val c = card("stat", """{"value":6240,"unit":"步","delta":-312.5,"trend":"down"}""").content as CardContent.Stat
        assertEquals("6240", c.value)
        assertEquals("-312.5", c.delta)
        assertEquals("down", c.trend)
    }

    @Test
    fun counterFallback() {
        val c = card("stat", """{"count":1}""").content as CardContent.Stat
        assertEquals("1", c.value)
        assertEquals("count", c.label)
    }

    @Test
    fun multipleCountersAreUnsupported() {
        assertTrue(card("stat", """{"a":1,"b":2}""").content is CardContent.Unsupported)
    }

    @Test
    fun booleanDisplay() {
        assertEquals("是", CardParser.display(kotlinx.serialization.json.JsonPrimitive(true)))
        assertEquals("否", CardParser.display(kotlinx.serialization.json.JsonPrimitive(false)))
    }

    @Test
    fun wideStatAndSegmentBar() {
        val c = card(
            "stat",
            """{"first":true,"wide":true,"value":"-105","bar":{"max":2000,"tone":"alert","segments":[{"x":"吃饭","y":344.5}]}}"""
        )
        assertTrue(c.first)
        assertFalse(c.tile)
        val s = c.content as CardContent.Stat
        assertEquals("alert", s.bar?.tone)
        assertEquals(344.5, s.bar!!.segments[0].y, 0.0)
    }

    @Test
    fun barWithoutMaxIsAbsent() {
        val c = card("stat", """{"value":1,"bar":{"segments":[]}}""")
        assertTrue(c.tile)
        assertNull((c.content as CardContent.Stat).bar)
    }

    @Test
    fun stringAndObjectListItems() {
        val c =
            card(
                "list",
                """{"items":["通知",{"title":"讲座","subtitle":"周三"},{"no_title":true}]}"""
            ).content as CardContent.Items
        assertEquals(listOf("通知", "讲座"), c.items.map { it.title })
    }

    @Test
    fun progressClamps() {
        assertEquals(0.0, Progress(1.0, 0.0).fraction, 0.0)
        assertEquals(1.0, Progress(20.0, 10.0).fraction, 0.0)
        assertEquals(0.0, Progress(-1.0, 10.0).fraction, 0.0)
    }

    @Test
    fun progressAndStates() {
        val c = card(
            "list",
            """{"style":"timeline","items":[{"title":"高数","state":"now","progress":{"value":3,"max":4}},{"title":"英语","state":"future"}]}"""
        )
        assertEquals("timeline", c.listStyle)
        val list = c.content as CardContent.Items
        assertEquals(.75, list.items[0].progress!!.fraction, 0.0)
        assertNull(list.items[1].state)
    }

    @Test
    fun unknownListStyleUsesRows() {
        assertEquals("rows", card("list", """{"style":"future","items":[]}""").listStyle)
    }

    @Test
    fun weatherSymbolPreserved() {
        assertEquals("cloud.sun.fill", card("stat", """{"value":21,"symbol":"cloud.sun.fill"}""").symbol)
    }

    @Test
    fun timetableTableHighlight() {
        val c = card(
            "text",
            """{"text":"t","footnote":"今天读到这周","table":{"columns":["","周一","周二"],"rows":[["08:00","","英语"]],"highlight":2}}"""
        )
            .content as CardContent.Text
        assertEquals(2, c.table?.highlight)
        assertEquals("今天读到这周", c.footnote)
    }

    @Test
    fun shortTableRowFallsBackToText() {
        assertNull(
            (card(
                "text",
                """{"text":"t","table":{"columns":["","周一"],"rows":[["08:00"]]}}"""
            ).content as CardContent.Text).table
        )
    }

    @Test
    fun invalidHighlightIgnored() {
        assertNull(
            (card(
                "text",
                """{"text":"t","table":{"columns":["","周一"],"rows":[["08:00",""]],"highlight":9}}"""
            ).content as CardContent.Text).table?.highlight
        )
    }

    @Test
    fun chartSingleSeriesTarget() {
        val c = card(
            "chart",
            """{"kind":"bar","points":[{"x":"9月","y":2260,"label":"2,260"}],"target":{"value":2000,"label":"生活费"}}"""
        )
            .content as CardContent.Chart
        assertEquals("bar", c.kind)
        assertEquals(2000.0, c.target!!.value, 0.0)
        assertEquals("2,260", c.series[0].points[0].label)
    }

    @Test
    fun chartMultipleSeriesNumericX() {
        val c = card(
            "chart",
            """{"series":[{"name":"深睡","points":[{"x":1,"y":80}]},{"name":"REM","points":[{"x":1,"y":95}]}]}"""
        )
            .content as CardContent.Chart
        assertEquals(listOf("深睡", "REM"), c.series.map { it.name })
        assertEquals("1", c.series[0].points[0].x)
        assertEquals("line", c.kind)
    }

    @Test
    fun donutTotalAndLabels() {
        val c = card(
            "chart",
            """{"kind":"donut","value":"17.0B","label":"Claude 与 GPT","points":[{"x":"Claude","y":15},{"x":"GPT","y":2}]}"""
        )
            .content as CardContent.Chart
        assertEquals("donut", c.kind)
        assertEquals("17.0B", c.value)
        assertEquals(2, c.series[0].points.size)
    }

    @Test
    fun photoPlaceholdersKeepLocalPathOnly() {
        val c = card(
            "photo_grid",
            """{"photos":[{"id":"p1","caption":"晚霞","path":"/local/a.heic"},{"caption":"无 ID"}]}"""
        ).content as CardContent.Photos
        assertEquals("1", c.photos[1].id)
        assertEquals("/local/a.heic", c.photos[0].path)
    }

    @Test
    fun todoCardUsesSameModel() {
        val c = card("todo_list", """{"todos":[${fixture("todo")}]}""").content as CardContent.Todos
        assertEquals("完成信息核对", c.todos[0].title)
    }

    @Test
    fun invalidTodoCardDoesNotBreakOtherCards() {
        assertTrue(card("todo_list", """{"todos":[{"id":1}]}""").content is CardContent.Unsupported)
    }

    @Test
    fun unknownTypeRoundTrips() {
        val c = card("map", """{"lat":1}""")
        assertEquals("map", c.type)
        assertEquals(c, Wire.decodeFromString<Card>(Wire.encodeToString(Card.serializer(), c)))
    }

    @Test
    fun missingFieldsDegradePerCard() {
        listOf(
            "text",
            "stat",
            "list",
            "chart",
            "photo_grid",
            "todo_list"
        )
            .forEach { assertTrue(card(it, "{}").content is CardContent.Unsupported) }
    }

    @Test
    fun realBackendHealthCards() {
        val cards = fromFixture<List<Card>>("healthCards")
        assertEquals(8, cards.size)
        assertTrue(cards.none { it.content is CardContent.Unsupported })
        assertEquals("4.5", (cards[1].content as CardContent.Stat).value)
        val chart = cards[3].content as CardContent.Chart
        assertEquals(
            listOf(
                "深睡",
                "核心",
                "REM",
                "清醒"
            ),
            chart.series.map { it.name }
        )
        assertTrue(chart.series.all { it.points.size == 7 })
    }

    @Test
    fun phonePairsNonAdjacentTilesWithoutChangingOtherOrder() {
        assertEquals(
            listOf(listOf(0, 2), listOf(1), listOf(3)),
            CardPacking.phoneRows(
                listOf(
                    true,
                    false,
                    true,
                    false
                )
            )
        )
    }

    @Test
    fun oddTileFillsOneRow() {
        assertEquals(listOf(listOf(0, 1), listOf(2)), CardPacking.phoneRows(listOf(true, true, true)))
        assertEquals(emptyList<List<Int>>(), CardPacking.phoneRows(emptyList()))
    }
}
