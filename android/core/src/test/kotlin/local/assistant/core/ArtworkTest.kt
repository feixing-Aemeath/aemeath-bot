package local.assistant.core

import org.junit.Assert.*
import org.junit.Test

class ArtworkTest {
    @Test
    fun catalogReadsRolesCharacterNamesAndRequiredLayers() {
        val files = mapOf(
            "app.json" to """{"assistant":{"sticker":"aemeath","accent":"#E86A9A"}}""",
            "characters.json" to """{"aemeath":"助理Bot"}"""
        )
        val paths = files.keys + setOf(
            "aemeath.png",
            "placeholder.png",
            "aemeath/base.png",
            "aemeath/eyes_open.png",
            "aemeath/eyes_closed.png"
        )
        val catalog = StickerCatalog.fromPaths(paths, files::get)
        assertEquals(setOf("aemeath", "placeholder"), catalog.names)
        assertEquals(setOf("aemeath"), catalog.layered)
        assertEquals("助理Bot", catalog.characters["aemeath"])
        assertEquals("aemeath", catalog.roles["assistant"]?.sticker)
    }

    @Test
    fun missingLayersAndMalformedMetadataHaveSafeDefaults() {
        val catalog = StickerCatalog.fromPaths(setOf("a.png", "a/base.png", "a/eyes_open.png")) { "invalid" }
        assertTrue(catalog.layered.isEmpty())
        assertTrue(catalog.roles.isEmpty())
        assertEquals("placeholder", catalog.resolved("missing"))
        assertEquals("placeholder", catalog.resolved(null))
        assertEquals("a", catalog.resolved("a"))
    }

    @Test
    fun hexAccentsMatchThePhoneParser() {
        assertEquals(0xFF22B8CF.toInt(), parseAccent(" #22b8cf "))
        assertEquals(0xFF22B8CF.toInt(), parseAccent("22B8CF"))
        listOf(
            "#FFF",
            "#GG2222",
            "red",
            "#11223344",
            ""
        ).forEach { assertNull(parseAccent(it)) }
        assertNull(parseAccent(null))
    }
}
