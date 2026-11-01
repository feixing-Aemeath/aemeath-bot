package local.assistant.core

/** Shared by the tablet's sidebar and its compact drawer. No Android dependencies. */
object PadPresentation {
    const val CARD_MINIMUM_WIDTH = 270.0
    const val MEDIUM_WIDTH = 600.0
    const val PHONE_STORAGE_KEY = "chosen"
    const val TABLET_STORAGE_KEY = "chosen.tablet"

    fun storageKey(tablet: Boolean) = if (tablet) TABLET_STORAGE_KEY else PHONE_STORAGE_KEY

    fun selectedBot(storedId: String, bots: List<Bot>, settled: Boolean): Bot? =
        bots.find { it.id == storedId } ?: if (settled) bots.find { it.kind == BotKind.ASSISTANT } else null

    fun preview(messages: List<BotMessage>, fallback: String): String =
        messages.lastOrNull { it.author == "bot" && it.preview.isNotBlank() }?.preview ?: fallback

    fun sidebar(bots: List<Bot>, plugins: List<PluginView>): List<Bot> {
        val active = plugins.filter { it.state in setOf("enabled", "error") }.map { it.id }.toSet()
        return bots.filter { it.kind == BotKind.ASSISTANT } +
            bots.filter { it.kind == BotKind.CHARACTER }.sortedBy { bot ->
                bot.pluginIds.none { it in active }
            } + bots.filter { it.kind == BotKind.KNOWLEDGE } + bots.filter { it.kind == BotKind.SOURCE }
    }
}
