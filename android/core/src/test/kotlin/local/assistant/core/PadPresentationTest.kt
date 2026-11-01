package local.assistant.core

import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

class PadPresentationTest {
    private fun bot(id: String, kind: BotKind) = Bot(id, id, id, kind, PluginMascot())
    private val assistant = bot("assistant", BotKind.ASSISTANT)
    private val shelf = bot("knowledge", BotKind.KNOWLEDGE)
    private val saved = bot("health", BotKind.CHARACTER)

    @Test
    fun savedSelectionWaitsForPlugins() {
        assertNull(PadPresentation.selectedBot("health", listOf(assistant, shelf), false))
        assertEquals(saved, PadPresentation.selectedBot("health", listOf(assistant, saved, shelf), false))
    }

    @Test
    fun missingSelectionFallsBackAfterLoading() {
        assertEquals(assistant, PadPresentation.selectedBot("removed", listOf(assistant, shelf), true))
        assertNull(PadPresentation.selectedBot("removed", emptyList(), true))
    }

    @Test
    fun knowledgeSelectionIsRestored() {
        assertEquals(shelf, PadPresentation.selectedBot("knowledge", listOf(assistant, shelf), true))
    }

    @Test
    fun phoneAndTabletHaveIndependentStorage() {
        assertEquals("chosen", PadPresentation.storageKey(false))
        assertNotEquals(PadPresentation.storageKey(false), PadPresentation.storageKey(true))
    }

    @Test
    fun sidebarKeepsBuiltInsSourcesAndDisabledRolesInTheirSections() {
        val off = bot("off", BotKind.CHARACTER)
        val source = bot("qq", BotKind.SOURCE)
        val plugins = listOf(
            PluginView(id = "off", name = "off", state = "disabled"),
            PluginView(id = "health", name = "health", state = "enabled")
        )
        assertEquals(
            listOf(assistant, saved, off, shelf, source),
            PadPresentation.sidebar(listOf(source, off, shelf, saved, assistant), plugins)
        )
    }

    @Test
    fun errorRolesStayWithActiveRolesAndMergedRolesUseAnyActivePlugin() {
        val joined = saved.copy(id = "off", joined = listOf("health"))
        val error = bot("error", BotKind.CHARACTER)
        val inactive = bot("inactive", BotKind.CHARACTER)
        val plugins = listOf(
            PluginView(id = "health", name = "health", state = "enabled"),
            PluginView(id = "error", name = "error", state = "error")
        )
        assertEquals(listOf(joined, error, inactive), PadPresentation.sidebar(listOf(inactive, joined, error), plugins))
    }

    @Test
    fun previewIgnoresMyMessagesAppNoticesAndBlankBotMessages() {
        val at = Instant.EPOCH
        val rows = listOf(
            BotMessage("1", "bot", at, "**今天有课**\n第二行"),
            BotMessage("2", "me", at, "我的问题"),
            BotMessage("3", "app", at, "失败"),
            BotMessage("4", "bot", at, "  ")
        )
        assertEquals("今天有课", PadPresentation.preview(rows, shelf.intro))
        assertEquals("晨间简报", PadPresentation.preview(rows + BotMessage("5", "bot", at, "正文", "晨间简报"), shelf.intro))
    }

    @Test
    fun previewFallsBackToDuties() {
        assertEquals(shelf.intro, PadPresentation.preview(emptyList(), shelf.intro))
    }
}
